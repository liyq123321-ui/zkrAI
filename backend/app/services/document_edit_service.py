"""Exact chat-driven PRD edits, previewed and committed as one document version."""
import base64
import binascii
import hashlib
import json
import os
import re
from copy import deepcopy

from app.ai.provider import BlockPlan, BulkEditRule, DocumentEditPlan, PlanNode
from app.domain.types import ErDiagram
from app.services.block_service import BlockError
from app.services.drawio_diagrams import diagram_anchor, diagram_review_projection
from app.services.document_bulk_edit import scan_bulk_matches
from app.services.prd_templates import validate_plan


_MERMAID_FENCE = re.compile(r"(?ms)^```mermaid[^\n]*\n.*?^```[ \t]*$")
_VISUAL_REQUEST = re.compile(r"mermaid|draw\.?io|ER\s*图|实体关系|架构图|流程图|状态图|时序图|关系图|数据流图|配图|图示|这张图|图中|画图|画一张|画个|图片|插画|插图", re.I)
_CROSS_REFERENCE = re.compile(r"同步|联动|一起|同时|保持一致|跨章节|对应修改|一并修改|关联章节|相关章节|连带")
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]+)\)")


def _image_payload(step) -> tuple[str, bytes]:
    if step.image_base64:
        try:
            raw = base64.b64decode(step.image_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise BlockError("生成图片编码无效", 422) from error
        mime = step.image_mime or "image/png"
    else:
        raw = step.image_svg.encode("utf-8")
        mime = "image/svg+xml"
        if re.search(rb"(?i)@import|url\s*\(|<\s*(?:image|use)\b|data:", raw):
            raise BlockError("生成配图包含外部资源或不安全内容", 422)
    if not raw:
        raise BlockError("Agent 未生成配图内容", 422)
    if len(raw) > 12 * 1024 * 1024:
        raise BlockError("生成配图超过 12 MB", 422)
    from app.services.document_asset_service import ASSET_MIME_TYPES, DocumentAssetService
    if mime not in ASSET_MIME_TYPES:
        raise BlockError("生成图片格式不受支持", 422)
    DocumentAssetService._image_dimensions(mime, raw)
    return mime, raw


def _image_edit(content: str, step, asset_id: str) -> str:
    alt = (step.image_alt or step.diagram_title or "产品使用示意图").replace("]", "").replace("\n", " ").strip()
    figure = f"![{alt}](asset://{asset_id})"
    if step.action == "image_replace":
        matches = list(_MARKDOWN_IMAGE.finditer(content))
        if step.target_text:
            matching = [match for match in matches if match.group(0) == step.target_text]
        else:
            matching = matches if len(matches) == 1 and step.image_index == 0 else []
        if len(matching) != 1:
            raise BlockError("待替换的图片定位不唯一，请指定图片引用或选中原图", 422)
        match = matching[0]
        if not step.image_alt:
            figure = f"![{match.group(1)}](asset://{asset_id})"
        return content[:match.start()] + figure + content[match.end():]
    figure = "\n\n" + figure + "\n"
    if step.target_text:
        if content.count(step.target_text) != 1:
            raise BlockError("配图插入位置不唯一，请选中目标段落", 422)
        at = content.index(step.target_text)
        if step.position == "after":
            at += len(step.target_text)
        elif step.position != "before":
            raise BlockError("配图只能插入到目标段落前后", 422)
        return content[:at] + figure + content[at:]
    return content.rstrip() + figure


def _mermaid_edit(content: str, step) -> str:
    source = step.diagram_source.strip()
    if (not source or "```" in source or "<script" in source.casefold()
            or re.search(r"(?im)^\s*click\s|%%\s*\{|javascript:", source)):
        raise BlockError("Mermaid 图源为空或包含不允许的指令", 422)
    if not re.match(r"(?i)^(?:flowchart|graph|sequenceDiagram|stateDiagram(?:-v2)?|erDiagram|classDiagram|journey|gantt|mindmap|timeline|pie|C4Context)\b", source):
        raise BlockError("Mermaid 图源缺少受支持的图类型", 422)
    figure = f"```mermaid\n{source}\n```"
    if step.action == "mermaid_replace":
        matches = list(_MERMAID_FENCE.finditer(content))
        if step.diagram_index >= len(matches):
            raise BlockError("未找到指定 Mermaid 图，请选中图源或说明图名", 422)
        match = matches[step.diagram_index]
        return content[:match.start()] + figure + content[match.end():]
    caption = f"\n*图：{step.diagram_title.strip()}*" if step.diagram_title.strip() else ""
    figure = "\n\n" + figure + caption + "\n"
    if step.target_text:
        if content.count(step.target_text) != 1:
            raise BlockError("配图插入位置不唯一，请选中目标段落", 422)
        index = content.index(step.target_text)
        if step.position == "after":
            index += len(step.target_text)
        elif step.position != "before":
            raise BlockError("配图只能插入到目标段落前后", 422)
        return content[:index] + figure + content[index:]
    return content.rstrip() + figure


def _er_upsert(block: dict, step) -> None:
    fragment = deepcopy(block.get("fragment") or {})
    diagrams = list(fragment.get("er_diagrams") or [])
    existing = next((item for item in diagrams if item.get("diagram_id") == step.diagram_id), None)
    if not step.diagram_id:
        if len(diagrams) != 1:
            raise BlockError("请指出要修改的 ER 图名称或图 ID", 422)
        existing = diagrams[0]
    diagram_id = existing["diagram_id"] if existing else step.diagram_id
    if not diagram_id or not step.drawio_xml:
        raise BlockError("ER 图需要稳定图 ID 和可编辑 draw.io XML", 422)
    try:
        diagram = ErDiagram.model_validate({
            "diagram_id": diagram_id,
            "title": step.diagram_title or (existing or {}).get("title") or "数据模型",
            "after_section": step.after_section or (existing or {}).get("after_section") or "core_objects",
            "drawio_xml": step.drawio_xml,
        })
    except ValueError as error:
        raise BlockError(f"ER 图源校验失败：{error}", 422) from error
    old_anchor = diagram_anchor(ErDiagram.model_validate(existing)) if existing else ""
    new_anchor = diagram_anchor(diagram)
    updated = diagram.model_dump(mode="json")
    fragment["er_diagrams"] = [updated if item.get("diagram_id") == diagram_id else item for item in diagrams]
    if existing is None:
        fragment["er_diagrams"].append(updated)
    content = block["content"]
    if old_anchor and old_anchor in content:
        content = content.replace(old_anchor, new_anchor)
        if existing and existing.get("title") != diagram.title:
            content = content.replace(f"[ER 图：{existing['title']}](#{new_anchor})",
                                      f"[ER 图：{diagram.title}](#{new_anchor})")
    elif f"#{new_anchor}" not in content:
        content = content.rstrip() + f"\n\n[ER 图：{diagram.title}](#{new_anchor})\n"
    block["content"] = content
    block["fragment"] = fragment


def _related_targets(source: list[dict], target_ids: list[str], instruction: str) -> list[str]:
    if not _VISUAL_REQUEST.search(instruction) and not _CROSS_REFERENCE.search(instruction):
        return target_ids
    cross = bool(_CROSS_REFERENCE.search(instruction))
    existing_edit = bool(re.search(r"修改|调整|替换|重画|更新|修正|微调|同步", instruction))
    selected = set(target_ids)
    if not cross and any(
        block["id"] in selected and (
            "```mermaid" in block["content"].casefold()
            or (block.get("fragment") or {}).get("er_diagrams")
            or bool(_MARKDOWN_IMAGE.search(block["content"]))
        ) for block in source
    ):
        return target_ids
    candidates = []
    for block in source:
        if block["id"] in selected:
            continue
        title = block["title"]
        has_er = bool((block.get("fragment") or {}).get("er_diagrams"))
        has_mermaid = "```mermaid" in block["content"].casefold()
        score = 0
        if title and title in instruction:
            score += 100
        if existing_edit and ((has_er and re.search(r"ER\s*图|实体关系|数据模型", instruction, re.I))
                              or (has_mermaid and _VISUAL_REQUEST.search(instruction))):
            score += 35
        if cross and re.search(r"架构|流程|接口|边界|数据|需求|功能|对象", title):
            score += 15
        if score:
            candidates.append((score, block["order"], block["id"]))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [*target_ids, *(item[2] for item in candidates[:max(0, 8 - len(target_ids))])]


def _cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith(r"\|"):
        value = value[:-1]
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", value)]


def _table_ranges(lines: list[str]) -> list[tuple[int, int]]:
    found = []
    fenced = False
    index = 0
    while index + 1 < len(lines):
        if lines[index].lstrip().startswith("```"):
            fenced = not fenced
            index += 1
            continue
        if fenced or "|" not in lines[index]:
            index += 1
            continue
        separator = _cells(lines[index + 1])
        if not separator or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            index += 1
            continue
        width = len(_cells(lines[index]))
        if width != len(separator):
            index += 1
            continue
        end = index + 2
        while end < len(lines) and "|" in lines[end] and len(_cells(lines[end])) == width:
            end += 1
        found.append((index, end))
        index = end
    return found


def _table_edit(content: str, step) -> str:
    lines = content.splitlines()
    ranges = _table_ranges(lines)
    if step.table_index >= len(ranges):
        raise BlockError("未找到指定表格，请在正文中选中表格或说明表头", 422)
    start, end = ranges[step.table_index]
    headers = _cells(lines[start])
    separator = _cells(lines[start + 1])
    rows = [_cells(line) for line in lines[start + 2:end]]
    action = step.action
    if action == "table_add_column":
        if not step.column or step.column in headers or len(step.values) not in (0, len(rows)):
            raise BlockError("新列名称重复，或新列数据行数不匹配", 422)
        headers.append(step.column)
        separator.append("---")
        for index, row in enumerate(rows):
            row.append(step.values[index] if step.values else "")
    else:
        if step.column not in headers and action in {"table_set", "table_delete_column", "table_sort"}:
            raise BlockError(f"表格没有「{step.column}」列", 422)
        column = headers.index(step.column) if step.column in headers else -1
        if action == "table_delete_column":
            if len(headers) <= 1:
                raise BlockError("不能删除表格的最后一列", 422)
            for row in [headers, separator, *rows]:
                row.pop(column)
        elif action == "table_add_row":
            if len(step.values) != len(headers):
                raise BlockError("新增行的单元格数量与表头不一致", 422)
            if step.position == "child_of":
                raise BlockError("表格行只能插入到指定行前后", 422)
            insert_at = min(step.row - (step.position == "before"), len(rows)) if step.row else len(rows)
            insert_at = max(0, insert_at)
            rows.insert(insert_at, list(step.values))
        elif action in {"table_set", "table_delete_row"}:
            if action == "table_set" and step.row == 0 and not step.row_match:
                if not step.value or step.value in headers:
                    raise BlockError("新表头为空或与现有列名重复", 422)
                headers[column] = step.value
                row_index = None
            elif step.row_match:
                matched = [index for index, row in enumerate(rows) if step.row_match in row or
                           any(step.row_match in cell for cell in row)]
                if len(matched) != 1:
                    raise BlockError("表格行定位不唯一，请补充行名", 422)
                row_index = matched[0]
                if step.row and row_index != step.row - 1:
                    raise BlockError("表格行号与行内容不一致", 422)
            elif 1 <= step.row <= len(rows):
                row_index = step.row - 1
            else:
                raise BlockError("请指定有效的数据行号或唯一行内容", 422)
            if action == "table_set" and row_index is not None:
                rows[row_index][column] = step.value
            elif action == "table_delete_row":
                rows.pop(row_index)
        elif action == "table_sort":
            def key(row):
                value = row[column].strip()
                number = re.fullmatch(r"[¥$]?\s*(-?\d+(?:\.\d+)?)\s*%?", value)
                return (0, float(number.group(1))) if number else (1, value.casefold())
            rows.sort(key=key, reverse=step.descending)
        else:
            raise BlockError("不支持的表格操作", 422)
    def render(row):
        return "| " + " | ".join(cell.replace("\r", " ").replace("\n", "<br>").replace("|", r"\|") for cell in row) + " |"
    lines[start:end] = [render(headers), render(separator), *(render(row) for row in rows)]
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def _reorder(blocks: dict[str, dict], step) -> None:
    source = blocks.get(step.block_id)
    destination = blocks.get(step.target_block_id)
    if not source or not destination or source is destination:
        raise BlockError("移动章节的起点或目标无效", 422)
    parent = step.target_block_id if step.position == "child_of" else destination["parent_id"]
    ancestor = parent
    while ancestor:
        if ancestor == source["id"]:
            raise BlockError("不能把章节移到自己的子章节内", 422)
        ancestor = blocks[ancestor]["parent_id"]
    siblings = {}
    for block in sorted(blocks.values(), key=lambda item: (item["order"], item["id"])):
        siblings.setdefault(block["parent_id"], []).append(block["id"])
    siblings[source["parent_id"]].remove(source["id"])
    target_siblings = siblings.setdefault(parent, [])
    if step.position == "child_of":
        target_siblings.append(source["id"])
    else:
        position = target_siblings.index(destination["id"])
        target_siblings.insert(position + (step.position == "after"), source["id"])
    source["parent_id"] = parent
    visited = set()
    order = []
    def walk(parent_id):
        for block_id in siblings.get(parent_id, []):
            if block_id in visited:
                raise BlockError("章节目录存在循环", 422)
            visited.add(block_id)
            order.append(block_id)
            walk(block_id)
    walk(None)
    if len(order) != len(blocks):
        raise BlockError("章节目录无法完整排序", 422)
    for index, block_id in enumerate(order):
        blocks[block_id]["order"] = index


class DocumentEditService:
    def __init__(self, blocks, provider, assets=None):
        self.blocks = blocks
        self.provider = provider
        self.assets = assets

    async def _prepare_image_step(self, step):
        if not os.getenv("IMG_GEN_URL"):
            _image_payload(step)
            return "已生成可编辑 SVG 配图；配置 IMG_GEN_URL 后可使用 generate_image 图片 Skill。"
        from app.mcp_gateway.protocol import McpGateway
        gateway = McpGateway.build()
        skill = next((item for item in gateway.config.tools if item.id == "generate_image"), None)
        if skill is None:
            raise BlockError("图片 Skill 尚未注册", 503)
        try:
            result = await gateway.registry.get(skill).call_tool(skill, {
                "prompt": step.image_prompt, "width": 1200, "height": 720,
            })
            image = next(item for item in result.content if item.get("type") == "image")
            step.image_base64 = image["data"]
            step.image_mime = image.get("mimeType") or "image/png"
            _image_payload(step)
            return "已调用 generate_image Skill 生成配图。"
        except Exception as error:
            step.image_base64 = ""
            step.image_mime = ""
            _image_payload(step)
            return f"图片服务暂不可用，已生成可编辑 SVG 配图：{type(error).__name__}。"

    def _simulate(self, source, plan, image_asset_ids=None):
        items = {block["id"]: deepcopy(block) for block in source}
        for step_index, step in enumerate(plan.steps):
            block = items.get(step.block_id)
            if not block:
                raise BlockError("指令引用了当前文档以外的章节", 422)
            if step.action == "move_block":
                _reorder(items, step)
            elif step.action == "replace_text":
                if not step.old_text or block["content"].count(step.old_text) != 1:
                    raise BlockError("原文定位不唯一或已变化，请重新选中目标段落", 409)
                block["content"] = block["content"].replace(step.old_text, step.new_text, 1)
            elif step.action == "bulk_replace":
                if not step.old_text or step.match_end != step.match_start + len(step.old_text) or \
                        block["content"][step.match_start:step.match_end] != step.old_text:
                    raise BlockError("批量命中位置已变化，请重新生成命中清单", 409)
                block["content"] = (block["content"][:step.match_start] + step.new_text
                                    + block["content"][step.match_end:])
            elif step.action == "move_text":
                content = block["content"]
                if not step.old_text or not step.target_text or step.old_text == step.target_text or \
                        content.count(step.old_text) != 1 or content.count(step.target_text) != 1 or \
                        step.target_text in step.old_text or step.position == "child_of":
                    raise BlockError("段落移动位置不唯一，请选中原文及目标段落", 422)
                remaining = content.replace(step.old_text, "", 1)
                anchor = remaining.find(step.target_text)
                if anchor < 0:
                    raise BlockError("目标段落位于被移动的原文内", 422)
                at = anchor + (len(step.target_text) if step.position == "after" else 0)
                block["content"] = remaining[:at] + step.old_text + remaining[at:]
            elif step.action in {"mermaid_insert", "mermaid_replace"}:
                block["content"] = _mermaid_edit(block["content"], step)
            elif step.action == "er_upsert":
                _er_upsert(block, step)
            elif step.action in {"image_insert", "image_replace"}:
                _, raw = _image_payload(step)
                asset_id = ((image_asset_ids or {}).get(step_index)
                            or "draft-" + hashlib.sha256(raw).hexdigest()[:12])
                block["content"] = _image_edit(block["content"], step, asset_id)
            else:
                block["content"] = _table_edit(block["content"], step)
            if len(block["content"]) > 100000:
                raise BlockError("修改后的章节超过长度限制", 422)
        nodes = [PlanNode(key=item["id"], title=item["title"], parent_key=item["parent_id"],
                          instruction=item["instruction"], dependencies=item["dependencies"])
                 for item in items.values()]
        try:
            validate_plan(BlockPlan(blocks=nodes))
        except ValueError as error:
            raise BlockError(str(error), 422) from error
        return items

    def _preview(self, original, changed):
        preview = []
        for block in original:
            after = changed[block["id"]]
            if not any(block.get(key) != after.get(key) for key in ("title", "content", "parent_id", "order", "fragment", "instruction", "dependencies")):
                continue
            old_diagrams = {item["diagram_id"]: item for item in (block.get("fragment") or {}).get("er_diagrams", [])}
            new_diagrams = {item["diagram_id"]: item for item in (after.get("fragment") or {}).get("er_diagrams", [])}
            diagram_previews = []
            for diagram_id, item in new_diagrams.items():
                previous = old_diagrams.get(diagram_id)
                if previous == item:
                    continue
                old_entities = set()
                if previous:
                    old_entities = {entity["label"] for page in diagram_review_projection(previous["drawio_xml"])["pages"]
                                    for entity in page["entities"]}
                new_entities = {entity["label"] for page in diagram_review_projection(item["drawio_xml"])["pages"]
                                for entity in page["entities"]}
                diagram_previews.append({**item, "anchor": diagram_anchor(ErDiagram.model_validate(item)),
                                         "added_entities": sorted(new_entities - old_entities),
                                         "removed_entities": sorted(old_entities - new_entities)})
            preview.append({"block_id": block["id"], "title": block["title"],
                            "before": block["content"], "after": after["content"],
                            "metadata_changes": [
                                {"field": key, "before": block.get(key), "after": after.get(key)}
                                for key in ("title", "instruction", "dependencies") if block.get(key) != after.get(key)
                            ],
                            "old_parent_id": block["parent_id"], "parent_id": after["parent_id"],
                            "old_order": block["order"], "order": after["order"],
                            "diagram_previews": diagram_previews})
        return preview

    async def plan(self, document_id, actor, instruction, target_ids, selected_text="", screenshot_anchor="", conversation_context="", object_target=None, knowledge=None, strict_targets=False):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            source = [dict((key, getattr(block, key)) for key in
                           ("id", "title", "parent_id", "order", "version", "content", "instruction", "dependencies", "fragment"))
                      for block in self.blocks.blocks(db, doc)]
            revision = doc.revision
            context_revision = doc.context_revision
            if knowledge is None:
                from app.services.document_decisions import knowledge_context
                knowledge_ids = target_ids if strict_targets or object_target else _related_targets(source, target_ids, instruction)
                knowledge = knowledge_context(db, doc, [b for b in source if b['id'] in knowledge_ids], consume=True)
        known = {block["id"] for block in source}
        if not target_ids or len(target_ids) > 8 or any(block_id not in known for block_id in target_ids):
            raise BlockError("请选择明确的目标章节，最多一次修改八个章节", 422)
        from app.services.document_object_target import ObjectTarget, resolve_target, constrain_steps
        target = ObjectTarget.model_validate(object_target) if object_target else None
        if target:
            located = resolve_target(source, target)
            if target.block_id not in target_ids: raise BlockError('对象不在指定章节内', 422)
            target_ids = [target.block_id]
        elif not strict_targets:
            target_ids = _related_targets(source, target_ids, instruction)
        context = {"instruction": instruction, "document_map": [
            {**{key: block[key] for key in ("id", "title", "parent_id", "order")},
             "has_mermaid": "```mermaid" in block["content"].casefold(),
             "er_diagram_ids": [item["diagram_id"] for item in (block.get("fragment") or {}).get("er_diagrams", [])]}
            for block in source], "focused_block_ids": target_ids, "selected_text": selected_text,
            "screenshot_anchor": screenshot_anchor,
            "conversation_context": conversation_context,
            'confirmed_knowledge': knowledge,
            'scope_instruction': '仅允许修改 focused_block_ids；未勾选章节、原型、PPT 不得改写。' if strict_targets else '',
            "blocks": [{**block, "fragment": {"er_diagrams": (block.get("fragment") or {}).get("er_diagrams", [])}}
                       for block in source if block["id"] in target_ids]}
        if not hasattr(self.provider, "plan_chat_edit"):
            raise BlockError("当前 Agent 尚不支持结构化文档指令", 503)
        if target:
            context['object_target'] = {**target.model_dump(), 'resolved': located,
                'constraint': '只更新指定对象；表格仅用 table_set 修改所选单元格/行/列，使用 resolved 行号；图沿用对象 ID，保留其他节点和关系。'}
        plan = DocumentEditPlan.model_validate(await self.provider.plan_chat_edit(context))
        if plan.decision_conflicts: raise BlockError('与已确认决定冲突，请先更新或撤销对应决定：' + '；'.join(plan.decision_conflicts), 422)
        if target: constrain_steps(source, target, plan)
        image_steps = [(index, step) for index, step in enumerate(plan.steps)
                       if step.action in {"image_insert", "image_replace"}]
        if len(image_steps) > 3:
            raise BlockError("一次最多生成三张普通配图", 422)
        for _, step in image_steps:
            note = await self._prepare_image_step(step)
            if note not in plan.impact_notes and len(plan.impact_notes) < 12:
                plan.impact_notes.append(note)
        for step in plan.steps:
            if step.action != "move_block" and step.block_id not in target_ids:
                raise BlockError("Agent 的修改超出了选定章节，请缩小指令范围", 422)
            if step.action == "move_block" and step.block_id not in target_ids:
                raise BlockError("Agent 移动了未指定的章节", 422)
            if step.action == "er_upsert" and re.search(r"修改|调整|替换|重画|更新|修正|微调", instruction) \
                    and not re.search(r"新增|新建|再画|另画|额外", instruction):
                original = next(block for block in source if block["id"] == step.block_id)
                existing_ids = {item["diagram_id"] for item in (original.get("fragment") or {}).get("er_diagrams", [])}
                if existing_ids and step.diagram_id and step.diagram_id not in existing_ids:
                    raise BlockError("修改已有 ER 图时必须沿用原图 ID，请重新生成修改方案", 422)
        if _CROSS_REFERENCE.search(instruction) and _VISUAL_REQUEST.search(instruction):
            changed_visual = any(step.action in {"mermaid_insert", "mermaid_replace", "er_upsert",
                                                 "image_insert", "image_replace"} for step in plan.steps)
            changed_prose = any(step.action in {"replace_text", "move_text", "table_set", "table_add_row",
                                                       "table_delete_row", "table_add_column", "table_delete_column"}
                                for step in plan.steps)
            if not changed_visual and not plan.impact_notes:
                raise BlockError("架构联动指令没有生成图源修改，也没有说明现有图为何无需修改", 422)
            if not changed_prose and not plan.impact_notes:
                raise BlockError("架构联动指令没有生成正文修改，也没有说明现有正文为何无需修改", 422)
            asks_both = bool(re.search(r"同步(?:更新|修改)|一起(?:更新|修改)|同时(?:更新|修改)|一并修改", instruction)
                            and re.search(r"图.{0,20}(?:正文|说明|表格|需求|接口)|(?:正文|说明|表格|需求|接口).{0,20}图", instruction))
            if asks_both and (not changed_visual or not changed_prose):
                raise BlockError("指令要求图文同步修改，但 Agent 的方案遗漏了图或正文", 422)
        changed = self._simulate(source, plan)
        preview = self._preview(source, changed)
        if not preview:
            raise BlockError("该指令没有产生正文或目录变化", 422)
        image_previews = []
        for index, step in image_steps:
            mime, raw = _image_payload(step)
            image_previews.append({"step_index": index, "alt": step.image_alt,
                                   "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"})
        return {"summary": plan.summary, "steps": [step.model_dump(mode="json") for step in plan.steps],
                "coupled": bool(_CROSS_REFERENCE.search(instruction)),
                "impact_notes": plan.impact_notes, "base_revision": revision, "context_revision": context_revision, "preview": preview,
                "image_previews": image_previews,
                "source_blocks": [{**b, 'content': b['content'] if b['id'] in {s.block_id for s in plan.steps} else '',
                                   'fragment': b.get('fragment') if b['id'] in {s.block_id for s in plan.steps} else None} for b in source],
                "object_target": target.model_dump() if target else None}

    async def plan_bulk(self, document_id, actor, instruction, knowledge=None):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            source = [dict((key, getattr(block, key)) for key in
                           ("id", "title", "parent_id", "order", "version", "content", "instruction", "dependencies", "fragment"))
                      for block in self.blocks.blocks(db, doc)]
            revision = doc.revision
            context_revision = doc.context_revision
            if knowledge is None:
                from app.services.document_decisions import knowledge_context
                knowledge = knowledge_context(db, doc, source, consume=True)
        if not hasattr(self.provider, "parse_bulk_edit"):
            raise BlockError("当前 Agent 尚不支持批量规则指令", 503)
        rule = BulkEditRule.model_validate(await self.provider.parse_bulk_edit({
            "instruction": instruction,
            'confirmed_knowledge': knowledge,
            "document_map": [{"id": block["id"], "title": block["title"]} for block in source],
        }))
        if rule.decision_conflicts: raise BlockError('与已确认决定冲突：' + '；'.join(rule.decision_conflicts), 422)
        if re.search(r"所有表格|全部表格|每个表格|表格(?:里|中|内)", instruction) and not re.search(r"(?:表格和正文|正文和表格)", instruction):
            rule.scope = "tables"
        if re.search(r"引用原文|原文摘录|原始引用|不要动引用|不改引用", instruction):
            rule.exclude_quotes = True
        unknown_exclusions = set(rule.excluded_block_titles) - {block["title"] for block in source}
        if unknown_exclusions:
            raise BlockError("批量规则中的排除章节不存在，请重新说明章节名称", 422)
        steps, excluded, excluded_count = scan_bulk_matches(source, rule)
        if not steps:
            raise BlockError("规则没有命中可修改的正文；请检查原文、范围或例外条件", 422)
        plan = DocumentEditPlan(summary=rule.summary, steps=steps)
        preview = self._preview(source, self._simulate(source, plan))
        by_id = {block["id"]: block for block in source}
        matches = []
        for index, step in enumerate(steps):
            content = by_id[step.block_id]["content"]
            line = content.count("\n", 0, step.match_start) + 1
            start = content.rfind("\n", 0, step.match_start) + 1
            end = content.find("\n", step.match_end)
            context = content[start:end if end >= 0 else len(content)].strip()[:240]
            matches.append({"step_index": index, "block_id": step.block_id,
                            "title": by_id[step.block_id]["title"], "line": line, "context": context})
        return {"summary": rule.summary, "rule": rule.model_dump(mode="json"),
                "steps": [step.model_dump(mode="json") for step in steps],
                "base_revision": revision, "context_revision": context_revision, "preview": preview, "bulk_matches": matches,
                "excluded_matches": excluded, "excluded_count": excluded_count, "source_blocks": source}

    def rebase(self, document_id, actor, steps):
        if any(step.action not in {"replace_text", "move_text"} for step in steps):
            raise BlockError("当前只支持文字替换和段落移动的精确重新定位；表格、图和目录请基于最新版本重新生成", 422)
        with self.blocks.factory() as db:
            doc = self.blocks.document(db, document_id, actor)
            source = [{key: getattr(block, key) for key in
                       ("id", "title", "parent_id", "order", "version", "content", "instruction", "dependencies", "fragment")}
                      for block in self.blocks.blocks(db, doc)]
            plan = DocumentEditPlan(summary="在最新正文上重新定位文字修改", steps=steps)
            preview = self._preview(source, self._simulate(source, plan))
            if not preview:
                raise BlockError("当前正文已包含这些结果，无需再次应用", 422)
            return {"summary": plan.summary, "base_revision": doc.revision, "steps": [s.model_dump(mode="json") for s in steps],
                    "preview": preview, "coupled": True, "impact_notes": ["已保留当前版本的其他内容，请核对重新定位后的文字差异。"]}

    def _restore_candidate(self, db, doc, version):
        from app.models import DocumentVersion
        saved = db.query(DocumentVersion).filter_by(document_id=doc.id, version=version).one_or_none()
        if saved is None:
            raise BlockError("历史版本不存在", 404)
        keys = ("id", "title", "parent_id", "order", "version", "content", "instruction", "dependencies", "fragment")
        source = [{key: getattr(block, key) for key in keys} for block in self.blocks.blocks(db, doc)]
        historical = {item["id"]: item for item in saved.snapshot["blocks"]}
        if set(historical) != {block["id"] for block in source}:
            raise BlockError("该历史版本的章节集合与当前文档不同，请先使用章节历史恢复需要的内容", 422)
        changed = {block["id"]: {**block, **{key: historical[block["id"]].get(key) for key in
                   ("title", "content", "parent_id", "order", "instruction", "dependencies", "fragment")}}
                   for block in source}
        try:
            validate_plan(BlockPlan(blocks=[PlanNode(key=b["id"], title=b["title"], parent_key=b["parent_id"],
                          instruction=b["instruction"], dependencies=b["dependencies"]) for b in changed.values()]))
        except ValueError as error:
            raise BlockError("历史目录无法恢复：" + str(error), 422) from error
        return source, changed, historical

    def restore_preview(self, document_id, actor, version=None):
        from app.models import DocumentVersion
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            self.blocks._backfill_document_versions(db, doc)
            db.flush()
            if version is None:
                latest = db.query(DocumentVersion).filter_by(document_id=doc.id).order_by(DocumentVersion.version.desc()).limit(2).all()
                if len(latest) < 2 or latest[0].source not in {"chat", "generate", "revise", "document_revision"}:
                    raise BlockError("最近一次保存不是可撤销的 Agent 修改，请从修订版本选择要恢复的内容", 409)
                version = latest[1].version
            source, changed, _ = self._restore_candidate(db, doc, version)
            preview = self._preview(source, changed)
            if not preview:
                raise BlockError("历史内容与当前文档相同，无需恢复", 422)
            return {"summary": f"将全文内容与目录恢复到 v{version}", "base_revision": doc.revision,
                    "restore_version": version, "steps": [], "coupled": True, "preview": preview,
                    "impact_notes": ["恢复会创建一个新版本；历史记录、文档背景和全局规则保留。恢复后等待手动审核。"]}

    def restore_apply(self, document_id, actor, expected_revision, version):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            if doc.revision != expected_revision:
                raise BlockError("文档在预览后已有修改，请重新预览恢复方案", 409)
            source, changed, historical = self._restore_candidate(db, doc, version)
            preview = self._preview(source, changed)
            if not preview:
                raise BlockError("没有可恢复的差异", 422)
            for item in preview:
                block = self.blocks.block(db, doc, item["block_id"])
                before = next(b for b in source if b["id"] == block.id)
                after = changed[block.id]
                values = {key: after[key] for key in ("title", "content", "parent_id", "order", "instruction", "dependencies", "fragment")
                          if before[key] != after[key]}
                self.blocks.save_in_transaction(db, doc, block, block.version, values, actor, "restore_document")
                if historical[block.id].get("fragment_version") != historical[block.id].get("version"):
                    block.fragment_version = None
                    from app.models import BlockVersion
                    from app.services.block_service import record
                    db.flush()
                    entry = db.query(BlockVersion).filter_by(block_id=block.id, version=block.version).one()
                    entry.snapshot = record(block)
        return self.blocks.snapshot(document_id, actor)

    def apply(self, document_id, actor, expected_revision, steps, alternative_id=None, job_id=None):
        plan = DocumentEditPlan(summary="应用对话修改", steps=steps)
        created_paths = []
        try:
            with self.blocks.write() as db:
                doc = self.blocks.document(db, document_id, actor)
                job = None
                if job_id:
                    from app.models import DocumentJob
                    job = db.get(DocumentJob, job_id)
                    if not job or job.document_id != doc.id: raise BlockError("候选任务不存在", 404)
                    fingerprint = hashlib.sha256(json.dumps([s.model_dump(mode="json") for s in steps], sort_keys=True).encode()).hexdigest()
                    if job.status == "applied":
                        if job.apply_fingerprint != fingerprint: raise BlockError("此任务已应用过另一组操作，请重新生成候选")
                        return self.blocks.snapshot(document_id, actor)
                    if job.status != "ready" or not job.result: raise BlockError("候选未完成或已取消，不能应用")
                    if job.result["base_revision"] != expected_revision: raise BlockError("候选版本不匹配")
                    if alternative_id != job.result.get("alternative_id"): raise BlockError("候选方案标识不匹配")
                    if any(s.model_dump(mode="json") not in job.result["steps"] for s in steps):
                        raise BlockError("操作与已保存候选不一致", 422)
                    selected_indices = [job.result['steps'].index(s.model_dump(mode='json')) for s in steps]
                    if selected_indices != sorted(set(selected_indices)): raise BlockError('请按原顺序应用所选操作，不能重复', 422)
                    from app.services.document_candidate_reuse import candidate_groups
                    groups = candidate_groups(job.result, job.result.get('source_blocks') or [])
                    if any(set(g) & set(selected_indices) and not set(g) <= set(selected_indices) for g in groups):
                        raise BlockError('有依赖的操作需要共同应用', 422)
                if doc.revision != expected_revision:
                    raise BlockError("PRD 已有新版本，请重新生成修改方案")
                if alternative_id:
                    from app.models import DocumentWorkspace
                    workspace = db.get(DocumentWorkspace, doc.id)
                    state = deepcopy(workspace.state) if workspace else {}
                    alt = next((a for a in state.get("alternatives", []) if a["id"] == alternative_id), None)
                    if not alt or alt["plan"]["base_revision"] != expected_revision:
                        raise BlockError("方案基线已变化，请重新生成")
                    stored_steps = alt["plan"]["steps"]
                    if any(step.model_dump(mode="json") not in stored_steps for step in steps):
                        raise BlockError("应用操作与已保存方案不一致", 422)
                    alt["status"] = "applied" if len(steps) == len(stored_steps) else "partially_applied"
                    alt["applied_steps"] = len(steps)
                    workspace.state, workspace.revision = state, workspace.revision + 1
                rows = self.blocks.blocks(db, doc)
                source = [dict((key, getattr(block, key)) for key in
                               ("id", "title", "parent_id", "order", "version", "content", "instruction", "dependencies", "fragment"))
                          for block in rows]
                self._simulate(source, plan)
                image_asset_ids = {}
                for index, step in enumerate(plan.steps):
                    if step.action not in {"image_insert", "image_replace"}:
                        continue
                    if self.assets is None:
                        raise BlockError("PRD 图片资产服务尚未启用", 503)
                    mime, raw = _image_payload(step)
                    from app.services.document_asset_service import ASSET_MIME_TYPES
                    asset = self.assets.store_asset(
                        db, document_id=document_id, block_id=step.block_id, actor=actor,
                        file_name=f"prd-illustration-{index + 1}{ASSET_MIME_TYPES[mime]}",
                        mime_type=mime, content=raw,
                        metadata={"source": "chat_generated", "prompt": step.image_prompt[:1000]},
                        created_paths=created_paths,
                    )
                    image_asset_ids[index] = asset.id
                changed = self._simulate(source, plan, image_asset_ids)
                preview = self._preview(source, changed)
                if not preview:
                    raise BlockError("修改方案没有产生变化", 422)
                by_id = {block.id: block for block in rows}
                for item in preview:
                    block = by_id[item["block_id"]]
                    after = changed[block.id]
                    values = {key: after[key] for key in ("content", "parent_id", "order", "fragment")
                              if getattr(block, key) != after[key]}
                    if "content" in values or "fragment" in values:
                        self.blocks.save_in_transaction(db, doc, block, block.version, values, actor, "chat")
                    else:
                        block.parent_id = after["parent_id"]
                        block.order = after["order"]
                        block.version += 1
                        if block.fragment_version is not None:
                            block.fragment_version = block.version
                        self.blocks.history(db, block, "chat", actor)
                        doc.revision += 1
                db.flush()
                if job is not None:
                    job.status, job.apply_fingerprint = "applied", fingerprint
                    if len(steps) == len(job.result['steps']) and job.result.get('answer_receipt'):
                        from app.models import DocumentWorkspace, DocumentVersion
                        from sqlalchemy import func
                        from app.services.document_workbench import now
                        workspace = db.get(DocumentWorkspace, doc.id)
                        state = deepcopy(workspace.state or {}) if workspace else {}
                        written = {p['block_id'] for p in preview}
                        version = (db.query(func.max(DocumentVersion.version)).filter_by(document_id=doc.id).scalar() or 0) + 1
                        for frozen in job.result['answer_receipt']:
                            question = next((q for q in state.get('questions', []) if q['id'] == frozen['id']), None)
                            targets = set(frozen.get('block_ids') or (job.payload.get('_answers') or {}).get('target_ids', []))
                            if question and question.get('status') not in ('ignored', 'archived') and question.get('answer') == frozen.get('answer') and question.get('text') == frozen.get('text') and question.get('block_ids') == frozen.get('block_ids') and targets and targets <= written:
                                receipt = {'version': version, 'revision': doc.revision, 'job_id': job.id, 'answer': frozen['answer'], 'block_ids': sorted(targets), 'at': now()}
                                question['status'], question['applied_receipt'] = 'applied', receipt
                                question['history'] = (question.get('history', []) + [{'status': 'applied', 'actor': actor, **receipt}])[-100:]
                        if workspace: workspace.state, workspace.revision = state, workspace.revision + 1
        except BaseException:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise
        return self.blocks.snapshot(document_id, actor)
