"""Durable, bounded single-process AI jobs. No database transaction spans AI await."""
import asyncio
import logging
import json
from app.models import Block, AgentRun
from app.database.models import SpecVersion
from app.ai.provider import BlockPlan, BlockResult, DocumentReview, DocumentRevision, DocumentRevisionBlock
from .block_service import BlockError, uid, record

logger = logging.getLogger(__name__)
MAX_SKILL_RESOLUTION_ROUNDS = 2
MAX_BLOCK_REFERENCE_SUMMARY_CHARS = 1200
LINE_RANGE_FLEX_LINES = 500


def line_range_change_is_local(
    original: str, candidate: str, start_line: int, end_line: int,
    padding: int = LINE_RANGE_FLEX_LINES,
) -> bool:
    """Treat a selected range as an anchor while rejecting distant or whole-Block rewrites."""

    from difflib import SequenceMatcher

    original_lines = original.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    candidate_lines = candidate.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if original_lines == candidate_lines:
        return True
    allowed_start = max(0, start_line - 1 - padding)
    allowed_end = min(len(original_lines), end_line + padding)
    changes = [opcode for opcode in SequenceMatcher(
        None, original_lines, candidate_lines, autojunk=False
    ).get_opcodes() if opcode[0] != "equal"]
    if not changes:
        return True
    # A narrow selection never grants permission to replace the complete Block,
    # even when the Block itself is shorter than the normal floating window.
    if (start_line > 1 or end_line < len(original_lines)) and any(
        i1 == 0 and i2 == len(original_lines) for _, i1, i2, _, _ in changes
    ):
        return False
    for _, i1, i2, _, _ in changes:
        if i1 == i2:  # insertion between source lines
            if not allowed_start <= i1 <= allowed_end:
                return False
        elif i1 < allowed_start or i2 > allowed_end:
            return False
    return True


def block_reference_summary(block: dict) -> str:
    """Build a small deterministic reference when a persisted Block summary is absent."""

    summary = str(block.get("summary") or "").strip()
    if summary:
        return summary[:MAX_BLOCK_REFERENCE_SUMMARY_CHARS]
    lines = [line.strip() for line in str(block.get("content") or "").splitlines() if line.strip()]
    headings = [line.lstrip("# ").strip() for line in lines if line.startswith("#")][:8]
    body = [line for line in lines if not line.startswith("#")][:4]
    return "；".join(headings + body)[:MAX_BLOCK_REFERENCE_SUMMARY_CHARS]


def compact_document_context(
    context: dict, candidates: dict[str, dict], review: DocumentReview, target_ids: list[str]
) -> dict:
    """Send full prose only for targeted Blocks; retain a bounded map for global consistency."""

    target_set = set(target_ids)
    selected_issues = [
        issue for issue in review.issues
        if not issue.block_ids or target_set.intersection(issue.block_ids)
    ]
    compact_review = review.model_copy(update={"issues": selected_issues})
    document_map = [
        {
            "id": block["id"], "title": block["title"], "parent_id": block.get("parent_id"),
            "order": block["order"], "version": block["version"],
            "dependencies": block.get("dependencies", []),
            "summary": block_reference_summary(block),
        }
        for block in candidates.values()
    ]
    return {
        "document_id": context["document_id"], "base_revision": context["base_revision"],
        "title": context.get("title", ""), "background": context.get("background", ""),
        "global_rules": context.get("global_rules", ""),
        "review": compact_review.model_dump(mode="json"),
        "blocks": [candidates[block_id] for block_id in target_ids if block_id in candidates],
        "document_map": document_map,
    }


def sentence_chunks(content: str) -> list[str]:
    """Keep Markdown byte-for-byte while exposing only complete display units."""
    if not content:
        return []
    endings = set("。！？!?；;\n")
    closers = set("”’」』）】》\"'")
    chunks: list[str] = []
    start = 0
    index = 0
    while index < len(content):
        if content[index] not in endings:
            index += 1
            continue
        end = index + 1
        while end < len(content) and content[end] in closers:
            end += 1
        chunks.append(content[start:end])
        start = end
        index = end
    if start < len(content):
        chunks.append(content[start:])
    return [chunk for chunk in chunks if chunk]


class AIService:
    def __init__(self, blocks, provider):
        self.blocks, self.provider = blocks, provider
        self.tasks = {}
        self.limit = asyncio.Semaphore(3)

    def update_progress(self, run_id: str, **values) -> bool:
        """Merge viewer-safe progress metadata into a queued or running job."""
        with self.blocks.write() as db:
            run = db.get(AgentRun, run_id)
            if not run or run.status not in ("queued", "running"):
                return False
            run.result = {**(run.result or {}), **values}
        return True

    async def publish_block_stream(self, run_id: str, content: str) -> bool:
        """Publish validated Markdown one complete sentence/line at a time."""
        chunks = sentence_chunks(content)
        rendered = ""
        for index, chunk in enumerate(chunks):
            rendered += chunk
            with self.blocks.write() as db:
                run = db.get(AgentRun, run_id)
                if not run or run.status != "running":
                    return False
                run.result = {
                    "stream_content": rendered,
                    "stream_sentence_count": index + 1,
                    "stream_complete": index + 1 == len(chunks),
                    "progress_stage": "streaming",
                    "current_block_id": run.block_id,
                    "completed": index + 1,
                    "total": len(chunks),
                    "progress_unit": "sentence",
                }
            if index + 1 < len(chunks):
                await asyncio.sleep(0.12)
        return True

    async def publish_document_stream(self, run_id: str, output: DocumentRevision) -> bool:
        """Expose a whole-document repair as independent Block sentence streams."""
        rendered: dict[str, str] = {}
        completed = 0
        total = sum(len(sentence_chunks(item.content)) for item in output.blocks)
        block_ids = [item.block_id for item in output.blocks]
        for block_index, item in enumerate(output.blocks):
            chunks = sentence_chunks(item.content)
            self.update_progress(
                run_id,
                progress_stage="streaming",
                current_block_id=item.block_id,
                active_block_ids=block_ids,
                completed_block_ids=block_ids[:block_index],
                completed=block_index,
                total=len(block_ids),
                progress_unit="block",
            )
            for chunk in chunks:
                rendered[item.block_id] = rendered.get(item.block_id, "") + chunk
                completed += 1
                with self.blocks.write() as db:
                    run = db.get(AgentRun, run_id)
                    if not run or run.status != "running":
                        return False
                    run.result = {
                        "stream_blocks": dict(rendered),
                        "stream_sentence_count": completed,
                        "stream_complete": completed == total,
                        "progress_stage": "streaming",
                        "current_block_id": item.block_id,
                        "active_block_ids": block_ids,
                        "completed_block_ids": block_ids[:block_index],
                        "completed": block_index,
                        "total": len(output.blocks),
                        "progress_unit": "block",
                    }
                if completed < total:
                    await asyncio.sleep(0.12)
        return True

    def enqueue_document_review(self, document_id, actor, request_id, expected_revision):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            existing = db.query(AgentRun).filter_by(document_id=doc.id, request_id=request_id).one_or_none()
            if existing:
                if existing.type != "document_review":
                    raise BlockError("请求 ID 已用于另一项操作")
                return record(existing)
            active = db.query(AgentRun).filter_by(document_id=doc.id).filter(
                AgentRun.status.in_(["queued", "running"])
            ).all()
            same_review = next((run for run in active if run.type == "document_review"), None)
            if same_review:
                return record(same_review)
            if doc.revision != expected_revision:
                raise BlockError("全文已改变，请刷新后重新审核")
            if active:
                raise BlockError("请等待当前生成或审核任务完成后，再审核全文")
            blocks = self.blocks.blocks(db, doc)
            if not any(b.content.strip() for b in blocks):
                raise BlockError("请先编写 PRD 正文再审核全文")
            context = {"document_id": doc.id, "base_revision": doc.revision, "title": doc.title,
                       "background": doc.background, "global_rules": doc.global_rules,
                       "blocks": [{key: getattr(b, key) for key in
                                   ("id", "title", "parent_id", "order", "version", "content", "dependencies")}
                                  for b in blocks]}
            if len(json.dumps(context, ensure_ascii=False)) > 300000:
                raise BlockError("全文超过单次审核容量（30 万字符），请精简后重试；未截断正文", 422)
            block_ids = [block.id for block in blocks]
            run = AgentRun(id=uid("run"), document_id=doc.id, type="document_review", status="queued",
                           base_version=doc.revision, input_snapshot=context,
                           dependency_versions={b.id: b.version for b in blocks},
                           context_revision=doc.context_revision, request_id=request_id, actor_id=actor,
                           apply_status="pending", result={"progress_stage": "queued", "completed": 0,
                           "total": len(block_ids), "progress_unit": "scope", "active_block_ids": block_ids})
            db.add(run)
            db.flush()
            result, run_id = record(run), run.id
        self.tasks[run_id] = asyncio.create_task(self.execute(run_id))
        self.tasks[run_id].add_done_callback(lambda task: self.tasks.pop(run_id, None))
        return result

    def enqueue_document_revision(self, document_id, actor, review_run_id, request_id, expected_revision):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            existing = db.query(AgentRun).filter_by(document_id=doc.id, request_id=request_id).one_or_none()
            if existing:
                if existing.type != "document_revision":
                    raise BlockError("请求 ID 已用于另一项操作")
                return record(existing)
            active = db.query(AgentRun).filter_by(document_id=doc.id).filter(
                AgentRun.status.in_(["queued", "running"])
            ).all()
            same_revision = next((run for run in active if run.type == "document_revision"), None)
            if same_revision:
                return record(same_revision)
            if doc.revision != expected_revision:
                raise BlockError("全文已改变，请更新全文后再自动解决")
            if active:
                raise BlockError("请等待当前生成或审核任务完成后，再自动解决")
            review = db.get(AgentRun, review_run_id)
            blocks = self.blocks.blocks(db, doc)
            versions = {b.id: b.version for b in blocks}
            if (not review or review.document_id != doc.id or review.type != "document_review"
                    or review.status != "completed" or not review.result):
                raise BlockError("没有可解决的全文审核意见", 404)
            if (review.apply_status != "applied" or review.context_revision != doc.context_revision
                    or review.dependency_versions != versions):
                raise BlockError("审核意见已过期，请先重新审核全文")
            context = {
                "document_id": doc.id,
                "base_revision": doc.revision,
                "title": doc.title,
                "background": doc.background,
                "global_rules": doc.global_rules,
                "review_run_id": review.id,
                "review": review.result,
                "blocks": [{key: getattr(b, key) for key in
                            ("id", "title", "parent_id", "order", "version", "content", "dependencies", "summary")}
                           for b in blocks],
            }
            if len(json.dumps(context, ensure_ascii=False)) > 300000:
                raise BlockError("全文超过单次修订容量（30 万字符），请分块解决", 422)
            target_ids = []
            for issue in review.result.get("issues", []):
                for block_id in issue.get("block_ids", []):
                    if block_id in versions and block_id not in target_ids:
                        target_ids.append(block_id)
            if not target_ids:
                target_ids = [block.id for block in blocks]
            run = AgentRun(id=uid("run"), document_id=doc.id, type="document_revision", status="queued",
                           base_version=doc.revision, input_snapshot=context, dependency_versions=versions,
                           context_revision=doc.context_revision, request_id=request_id, actor_id=actor,
                           apply_status="pending", result={"progress_stage": "queued", "completed": 0,
                           "total": len(target_ids), "progress_unit": "block",
                           "current_block_id": target_ids[0] if target_ids else None,
                           "active_block_ids": target_ids, "completed_block_ids": []})
            db.add(run)
            db.flush()
            result, run_id = record(run), run.id
        self.tasks[run_id] = asyncio.create_task(self.execute(run_id))
        self.tasks[run_id].add_done_callback(lambda task: self.tasks.pop(run_id, None))
        return result

    def start_initial(self, document_id, actor):
        """Claim a first draft once, durably, even across tabs and page reloads."""
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            runs = db.query(AgentRun).filter_by(document_id=doc.id).all()
            existing = next((r for r in runs if r.type == "initial"), None)
            if existing:
                return
            blocks = self.blocks.blocks(db, doc)
            if (doc.source_spec_version_id or doc.submitted_spec_id
                    or db.query(SpecVersion).filter_by(project_id=doc.project_id).count()
                    or not blocks or any(b.content or b.version > 1 for b in blocks)
                    or any(r.type != "plan" or r.status != "completed" or r.apply_status != "applied" for r in runs)):
                return
            # Stable topological order: preserve directory order when dependencies allow.
            ordered, remaining = [], list(blocks)
            while remaining:
                ready = next((b for b in remaining if set(b.dependencies) <= set(ordered)), None)
                if ready is None:
                    raise BlockError("模块依赖无法排序，请先检查大纲")
                ordered.append(ready.id)
                remaining.remove(ready)
            run = AgentRun(id=uid("run"), document_id=doc.id, type="initial", status="queued",
                           base_version=doc.revision, context_revision=doc.context_revision,
                           request_id="initial-generation-v1", actor_id=actor, apply_status="pending",
                           input_snapshot={"block_ids": ordered, "versions": {b.id: b.version for b in blocks}},
                           result={"completed": 0, "total": len(ordered), "progress_unit": "block",
                                   "progress_stage": "queued",
                                   "current_block_id": ordered[0] if ordered else None,
                                   "active_block_ids": ordered})
            db.add(run)
            db.flush()
            run_id = run.id
        self.tasks[run_id] = asyncio.create_task(self.execute_initial(run_id))
        self.tasks[run_id].add_done_callback(lambda task: self.tasks.pop(run_id, None))

    async def execute_initial(self, run_id):
        try:
            with self.blocks.write() as db:
                run = db.get(AgentRun, run_id)
                if run.status != "queued":
                    return
                run.status = "running"
                document_id, actor, inputs = run.document_id, run.actor_id, run.input_snapshot
            for index, block_id in enumerate(inputs["block_ids"]):
                self.update_progress(run_id, progress_stage="generating", completed=index,
                                     total=len(inputs["block_ids"]), progress_unit="block",
                                     current_block_id=block_id, active_block_ids=inputs["block_ids"])
                child = self.enqueue(document_id, actor, "generate", f"{run_id}:{block_id}",
                                     block_id, initial_run_id=run_id)
                await self.tasks[child["id"]]
                with self.blocks.factory() as db:
                    result = db.get(AgentRun, child["id"])
                    if result.status == "cancelled":
                        raise asyncio.CancelledError()
                    if result.status != "completed":
                        raise BlockError(result.error or "模块生成失败")
                    if not (result.result or {}).get("content", "").strip():
                        raise BlockError("模块生成内容为空，请手动重试")
                # Applying before the next enqueue makes upstream summaries available.
                self.apply(document_id, child["id"], actor, inputs["versions"][block_id])
                with self.blocks.write() as db:
                    run = db.get(AgentRun, run_id)
                    run.result = {"completed": index + 1, "total": len(inputs["block_ids"]),
                                  "progress_unit": "block", "progress_stage": "generating",
                                  "current_block_id": (inputs["block_ids"][index + 1]
                                                       if index + 1 < len(inputs["block_ids"]) else block_id),
                                  "active_block_ids": inputs["block_ids"]}
            with self.blocks.write() as db:
                run = db.get(AgentRun, run_id)
                run.status, run.apply_status = "completed", "applied"
        except asyncio.CancelledError:
            self.fail(run_id, "首版连续生成已停止，已有内容已保留", "cancelled")
            raise
        except Exception as error:
            logger.exception("Initial PRD generation %s stopped", run_id)
            self.fail(run_id, "首版连续生成已停止：" + (str(error) if isinstance(error, BlockError) else "请检查服务日志"))

    def enqueue(self, document_id, actor, kind, request_id, block_id=None, feedback="", line_range=None, comment_id=None, initial_run_id=None):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            existing = db.query(AgentRun).filter_by(document_id=doc.id, request_id=request_id).one_or_none()
            if existing:
                if existing.type != kind or existing.block_id != block_id:
                    raise BlockError("请求 ID 已用于另一项操作")
                return record(existing)
            active = db.query(AgentRun).filter_by(document_id=doc.id).filter(AgentRun.status.in_(["queued", "running"])).all()
            same_scope = next((run for run in active if
                               (block_id is not None and run.block_id == block_id)
                               or (kind == "plan" and run.type == "plan")), None)
            if same_scope:
                return record(same_scope)
            if any(r.type in ("document_review", "document_revision") for r in active):
                raise BlockError("正在处理全文，请等待完成或取消后再运行 AI")
            if any(r.type == "initial" and r.id != initial_run_id for r in active):
                raise BlockError("首版正在连续生成，请先停止自动生成再运行其他 AI 操作")
            if initial_run_id:
                parent = next((r for r in active if r.id == initial_run_id and r.type == "initial"), None)
                if not parent:
                    raise BlockError("首版连续生成已停止")
                b = self.blocks.block(db, doc, block_id)
                if (b.content or b.version != parent.input_snapshot["versions"].get(block_id)
                        or doc.context_revision != parent.context_revision):
                    raise BlockError("模块或文档背景已有人工修改，保留当前内容，请手动继续")
            if any(r.type == "plan" or kind == "plan" or r.block_id == block_id for r in active):
                raise BlockError("该 Block 或结构规划已有运行中的作业")
            deps = {}
            context = {"background": doc.background, "global_rules": doc.global_rules}
            if initial_run_id:
                context["initial_run_id"] = initial_run_id
            if kind == "plan":
                if any(b.content or b.version > 1 for b in self.blocks.blocks(db, doc)):
                    raise BlockError("已有内容，结构规划不能覆盖现有 Block")
                context["outline"] = [{"key": b.id, "title": b.title, "parent_key": b.parent_id,
                                       "instruction": b.instruction, "dependencies": b.dependencies}
                                      for b in self.blocks.blocks(db, doc)]
                base_version = doc.revision
                doc.plan_status = "generating"
            else:
                b = self.blocks.block(db, doc, block_id)
                base_version = b.version
                upstream = [self.blocks.block(db, doc, key) for key in b.dependencies]
                if any(not u.content or u.status == "outdated" for u in upstream):
                    raise BlockError("请先生成或更新上游依赖 Block")
                deps = {u.id: u.version for u in upstream}
                if comment_id:
                    from app.models import BlockComment
                    comment = db.get(BlockComment, comment_id)
                    if not comment or comment.document_id != doc.id:
                        raise BlockError("批注不存在", 404)
                    target = next((t for t in comment.targets if t["block_id"] == b.id), None)
                    if not target or target["base_version"] != b.version:
                        raise BlockError("批注基于旧版本，请重新选择当前内容后添加批注")
                    feedback = comment.text
                    line_range = {"start_line": target["start_line"], "end_line": target["end_line"]} if target.get("start_line") else None
                if line_range:
                    start, end = line_range["start_line"], line_range["end_line"]
                    if not 1 <= start <= end <= len(b.content.splitlines(keepends=True)):
                        raise BlockError("行范围无效", 422)
                    line_range = {
                        "start_line": start,
                        "end_line": end,
                        "allowed_start_line": max(1, start - LINE_RANGE_FLEX_LINES),
                        "allowed_end_line": min(len(b.content.splitlines()), end + LINE_RANGE_FLEX_LINES),
                    }
                context.update(block_id=b.id, base_version=b.version, title=b.title, instruction=b.instruction,
                               content=b.content, feedback=feedback, line_range=line_range,
                               upstream_summaries=[{"block_id": u.id, "summary": u.summary} for u in upstream],
                               requirement_id_range=[min(b.order * 9 + 1, 991), min(b.order * 9 + 9, 999)])
                b.status = "generating"
            run = AgentRun(id=uid("run"), document_id=doc.id, block_id=block_id, type=kind, status="queued",
                           base_version=base_version, input_snapshot=context, dependency_versions=deps,
                           context_revision=doc.context_revision, request_id=request_id, actor_id=actor, apply_status="pending")
            db.add(run)
            db.flush()
            result = record(run)
        self.tasks[run.id] = asyncio.create_task(self.execute(run.id))
        self.tasks[run.id].add_done_callback(lambda task: self.tasks.pop(run.id, None))
        return result

    async def resolve_document(self, run_id, context):
        """Revise and re-review an in-memory candidate before committing it."""
        originals = {block["id"]: dict(block) for block in context["blocks"]}
        candidates = {key: dict(value) for key, value in originals.items()}
        fragments = {}
        review = DocumentReview.model_validate(context["review"])
        last_summary = "已根据全文审核意见完成自动修订。"
        target_ids = []
        for issue in review.issues:
            for block_id in issue.block_ids:
                if block_id in candidates and block_id not in target_ids:
                    target_ids.append(block_id)
        if not target_ids:
            target_ids = list(candidates)
        # Each round now contains separate authoring, registry/consistency and
        # verification Skill calls. Two bounded rounds avoid an expensive retry
        # cascade while still allowing one evidence-backed correction cycle.
        for round_number in range(1, MAX_SKILL_RESOLUTION_ROUNDS + 1):
            self.update_progress(run_id, progress_stage="revising", resolution_round=round_number,
                                 completed=0, total=len(target_ids), progress_unit="block",
                                 current_block_id=target_ids[0] if target_ids else None,
                                 active_block_ids=target_ids, completed_block_ids=[])
            revision_context = compact_document_context(context, candidates, review, target_ids)
            revision_context["resolution_round"] = round_number
            full_size = len(json.dumps({**context, "blocks": list(candidates.values())}, ensure_ascii=False))
            compact_size = len(json.dumps(revision_context, ensure_ascii=False))
            logger.info(
                "Document revision context compacted: run=%s round=%s full_chars=%s compact_chars=%s",
                run_id, round_number, full_size, compact_size,
            )
            revision = DocumentRevision.model_validate(await self.provider.revise_document(revision_context))
            last_summary = revision.summary
            for item in revision.blocks:
                if item.block_id not in candidates:
                    continue
                candidates[item.block_id]["content"] = item.content
                fragments[item.block_id] = item
            revised_ids = [item.block_id for item in revision.blocks if item.block_id in candidates]
            self.update_progress(run_id, progress_stage="verifying", resolution_round=round_number,
                                 completed=len(revised_ids), total=max(len(target_ids), len(revised_ids)),
                                 progress_unit="block", current_block_id=revised_ids[-1] if revised_ids else None,
                                 active_block_ids=revised_ids or target_ids)
            verification_targets = revised_ids or target_ids
            verification_context = compact_document_context(
                context, candidates, review, verification_targets
            )
            verification_context.update(
                auto_resolution_verification=True, resolution_round=round_number
            )
            review = DocumentReview.model_validate(await self.provider.review_document(verification_context))
            valid_ids = set(candidates)
            review = review.model_copy(update={
                "document_id": context["document_id"], "base_revision": context["base_revision"],
                "issues": [issue.model_copy(update={"block_ids": [key for key in issue.block_ids if key in valid_ids]})
                           for issue in review.issues],
            })
            blockers = [issue for issue in review.issues if issue.severity == "blocker"]
            if not blockers:
                break
            next_targets = []
            for issue in blockers:
                for block_id in issue.block_ids:
                    if block_id in candidates and block_id not in next_targets:
                        next_targets.append(block_id)
            target_ids = next_targets or verification_targets
        changed_blocks = []
        for block_id, candidate in candidates.items():
            if candidate["content"] == originals[block_id]["content"]:
                continue
            item = fragments[block_id]
            changed_blocks.append(DocumentRevisionBlock(
                block_id=block_id, base_version=originals[block_id]["version"], content=candidate["content"],
                summary=item.summary, spec_fragment=item.spec_fragment,
            ))
        return DocumentRevision(document_id=context["document_id"], base_revision=context["base_revision"],
                                summary=last_summary, blocks=changed_blocks), review

    async def execute(self, run_id):
        try:
            async with self.limit:
                with self.blocks.write() as db:
                    run = db.get(AgentRun, run_id)
                    if not run or run.status != "queued":
                        return
                    run.status = "running"
                    kind = run.type
                    context = {key: value for key, value in run.input_snapshot.items() if key != "initial_run_id"}
                verification = None
                if kind == "document_revision":
                    output, verification = await self.resolve_document(run_id, context)
                else:
                    method = getattr(self.provider, {"plan": "plan_outline", "generate": "generate_block",
                              "revise": "revise_block", "review": "review_block",
                              "document_review": "review_document"}[kind])
                    if kind == "document_review":
                        self.update_progress(run_id, progress_stage="reviewing")
                    output = await method(context)
                    contract = DocumentReview if kind == "document_review" else BlockPlan if kind == "plan" else BlockResult
                    output = contract.model_validate(output)
                if kind in ("generate", "revise"):
                    if not await self.publish_block_stream(run_id, output.content):
                        return
                elif kind == "document_revision":
                    if not await self.publish_document_stream(run_id, output):
                        return
                with self.blocks.write() as db:
                    run = db.get(AgentRun, run_id)
                    if run.status != "running":
                        return
                    doc = self.blocks.document(db, run.document_id, run.actor_id)
                    if kind == "document_review":
                        # The run owns its read-only identity metadata. Models occasionally copy a
                        # nearby revision or title instead of the supplied opaque id; normalise those
                        # fields and retain only references that are safe to navigate to.
                        valid_ids = set(run.dependency_versions)
                        block_content = {block.id: block.content for block in self.blocks.blocks(db, doc)}
                        issues = []
                        for issue in output.issues:
                            evidence = []
                            for item in issue.evidence:
                                quote = item.quote.strip()
                                if item.block_id in valid_ids and quote and quote in block_content.get(item.block_id, ""):
                                    evidence.append(item.model_copy(update={"quote": quote}))
                            issues.append(issue.model_copy(update={
                                "block_ids": [key for key in issue.block_ids if key in valid_ids],
                                "evidence": evidence,
                            }))
                        output = output.model_copy(update={
                            "document_id": doc.id,
                            "base_revision": run.base_version,
                            "issues": issues,
                        })
                        # A report never writes blocks, versions, fragments or approval state.
                        changed = (doc.context_revision != run.context_revision or
                                   {b.id: b.version for b in self.blocks.blocks(db, doc)} != run.dependency_versions)
                        run.apply_status = "conflict" if changed else "applied"
                    elif kind == "document_revision":
                        current_versions = {b.id: b.version for b in self.blocks.blocks(db, doc)}
                        changed = doc.context_revision != run.context_revision or current_versions != run.dependency_versions
                        valid_ids = set(run.dependency_versions)
                        revised = []
                        seen = set()
                        for item in output.blocks:
                            if item.block_id not in valid_ids:
                                continue
                            if item.block_id in seen:
                                raise BlockError("AI 返回了重复的 Block 修订")
                            seen.add(item.block_id)
                            revised.append(item.model_copy(update={"base_version": run.dependency_versions[item.block_id]}))
                        output = output.model_copy(update={"document_id": doc.id, "base_revision": run.base_version,
                                                           "blocks": revised})
                        if changed:
                            run.apply_status = "conflict"
                        else:
                            changed_ids = []
                            for item in revised:
                                block = self.blocks.block(db, doc, item.block_id)
                                if block.content == item.content:
                                    continue
                                self.blocks.save_in_transaction(db, doc, block, block.version,
                                    {"content": item.content, "summary": item.summary,
                                     "fragment": item.spec_fragment.model_dump(mode="json")},
                                    run.actor_id, run.type, run.id)
                                changed_ids.append(block.id)
                            for block_id in changed_ids:
                                self.blocks.block(db, doc, block_id).status = "ready"
                            source = db.get(AgentRun, context["review_run_id"])
                            if source and source.status == "completed":
                                source.apply_status = "resolved"
                            run.apply_status = "applied"
                            current_blocks = self.blocks.blocks(db, doc)
                            current_versions = {block.id: block.version for block in current_blocks}
                            verification = verification.model_copy(update={
                                "document_id": doc.id, "base_revision": doc.revision,
                                "issues": [issue.model_copy(update={
                                    "block_ids": [key for key in issue.block_ids if key in current_versions],
                                    "evidence": [item for item in issue.evidence
                                                 if item.block_id in current_versions and item.quote.strip()
                                                 and item.quote.strip() in next((block.content for block in current_blocks
                                                                               if block.id == item.block_id), "")],
                                }) for issue in verification.issues],
                            })
                            verify_run = AgentRun(
                                id=uid("run"), document_id=doc.id, type="document_review", status="completed",
                                base_version=doc.revision,
                                input_snapshot={"document_id": doc.id, "base_revision": doc.revision,
                                                "title": doc.title, "background": doc.background,
                                                "global_rules": doc.global_rules,
                                                "blocks": [{key: getattr(block, key) for key in
                                                           ("id", "title", "parent_id", "order", "version", "content", "dependencies")}
                                                          for block in current_blocks]},
                                dependency_versions=current_versions, context_revision=doc.context_revision,
                                request_id=f"{run.request_id}:verify", actor_id=run.actor_id,
                                apply_status="applied", result=verification.model_dump(mode="json"),
                            )
                            db.add(verify_run)
                    elif kind != "plan":
                        if output.block_id != run.block_id or output.base_version != run.base_version:
                            raise BlockError("AI 返回了错误的 Block 或基线版本")
                        if kind == "review" and output.content != context["content"]:
                            raise BlockError("审核操作不能改写正文")
                        if context.get("line_range"):
                            start, end = context["line_range"]["start_line"], context["line_range"]["end_line"]
                            if not line_range_change_is_local(context["content"], output.content, start, end):
                                raise BlockError(
                                    f"AI 修改超出选区附近的浮动范围（前后各 {LINE_RANGE_FLEX_LINES} 行），结果未应用"
                                )
                        b = self.blocks.block(db, doc, run.block_id)
                        stale_inputs = self.stale(db, doc, run)
                        run.apply_status = "conflict" if stale_inputs or b.version != run.base_version else "pending"
                        b.status = "outdated" if stale_inputs and b.content else ("ready" if b.content else "pending")
                    else:
                        if doc.revision != run.base_version:
                            run.apply_status, doc.plan_status = "conflict", "pending"
                        else:
                            self.blocks.replace_plan(db, doc, output, run.actor_id)
                            doc.plan_status, run.apply_status = "ready", "applied"
                    run.result = output.model_dump(mode="json")
                    run.status = "completed"
        except asyncio.CancelledError:
            self.fail(run_id, "作业已取消", "cancelled")
            raise
        except Exception as error:
            logger.exception("Block job %s failed", run_id)
            self.fail(run_id, str(error) if isinstance(error, (BlockError, ValueError)) else "AI 调用失败，请重试或检查服务日志")

    def stale(self, db, doc, run):
        return (doc.context_revision != run.context_revision or any(
            (b := db.get(Block, key)) is None or b.version != version or b.status == "outdated"
            for key, version in run.dependency_versions.items()))

    def apply(self, document_id, run_id, actor, expected_version, force=False):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            run = db.get(AgentRun, run_id)
            if not run or run.document_id != doc.id:
                raise BlockError("作业不存在", 404)
            if run.type in ("document_review", "document_revision"):
                raise BlockError("全文作业由服务端统一处理，不能作为单个 Block 应用")
            if run.apply_status == "applied":
                return {"applied": True}
            if run.status != "completed" or run.type in ("plan", "initial") or not run.result:
                raise BlockError("作业没有可应用的 Block 结果")
            b = self.blocks.block(db, doc, run.block_id)
            stale = self.stale(db, doc, run)
            if b.version != expected_version or (not force and (b.version != run.base_version or stale)):
                raise BlockError("AI 结果基于旧版本生成；请查看 Diff 或基于当前版本重新生成")
            result = BlockResult.model_validate(run.result)
            if run.type == "review":
                if b.content != result.content:
                    raise BlockError("正文已经改变，请重新审核")
                fragment = result.spec_fragment.model_dump(mode="json")
                supplied_ids = {d["diagram_id"] for d in fragment.get("er_diagrams", [])}
                fragment["er_diagrams"].extend(d for d in (b.fragment or {}).get("er_diagrams", []) if d["diagram_id"] not in supplied_ids)
                b.fragment = fragment
                b.fragment_version = b.version
                b.summary = result.summary
                doc.revision += 1
            else:
                self.blocks.save_in_transaction(db, doc, b, expected_version,
                    {"content": result.content, "summary": result.summary, "fragment": result.spec_fragment.model_dump(mode="json")},
                    actor, run.type, run.id)
            # Explicit acceptance does not make changed dependency context current.
            b.status = "outdated" if stale else "ready"
            run.apply_status = "applied"
        return {"applied": True}

    def cancel(self, document_id, run_id, actor):
        children = []
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            run = db.get(AgentRun, run_id)
            if not run or run.document_id != doc.id:
                raise BlockError("作业不存在", 404)
            if run.status in ("queued", "running") or (run.status == "completed" and run.apply_status != "applied"):
                run.status, run.apply_status = "cancelled", "cancelled"
                if run.block_id:
                    b = self.blocks.block(db, doc, run.block_id)
                    other_active = db.query(AgentRun).filter(AgentRun.block_id == b.id, AgentRun.id != run.id,
                                                           AgentRun.status.in_(["queued", "running"])).count()
                    if not other_active:
                        b.status = "outdated" if self.stale(db, doc, run) and b.content else ("ready" if b.content else "pending")
                elif run.type == "plan":
                    doc.plan_status = "pending"
                elif run.type == "initial":
                    children = [r.id for r in db.query(AgentRun).filter_by(document_id=doc.id)
                                if r.input_snapshot.get("initial_run_id") == run.id
                                and (r.status in ("queued", "running") or (r.status == "completed" and r.apply_status != "applied"))]
            elif run.apply_status == "applied":
                raise BlockError("结果已应用，若需撤回请从历史版本恢复")
        for child_id in children:
            self.cancel(document_id, child_id, actor)
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
        return {"cancelled": True}

    def retry(self, document_id, run_id, actor, request_id):
        with self.blocks.factory() as db:
            doc = self.blocks.document(db, document_id, actor)
            run = db.get(AgentRun, run_id)
            if not run or run.document_id != doc.id or not run.block_id:
                raise BlockError("Block 作业不存在", 404)
            block = self.blocks.block(db, doc, run.block_id)
            context = run.input_snapshot
            line_range = context.get("line_range")
            if line_range:
                old_lines = context["content"].splitlines(keepends=True)
                quote = ''.join(old_lines[line_range['start_line'] - 1:line_range['end_line']])
                if not quote or block.content.count(quote) != 1:
                    raise BlockError("原行范围已变化，请重新选择当前行范围并点击 AI 修改")
                offset = block.content.index(quote)
                start = block.content[:offset].count('\n') + 1
                line_range = {"start_line": start, "end_line": start + len(quote.splitlines()) - 1}
            kind, block_id, feedback = run.type, run.block_id, context.get("feedback", "")
        return self.enqueue(document_id, actor, kind, request_id, block_id, feedback, line_range)

    def fail(self, run_id, message, status="failed"):
        with self.blocks.write() as db:
            run = db.get(AgentRun, run_id)
            if not run or run.status not in ("queued", "running"):
                return
            run.status, run.error = status, message[:3000]
            if run.block_id:
                b = db.get(Block, run.block_id)
                if b and b.version == run.base_version:
                    b.status = "error"
            elif run.type == "plan":
                from app.models import Document
                db.get(Document, run.document_id).plan_status = "error"

    def recover(self):
        with self.blocks.factory() as db:
            ids = [r.id for r in db.query(AgentRun).filter(AgentRun.status.in_(["queued", "running"]))]
        for run_id in ids:
            self.fail(run_id, "PROCESS_INTERRUPTED：服务已重启，请重试")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
