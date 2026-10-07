"""Evidence-backed PRD workbench. Reading aids never approve or mutate the PRD."""
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
import time
from app.models import DocumentWorkspace, AgentRun
from app.database.models import PrdPrototype, PrdPresentation, WorkItem, WorkspaceChatMessage
from app.services.block_service import BlockError, record, uid


IDS = re.compile(r"(?<![A-Za-z0-9_])(?:NFR|FR|AC)[-_]\d+(?![A-Za-z0-9_])", re.I)
UNKNOWN = re.compile(r"待确认|待补充|待决策|尚未明确|待定|\bTBD\b|\bTODO\b", re.I)


def question_key(text):
    return digest(re.sub(r'[\s\W_]+', '', text.casefold()))


@lru_cache(maxsize=16)
def similar_questions(rows):
    found = []
    for i, (aid, a) in enumerate(rows):
        for bid, b in rows[i+1:]:
            match = SequenceMatcher(None, a, b, autojunk=False)
            if match.quick_ratio() >= .82 and match.ratio() >= .82:
                found.append({'left': aid, 'right': bid})
                if len(found) >= 30: return found
    return found


@lru_cache(maxsize=16)
def content_index(serialized):
    """Only deterministic parsing is cached; no approval or stale review is reused."""
    blocks = json.loads(serialized)
    refs, kinds, covered = {}, {}, set()
    for b in blocks:
        refs[b['id']] = requirement_ids(b['content'])
        tags = []
        if re.search(r'(?m)^\s*\|.*\|', b['content']): tags.append('表格')
        if '```mermaid' in b['content'] or (b.get('fragment') or {}).get('er_diagrams'): tags.append('关系图')
        if '![' in b['content']: tags.append('图片')
        if re.search(r'验收|Given|When|Then|AC[-_]', b['content'], re.I): tags.append('验收')
        kinds[b['id']] = tags
        if b.get('fragment_version') == b['version'] and b.get('status') != 'outdated':
            covered.update(r.upper() for ac in (b.get('fragment') or {}).get('acceptance_criteria', []) for r in ac.get('requirement_ids', []))
    return refs, kinds, covered


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def now():
    return datetime.now(UTC).isoformat()


def requirement_ids(content):
    return set(x.upper() for x in IDS.findall(content or ""))


def affected_scope(blocks, baseline, context_changed=False):
    changed = {b["id"] for b in blocks if baseline.get(b["id"]) != b["version"]}
    if context_changed or set(baseline) - {b["id"] for b in blocks}:
        changed = {b["id"] for b in blocks}
    affected = set(changed)
    while True:
        refs = set().union(*(requirement_ids(b["content"]) for b in blocks if b["id"] in affected))
        upstream = {k for b in blocks if b["id"] in affected for k in b.get("dependencies", [])}
        expanded = affected | {b["id"] for b in blocks if b["id"] in upstream or set(b.get("dependencies", [])) & affected
                               or (requirement_ids(b["content"]) & refs)}
        if expanded == affected:
            return changed, affected
        affected = expanded


def segments(block):
    """Keep fenced diagrams/tables atomic, with exact source spans."""
    text = block["content"]
    pattern = r"(?m)^```[^\n]*\n[\s\S]*?^```[^\n]*(?:\n|$)|(?:^\|[^\n]*\n?)+|[^\n]+(?:\n(?!\s*\n|```|\|)[^\n]+)*"
    for match in re.finditer(pattern, text):
        quote = match.group().strip()
        if not quote:
            continue
        kind = "图源" if quote.startswith("```") else "表格" if quote.startswith("|") else "图片" if "![" in quote else "段落"
        yield {"block_id": block["id"], "title": block["title"], "quote": quote,
               "start": match.start() + len(match.group()) - len(match.group().lstrip()), "kind": kind}


def reconcile_anchors(previous, blocks):
    fresh = [entry for block in blocks for entry in segments(block)]
    old_live = [a for a in previous if not a.get("missing")]
    used = set()
    for entry in fresh:
        # Unique exact content survives reorder, rename and cross-chapter moves.
        candidates = [a for a in old_live if a["quote"] == entry["quote"] and a["id"] not in used]
        same = [a for a in candidates if a["block_id"] == entry["block_id"]]
        same_new = [a for a in fresh if a["quote"] == entry["quote"] and a["block_id"] == entry["block_id"]]
        unique_new = sum(a["quote"] == entry["quote"] for a in fresh) == 1
        chosen = same[0] if len(same) == len(same_new) == 1 else candidates[0] if len(candidates) == 1 and unique_new else None
        entry["id"] = chosen["id"] if chosen else uid("anchor")
        used.add(entry["id"])
    return fresh + [{**a, "missing": True} for a in previous if a["id"] not in used][-500:]


class DocumentWorkbench:
    def __init__(self, blocks, edits):
        self.blocks, self.edits = blocks, edits

    def _state(self, db, doc):
        row = db.get(DocumentWorkspace, doc.id)
        if row is None:
            row = DocumentWorkspace(document_id=doc.id, revision=1, state={})
            db.add(row)
        return row, deepcopy(row.state or {"questions": [], "alternatives": [], "issues": {}, "anchors": []})

    def inspect(self, document_id, actor):
        started = time.perf_counter()
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            blocks = [record(b) for b in self.blocks.blocks(db, doc)]
            row, state = self._state(db, doc)
            # Avoid reassigning duplicate anchors on repeated reads of unchanged text.
            fingerprint = digest(json.dumps([(b["id"], b["title"], b["content"]) for b in blocks], ensure_ascii=False))
            if state.get("anchor_fingerprint") != fingerprint:
                state["anchors"] = reconcile_anchors(state.get("anchors", []), blocks)
                state["anchor_fingerprint"] = fingerprint
                row.state, row.revision = state, row.revision + 1
            reviews = db.query(AgentRun).filter_by(document_id=doc.id, type="document_review", status="completed").order_by(AgentRun.created_at.desc()).all()
            full = next((r for r in reviews if (r.input_snapshot or {}).get("review_scope") != "affected"), None)
            changed, affected = affected_scope(blocks, full.dependency_versions if full else {}, bool(full and full.context_revision != doc.context_revision))
            versions = {b["id"]: b["version"] for b in blocks}
            issues, seen = [], set()
            for review in reviews:
                for item in (review.result or {}).get("issues", []):
                    key = digest(json.dumps([sorted(item.get("block_ids", [])), item["description"]], ensure_ascii=False))
                    if key in seen:
                        continue
                    seen.add(key)
                    targets = item.get("block_ids") or list(versions)
                    stale = review.context_revision != doc.context_revision or any(review.dependency_versions.get(k) != versions.get(k) for k in targets)
                    saved = state.get("issues", {}).get(key, {})
                    recheck = next((r for r in reviews if r.created_at > review.created_at
                                    and r.context_revision == doc.context_revision and r.dependency_versions == versions
                                    and ((r.input_snapshot or {}).get("review_scope") != "affected"
                                         or set(targets) <= set((r.input_snapshot or {}).get("affected_block_ids", [])))), None)
                    status = saved.get("status", "unresolved")
                    if status == 'confirmed' and (saved.get('context_revision', doc.context_revision) != doc.context_revision
                            or any(saved.get('block_versions', {}).get(k) != versions.get(k) for k in targets)):
                        status = "recheck"
                    if saved.get("run_id") != review.id and status == "confirmed":
                        status = "recheck"
                    issues.append({**item, "id": key, "run_id": review.id, "stale": stale,
                                   "status": status, "note": saved.get("note", ""),
                                   "history": saved.get("history", []), "source_revision": review.base_version,
                                   "recheck": {"run_id": recheck.id, "summary": "后续审核未报告同名问题，请结合正文人工确认"} if recheck else None,
                                   "changed_blocks": [k for k in targets if review.dependency_versions.get(k) != versions.get(k)]})
            nodes, edges, health = [], [], []
            index_input = json.dumps([{k: b.get(k) for k in ('id', 'content', 'fragment', 'version', 'fragment_version', 'status')} for b in blocks], ensure_ascii=False, sort_keys=True)
            hits = content_index.cache_info().hits
            refs, indexed_kinds, covered = content_index(index_input)
            index_hit = content_index.cache_info().hits > hits
            for b in blocks:
                kinds = indexed_kinds[b['id']]
                nodes.append({"id": b["id"], "title": b["title"], "kinds": kinds, "refs": sorted(refs[b["id"]]), "changed": b["id"] in changed, "affected": b["id"] in affected})
                for other in blocks:
                    shared = refs[b["id"]] & refs[other["id"]]
                    if other["id"] in b["dependencies"]:
                        edges.append({"from": other["id"], "to": b["id"], "evidence": "显式章节依赖"})
                    elif b["id"] < other["id"] and shared:
                        edges.append({"from": b["id"], "to": other["id"], "evidence": "共同引用 " + "、".join(sorted(shared))})
                reasons = []
                if not b["content"].strip(): reasons.append("正文为空")
                unresolved_marks = [line for line in b['content'].splitlines() if UNKNOWN.search(line) and question_key(line) not in {
                    key for q in state.get('questions', []) if q.get('status') in ('ignored', 'archived') for key in [question_key(q['text']), *q.get('aliases', [])]}]
                if unresolved_marks: reasons.append("原文包含待确认标记，请人工核对")
                if b["id"] in affected and kinds: reasons.append("涉及变动范围，图表/验收可能需要同步")
                if b.get("fragment_version") != b["version"]: reasons.append("结构化验收资料尚未与当前正文核对")
                fragment = b.get("fragment") or {}
                if b.get("fragment_version") == b["version"]:
                    missing = [r["requirement_id"] for key in ("functional_requirements", "non_functional_requirements") for r in fragment.get(key, []) if r.get("requirement_id") and r["requirement_id"].upper() not in covered]
                    if missing: reasons.append("当前有效结构化资料中未找到关联验收：" + "、".join(missing))
                reasons.extend(i["description"] + ("（旧审核，待复查）" if i["stale"] else "") for i in issues if b["id"] in i.get("block_ids", []) and i["status"] != "confirmed")
                if reasons: health.append({"block_id": b["id"], "title": b["title"], "reasons": list(dict.fromkeys(reasons))})
            artifacts = []
            for model, kind in ((PrdPrototype, "HTML 原型"), (PrdPresentation, "PPT")):
                for artifact in db.query(model.id, model.title, model.spec_version_id).filter(model.project_id == doc.project_id).all():
                    artifacts.append({"id": artifact.id, "kind": kind, "title": artifact.title or kind,
                                      "source_spec": artifact.spec_version_id,
                                      "stale": doc.submitted_revision != doc.revision or artifact.spec_version_id != doc.submitted_spec_id})
            tasks = []
            for task in db.query(WorkItem).filter_by(project_id=doc.project_id).all():
                task_refs = requirement_ids(json.dumps({k: getattr(task, k) for k in ("description", "spec", "inputs", "acceptance_criteria")}, ensure_ascii=False))
                links = [b["id"] for b in blocks if refs[b["id"]] & task_refs]
                if links: tasks.append({"id": task.id, "title": task.title, "block_ids": links, "refs": sorted(task_refs)})
            from app.services.document_decisions import policies
            visible_policies = [deepcopy(p) for _, p in policies(db, doc)]
            questions = state.get('questions', [])
            for q in questions: q.setdefault('status', 'answered' if q.get('answer', '').strip() else 'open')
            live_questions = [q for q in questions if q['status'] in ('open', 'answered')][:200]
            similar = similar_questions(tuple((q['id'], q['text'][:500]) for q in live_questions))
            return {"revision": row.revision, "document_revision": doc.revision, "state": state, "nodes": nodes,
                    'policies': visible_policies, 'merge_suggestions': similar,
                    'inspection_timing': {'seconds': round(time.perf_counter() - started, 4), 'index_cache_hit': index_hit},
                    "edges": edges, "health": health, "issues": issues, "artifacts": artifacts, "tasks": tasks,
                    "latest_review": {"summary": (reviews[0].result or {}).get("summary", ""),
                                      "scope": (reviews[0].input_snapshot or {}).get("review_scope", "full"),
                                      "stale": reviews[0].dependency_versions != versions or reviews[0].context_revision != doc.context_revision} if reviews else None,
                    "changed_ids": sorted(changed), "affected_ids": sorted(affected), "baseline_run": full.id if full else None}

    def update(self, document_id, actor, expected, action, payload):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            row, state = self._state(db, doc)
            if row.revision != expected:
                raise BlockError("工作台已更新，请刷新后重试；你的输入仍保留")
            known = {b.id for b in self.blocks.blocks(db, doc)}
            if action in ('question_state', 'question_merge'):
                items = state.setdefault('questions', [])
                item = next((q for q in items if q['id'] == payload.get('id')), None)
                if not item: raise BlockError('问题不存在', 404)
                if action == 'question_merge':
                    target = next((q for q in items if q['id'] == payload.get('merge_into') and q['id'] != item['id']), None)
                    if not target or target.get('status') not in (None, 'open', 'answered'): raise BlockError('请选择另一个待处理问题', 422)
                    if item.get('answer') and target.get('answer') != item['answer']: raise BlockError('两个问题的答案不同，请先人工统一，避免丢失答案', 422)
                    target['aliases'] = list(set(target.get('aliases', []) + [question_key(item['text']), *item.get('aliases', [])]))
                    target['block_ids'] = list(dict.fromkeys(target.get('block_ids', []) + item.get('block_ids', [])))
                    item['merged_into'] = target['id']; new_status = 'archived'
                else:
                    new_status = payload.get('question_status')
                    if new_status not in ('open', 'answered', 'ignored', 'archived'): raise BlockError('无效的问题状态', 422)
                    if new_status == 'answered' and not item.get('answer', '').strip(): raise BlockError('请先填写答案', 422)
                item['status'] = new_status
                item['history'] = (item.get('history', []) + [{'status': new_status, 'actor': actor, 'at': now()}])[-100:]
            elif action == "question":
                items = state.setdefault("questions", [])
                item = next((q for q in items if q["id"] == payload.get("id")), None)
                ids = payload.get("block_ids", [])
                if any(k not in known for k in ids): raise BlockError("关联章节不存在", 422)
                category = payload.get("category", "business")
                if category not in ("business", "information", "implementation"): raise BlockError("未知分类", 422)
                if not payload.get("text", "").strip(): raise BlockError("请填写问题", 422)
                values = {"text": payload["text"][:4000], "answer": payload.get("answer", "")[:6000], "category": category,
                          "block_ids": ids, "updated_at": now(), "source": payload.get("source", "手动收集")[:200]}
                if item:
                    if item.get('text') != values['text']: item['aliases'] = list(set(item.get('aliases', []) + [question_key(item['text'])]))
                    if item.get('answer') != values['answer'] or item.get('text') != values['text'] or item.get('block_ids') != ids:
                        item['status'] = 'answered' if values['answer'].strip() else 'open'
                    item.update(values)
                else: items.append({"id": uid("question"), 'status': 'answered' if values['answer'].strip() else 'open', **values})
            elif action == "issue":
                status = payload.get("status")
                if status not in ("unresolved", "decision", "recheck", "confirmed"): raise BlockError("未知问题状态", 422)
                if status in ("confirmed", "decision") and not payload.get("note", "").strip(): raise BlockError("请填写确认依据或保留原因", 422)
                run = db.get(AgentRun, payload.get("run_id"))
                if not run or run.document_id != doc.id or run.type != "document_review": raise BlockError("审核来源不存在", 422)
                valid = {digest(json.dumps([sorted(i.get("block_ids", [])), i["description"]], ensure_ascii=False)) for i in (run.result or {}).get("issues", [])}
                if payload.get("id") not in valid: raise BlockError("审核问题不存在", 422)
                previous = state.setdefault("issues", {}).get(payload["id"], {})
                event = {"status": status, "note": payload.get("note", "")[:4000], "revision": doc.revision, "at": now(), "actor": actor, "run_id": run.id}
                event['block_versions'] = {b.id: b.version for b in self.blocks.blocks(db, doc)}
                event['context_revision'] = doc.context_revision
                state["issues"][payload["id"]] = {**event, "history": (previous.get("history", []) + [event])[-100:]}
            elif action == "alternative_note":
                alt = next((a for a in state.get("alternatives", []) if a["id"] == payload.get("id")), None)
                if not alt: raise BlockError("方案不存在", 404)
                alt["note"] = payload.get("note", "")[:4000]
            else:
                raise BlockError("未知工作台操作", 422)
            row.state, row.revision = state, row.revision + 1
        return self.inspect(document_id, actor)

    def collect(self, document_id, actor, expected):
        snapshot = self.blocks.snapshot(document_id, actor)
        candidates = []
        for b in snapshot["blocks"]:
            candidates.extend((line.strip(), [b["id"]], "正文") for line in b["content"].splitlines() if UNKNOWN.search(line))
        for c in snapshot["comments"]:
            if not c["resolved"]: candidates.append((c["text"], [t["block_id"] for t in c["targets"]], "批注 " + c["id"]))
        for r in snapshot["runs"]:
            if r["type"] == "document_review" and r["status"] == "completed":
                candidates.extend((i["description"], i.get("block_ids", []), "审核 " + r["id"]) for i in (r["result"] or {}).get("issues", []) if UNKNOWN.search(i["description"] + i["suggestion"]))
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            row, state = self._state(db, doc)
            if row.revision != expected: raise BlockError("工作台已更新，请刷新后重试")
            messages = db.query(WorkspaceChatMessage).filter_by(session_id=doc.session_id).order_by(WorkspaceChatMessage.created_at.desc()).limit(100).all()
            candidates.extend((m.body, [], "对话 " + m.id) for m in messages if UNKNOWN.search(m.body))
            items = state.setdefault("questions", [])
            seen = {key for q in items for key in [question_key(q['text']), *q.get('aliases', [])]}
            for text, ids, source in candidates:
                text = text[:4000]
                key = question_key(text)
                if key in seen: continue
                seen.add(key)
                category = "information" if re.search(r"资料|数据|提供|补充", text) else "implementation" if re.search(r"技术|接口|实现|选型", text) else "business"
                items.append({"id": uid("question"), "text": text, "answer": "", 'status': 'open', "block_ids": ids, "source": source, "category": category, "updated_at": now()})
            row.state, row.revision = state, row.revision + 1
        return self.inspect(document_id, actor)

    async def plan(self, document_id, actor, kind, instruction, target_ids, item_ids, anchor_id="", persist_alternative=True, knowledge=None):
        data = self.inspect(document_id, actor)
        state = data["state"]
        selected = ""
        if kind == "answers":
            items = [q for q in state.get("questions", []) if q["id"] in item_ids and q.get("answer", "").strip()]
            if not items: raise BlockError("请先填写并保存所选问题的答案", 422)
            ids = list(dict.fromkeys(k for q in items for k in q["block_ids"]))
            target_ids = list(dict.fromkeys(ids + (target_ids if any(not q["block_ids"] for q in items) else [])))
            instruction = "仅依据以下用户确认的答案更新关联内容，保留其他要求，不扩展范围：\n" + "\n".join(q["text"] + "\n已确认答案：" + q["answer"] for q in items)
        elif kind == "anchor":
            anchor = next((a for a in state["anchors"] if a["id"] == anchor_id and not a.get("missing")), None)
            if not anchor: raise BlockError("该位置内容已改变或无法唯一定位，请重新选择", 409)
            selected, target_ids = anchor["quote"], [anchor["block_id"]]
            instruction = f"只修改指定位置（{anchor_id}），保留其他正文。\n" + instruction
        elif kind not in ("alternative", "update"):
            raise BlockError("未知方案类型", 422)
        if len(instruction) > 24000: raise BlockError("本次答案过多，请分批预览", 422)
        plan = await self.edits.plan(document_id, actor, instruction, target_ids, selected, knowledge=knowledge)
        if kind == "answers" and len(items) < len(set(item_ids)):
            plan.setdefault("impact_notes", []).append(f"已跳过 {len(set(item_ids)) - len(items)} 条未填写答案或已不存在的信息；本次仅采用有确认答案的条目。")
        if kind == "anchor":
            if plan["base_revision"] != data["document_revision"]:
                raise BlockError("定位后正文又有变化，请刷新位置后重试")
            if any(s["action"] in ("er_upsert", "move_block") for s in plan["steps"]):
                raise BlockError("该操作需要章节或 ER 图专用入口，请通过对话修改并核对完整预览", 422)
            for item in plan["preview"]:
                if item["block_id"] != target_ids[0] or item["before"].count(selected) != 1:
                    raise BlockError("指定位置无法唯一定位，候选未应用")
                prefix, suffix = item["before"].split(selected, 1)
                if (not item["after"].startswith(prefix) or not item["after"].endswith(suffix)
                        or len(item["after"]) < len(prefix) + len(suffix)
                        or item.get("metadata_changes") or item["old_parent_id"] != item["parent_id"]
                        or item["old_order"] != item["order"]):
                    raise BlockError("候选修改超出指定位置，请重新描述修改要求；正文未改变")
        if kind == "alternative" and persist_alternative:
            with self.blocks.write() as db:
                doc = self.blocks.document(db, document_id, actor)
                row, state = self._state(db, doc)
                alt = {"id": uid("alternative"), "created_at": now(), "instruction": instruction,
                       "note": "", "plan": plan, "status": "draft", "target_ids": target_ids}
                state.setdefault("alternatives", []).append(alt)
                row.state, row.revision = state, row.revision + 1
                plan = {**plan, "alternative_id": alt["id"]}
        return plan

    def meeting(self, document_id, actor, left, right):
        if left == right: raise BlockError("请选择两个不同版本", 422)
        left, right = sorted((left, right))
        before, after = [self.blocks.document_version(document_id, v, actor) for v in (left, right)]
        data = self.inspect(document_id, actor)
        old = {b["id"]: b for b in before["snapshot"]["blocks"]}
        new = {b["id"]: b for b in after["snapshot"]["blocks"]}
        lines = [f"# {after['snapshot']['document']['title']} · 评审会议包", f"\n比较：v{left} → v{right}",
                 f"\n版本时间：{before['created_at']} → {after['created_at']}", f"\n生成时间：{now()}", "\n## 本次目录与正文变化"]
        count = 0
        for key in dict.fromkeys([*old, *new]):
            a, b = old.get(key), new.get(key)
            changes = ["新增"] if not a else ["删除"] if not b else [label for field, label in (("title", "标题"), ("parent_id", "层级"), ("order", "位置"), ("content", "正文"), ("instruction", "生成要求"), ("dependencies", "依赖"), ("fragment", "图源/结构化资料")) if a.get(field) != b.get(field)]
            if not changes: continue
            count += 1
            lines.append(f"- {(b or a)['title']}：{'、'.join(changes)}（章节 {key}）")
        if not count: lines.append("无目录或正文差异。")
        lines.extend(["\n## 尚未解决的审核事项", f"以下为生成时当前文档（revision {data['document_revision']}）的记录；旧审核已标注来源，不代表所选版本已审核通过。"])
        for i in data["issues"]:
            if i["status"] != "confirmed": lines.append(f"- [{i['severity']}] {i['description']}；建议：{i['suggestion']}；来源 revision {i['source_revision']} / {i['run_id']}；{'待复查' if i['stale'] else '当前证据'}；记录：{i['note'] or '无'}")
        lines.append("\n## 待拍板与已补充信息")
        for q in data["state"].get("questions", []):
            if q.get('status') not in ('ignored', 'archived'):
                lines.append(f"- {q['text']}\n  - 答案：{q['answer'] or '待确认'}；来源：{q['source']}")
        lines.append('\n## 当前已确认业务决定')
        for policy in data.get('policies', []):
            if policy['kind'] == 'decision' and policy['status'] == 'active':
                lines.append(f"- {policy['text']}；依据：{policy['reason']}；适用范围：{policy['scope']}")
        lines.append("\n## 备选方案")
        for a in data["state"].get("alternatives", []): lines.append(f"- {a['plan']['summary']}（基于 revision {a['plan']['base_revision']}，{a['status']}）\n  - 讨论记录：{a['note'] or '待补充'}")
        lines.extend(["\n## 会议决议（待填写）", "- 决定 / 责任人 / 截止日期 / 关联章节：", "- 此材料不构成审批通过；正文变更需通过修改预览应用。"])
        return {"markdown": "\n".join(lines), "filename": f"PRD-review-v{left}-v{right}.md"}
