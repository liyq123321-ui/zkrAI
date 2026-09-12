"""Durable, bounded single-process AI jobs. No database transaction spans AI await."""
import asyncio
import logging
from app.models import Block, AgentRun
from app.ai.provider import BlockPlan, BlockResult
from .block_service import BlockError, uid, record

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, blocks, provider):
        self.blocks, self.provider = blocks, provider
        self.tasks = {}
        self.limit = asyncio.Semaphore(3)

    def enqueue(self, document_id, actor, kind, request_id, block_id=None, feedback="", line_range=None, comment_id=None):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            existing = db.query(AgentRun).filter_by(document_id=doc.id, request_id=request_id).one_or_none()
            if existing:
                if existing.type != kind or existing.block_id != block_id:
                    raise BlockError("请求 ID 已用于另一项操作")
                return record(existing)
            active = db.query(AgentRun).filter_by(document_id=doc.id).filter(AgentRun.status.in_(["queued", "running"])).all()
            if any(r.type == "plan" or kind == "plan" or r.block_id == block_id for r in active):
                raise BlockError("该 Block 或结构规划已有运行中的作业")
            deps = {}
            context = {"background": doc.background, "global_rules": doc.global_rules}
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

    async def execute(self, run_id):
        try:
            async with self.limit:
                with self.blocks.write() as db:
                    run = db.get(AgentRun, run_id)
                    if not run or run.status != "queued":
                        return
                    run.status = "running"
                    kind, context = run.type, run.input_snapshot
                method = {"plan": self.provider.plan_outline, "generate": self.provider.generate_block,
                          "revise": self.provider.revise_block, "review": self.provider.review_block}[kind]
                output = await method(context)
                output = (BlockPlan if kind == "plan" else BlockResult).model_validate(output)
                with self.blocks.write() as db:
                    run = db.get(AgentRun, run_id)
                    if run.status != "running":
                        return
                    doc = self.blocks.document(db, run.document_id, run.actor_id)
                    if kind != "plan":
                        if output.block_id != run.block_id or output.base_version != run.base_version:
                            raise BlockError("AI 返回了错误的 Block 或基线版本")
                        if kind == "review" and output.content != context["content"]:
                            raise BlockError("审核操作不能改写正文")
                        if context.get("line_range"):
                            start, end = context["line_range"]["start_line"], context["line_range"]["end_line"]
                            original = context["content"].splitlines(keepends=True)
                            prefix, suffix = ''.join(original[:start - 1]), ''.join(original[end:])
                            if (not output.content.startswith(prefix) or not output.content.endswith(suffix)
                                    or len(output.content) < len(prefix) + len(suffix)):
                                raise BlockError("AI 修改了指定行范围以外的内容，结果未应用")
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
            if run.apply_status == "applied":
                return {"applied": True}
            if run.status != "completed" or run.type == "plan" or not run.result:
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
                else:
                    doc.plan_status = "pending"
            elif run.apply_status == "applied":
                raise BlockError("结果已应用，若需撤回请从历史版本恢复")
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
            else:
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
