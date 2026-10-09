"""Durable candidate jobs. No background job may apply PRD content."""
import asyncio
import time
import json
from datetime import UTC
from sqlalchemy.orm import defer
from copy import deepcopy
from app.models import DocumentJob
from app.agents.progress import bind_agent_progress
from app.services.block_service import BlockError, record, uid
from app.services.document_workbench import now


class DocumentJobs:
    def __init__(self, workbench):
        self.workbench = workbench
        self.blocks = workbench.blocks
        self.tasks = {}
        self.capacity = asyncio.Semaphore(2)

    def view(self, row, detail=False):
        value = record(row) if detail else {key: getattr(row, key) for key in
            ("id", "document_id", "actor_id", "request_id", "kind", "status", "base_revision", "events", "error")}
        value["created_at"] = (row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)).isoformat()
        value.pop("apply_fingerprint", None)
        value["title"] = (row.payload.get("instruction") or "确认答案写回")[:240]
        value['group'] = row.payload.get('_group')
        value['timings'] = row.payload.get('_timings', {})
        if not detail:
            value.pop("payload", None)
            value.pop("result", None)
        return value

    def list(self, document_id, actor):
        with self.blocks.factory() as db:
            self.blocks.document(db, document_id, actor)
            rows = db.query(DocumentJob).options(defer(DocumentJob.result)).filter_by(document_id=document_id).order_by(DocumentJob.created_at.desc()).limit(100).all()
            return [self.view(r) for r in rows]

    def get(self, document_id, actor, job_id):
        with self.blocks.factory() as db:
            self.blocks.document(db, document_id, actor)
            row = db.get(DocumentJob, job_id)
            if not row or row.document_id != document_id: raise BlockError("任务不存在", 404)
            return self.view(row, True)

    def enqueue(self, document_id, actor, request_id, payload, expected_revision):
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            existing = db.query(DocumentJob).filter_by(document_id=doc.id, request_id=request_id).one_or_none()
            if existing:
                if ({k: v for k, v in existing.payload.items() if not k.startswith("_")} != {k: v for k, v in payload.items() if not k.startswith("_")}
                        or existing.base_revision != expected_revision):
                    raise BlockError("请求标识已用于其他任务")
                return self.view(existing)
            if doc.revision != expected_revision: raise BlockError("正文已变化，请核对最新版本后重试")
            payload = deepcopy(payload)
            if payload["kind"] == "answers" and "_answers" not in payload:
                _, state = self.workbench._state(db, doc)
                items = [q for q in state.get("questions", []) if q["id"] in payload.get("item_ids", []) and q.get("answer", "").strip() and q.get('status') not in ('ignored', 'archived', 'applied')]
                if not items: raise BlockError("请先保存所选问题的答案", 422)
                targets = list(dict.fromkeys(k for q in items for k in (q.get("block_ids") or payload.get("target_ids", []))))
                if not targets or len(targets) > 8: raise BlockError("请选择关联章节，一次最多八个章节", 422)
                instruction = "仅依据下列用户确认答案修改对应章节，保留其他要求：\n" + "\n".join(q["text"] + "\n答案：" + q["answer"] for q in items)
                if len(instruction) > 24000: raise BlockError("本次答案过多，请分批生成预览", 422)
                payload["_answers"] = {"instruction": instruction, "target_ids": targets, 'items': deepcopy(items)}
            if '_knowledge' not in payload:
                from app.services.document_decisions import knowledge_context
                ids = (payload.get('_answers') or {}).get('target_ids') or payload.get('target_ids', [])
                all_blocks = [record(b) for b in self.blocks.blocks(db, doc)]
                if payload['kind'] in ('chat', 'update', 'alternative') and not payload.get('object_target'):
                    from app.services.document_edit_service import _related_targets
                    ids = _related_targets(all_blocks, ids, payload.get('instruction', ''))
                blocks = [b for b in all_blocks if not ids or b['id'] in ids]
                payload['_knowledge'] = knowledge_context(db, doc, blocks, consume=True)
            job = DocumentJob(id=uid("prdjob"), document_id=doc.id, actor_id=actor, request_id=request_id,
                              kind=payload["kind"], payload=payload, base_revision=doc.revision,
                              status="queued", events=[])
            db.add(job); db.flush()
            result = self.view(job)
        task = asyncio.create_task(self.execute(job.id))
        self.tasks[job.id] = task
        task.add_done_callback(lambda _: self.tasks.pop(job.id, None))
        return result

    def enqueue_linked(self, document_id, actor, request_id, revision, instruction, target_ids, origin_id):
        ids = list(dict.fromkeys(target_ids))
        created = []
        with self.blocks.write() as db:
            doc = self.blocks.document(db, document_id, actor)
            workspace, state = self.workbench._state(db, doc)
            groups = state.setdefault('linked_groups', [])
            old = next((g for g in groups if g['request_id'] == request_id), None)
            if old:
                if old['selected_ids'] != ids or old['instruction'] != instruction or old['base_revision'] != revision or old['origin_id'] != origin_id:
                    raise BlockError('请求标识已用于其他联动操作', 422)
                return old
            if doc.revision != revision: raise BlockError('正文已变化，请刷新影响地图')
            blocks = [record(b) for b in self.blocks.blocks(db, doc)]
            known = {b['id'] for b in blocks}
            if not ids or set(ids) - known or origin_id not in known: raise BlockError('请选择当前文档中的章节', 422)
            from app.services.document_decisions import knowledge_context
            knowledge = knowledge_context(db, doc, [b for b in blocks if b['id'] in ids], consume=True)
            state = deepcopy(workspace.state or state)
            group_id = uid('linked')
            chunks = [ids[i:i+8] for i in range(0, len(ids), 8)]
            group = {'id': group_id, 'request_id': request_id, 'selected_ids': ids, 'origin_id': origin_id,
                     'instruction': instruction, 'base_revision': revision, 'created_at': now(), 'job_ids': [],
                     'omitted_ids': [b['id'] for b in blocks if b['id'] not in ids],
                     'artifact_note': '只生成所选正文候选；HTML 原型、PPT 和工单未同步，需确认冻结版本后单独更新。'}
            for i, chunk in enumerate(chunks):
                job = DocumentJob(id=uid('prdjob'), document_id=doc.id, actor_id=actor, request_id=f'{group_id}:{i}',
                    kind='linked', status='queued', base_revision=revision, events=[], payload={
                        'kind': 'linked', 'instruction': instruction, 'target_ids': chunk, '_knowledge': knowledge,
                        '_group': {'id': group_id, 'part': i+1, 'total': len(chunks)}, '_receipt': group.copy()})
                group['job_ids'].append(job.id); db.add(job); created.append(job.id)
            state['linked_groups'] = (groups + [group])[-100:]
            workspace.state, workspace.revision = state, workspace.revision + 1
        for job_id in created:
            task = asyncio.create_task(self.execute(job_id)); self.tasks[job_id] = task
            task.add_done_callback(lambda _, key=job_id: self.tasks.pop(key, None))
        return group

    def performance(self, document_id, actor):
        from app.models import AgentRun
        with self.blocks.factory() as db:
            self.blocks.document(db, document_id, actor)
            jobs = db.query(DocumentJob).options(defer(DocumentJob.result)).filter_by(document_id=document_id).order_by(DocumentJob.created_at.desc()).limit(50).all()
            runs = db.query(AgentRun).filter_by(document_id=document_id).order_by(AgentRun.created_at.desc()).limit(30).all()
            return {'candidate_jobs': [self.view(j) for j in jobs], 'agent_runs': [
                {'id': r.id, 'type': r.type, 'status': r.status, 'created_at': r.created_at.isoformat(),
                 'error': r.error, 'timings': (r.input_snapshot or {}).get('operation_timings', [])} for r in runs],
                 'concurrency': 2, 'note': '候选最多两路并行。耗时为实际记录，不据此宣称固定提速比例；缓存命中不替代全文审核门禁。'}

    async def execute(self, job_id):
        started = time.monotonic()
        try:
            async with self.capacity:
                with self.blocks.write() as db:
                    row = db.get(DocumentJob, job_id)
                    if row.status != "queued": return
                    doc = self.blocks.document(db, row.document_id, row.actor_id)
                    if doc.revision != row.base_revision: raise BlockError("排队期间正文已变化，请基于当前版本重试")
                    row.status = "running"
                    document_id, actor, payload = row.document_id, row.actor_id, deepcopy(row.payload)
                    payload['_timings'] = {'queue_seconds': round(time.monotonic() - started, 3), 'operations': []}
                    row.payload = deepcopy(payload)
                def progress(_kind, message, *_args, **_kwargs):
                    with self.blocks.write() as db:
                        row = db.get(DocumentJob, job_id)
                        if row.status != "running": return
                        if _kind == 'operation_timing':
                            value = deepcopy(row.payload); metrics = value.setdefault('_timings', {}).setdefault('operations', [])
                            metrics.append(json.loads(message)); row.payload = value
                            return
                        row.events = (list(row.events or []) + [{"message": str(message)[:1000], "seconds": round(time.monotonic() - started, 1)}])[-50:]
                generating = time.monotonic()
                with bind_agent_progress(progress):
                    progress("stage", "正在准备目标内容与修改方案")
                    if payload["kind"] == "answers":
                        frozen = payload["_answers"]
                        result = await self.workbench.edits.plan(document_id, actor, frozen["instruction"], frozen["target_ids"], knowledge=payload['_knowledge'], strict_targets=True)
                        result['answer_receipt'] = frozen.get('items', [])
                    elif payload['kind'] == 'linked':
                        result = await self.workbench.edits.plan(document_id, actor, payload['instruction'], payload['target_ids'], knowledge=payload['_knowledge'], strict_targets=True)
                        result['linkage_receipt'] = payload['_receipt']
                    elif payload["kind"] == "chat":
                        target_ids = payload["target_ids"]
                        if not target_ids and payload.get("scope") == "document":
                            # 用户没勾选章节、指令里也没写章节名 —— 默认面向整篇全文：
                            # 先让 Agent 读整篇目录挑出这次要改的章节，再规划具体修改。
                            progress("stage", "未指定章节，正在读取整篇目录并定位要修改的章节")
                            located = time.monotonic()
                            target_ids, reason = await self.workbench.edits.select_targets(
                                document_id, actor, payload["instruction"], knowledge=payload['_knowledge'])
                            progress("stage", f"已定位 {len(target_ids)} 个章节{('：' + reason) if reason else ''}；"
                                              f"耗时 {round(time.monotonic() - located, 1)} 秒，正在生成修改方案")
                        result = await self.workbench.edits.plan(document_id, actor, payload["instruction"], target_ids,
                            payload.get("selected_text", ""), payload.get("screenshot_anchor", ""), payload.get("conversation_context", ""), payload.get("object_target"), knowledge=payload['_knowledge'])
                    elif payload["kind"] == "bulk":
                        result = await self.workbench.edits.plan_bulk(document_id, actor, payload["instruction"], knowledge=payload['_knowledge'])
                    else:
                        result = await self.workbench.plan(document_id, actor, payload["kind"], payload.get("instruction", ""),
                            payload.get("target_ids", []), payload.get("item_ids", []), payload.get("anchor_id", ""), persist_alternative=False, knowledge=payload['_knowledge'])
                generation_seconds = time.monotonic() - generating
                saving = time.monotonic()
                with self.blocks.write() as db:
                    row = db.get(DocumentJob, job_id)
                    if row.status != "running": return
                    doc = self.blocks.document(db, document_id, actor)
                    if result["base_revision"] != row.base_revision:
                        raise BlockError("生成时目标版本发生变化，请核对后重试")
                    if payload["kind"] == "alternative":
                        workspace, state = self.workbench._state(db, doc)
                        alt_id = uid("alternative")
                        state.setdefault("alternatives", []).append({"id": alt_id, "created_at": now(),
                            "instruction": payload["instruction"], "note": "", "status": "draft", "plan": {**result, "job_id": job_id},
                            "target_ids": payload.get("target_ids", [])})
                        workspace.state, workspace.revision = state, workspace.revision + 1
                        result = {**result, "alternative_id": alt_id}
                    row.result = {**result, "job_id": job_id}
                    row.status = "ready"
                    timed = deepcopy(row.payload)
                    timed['_timings'].update(generation_seconds=round(generation_seconds, 3), save_seconds=round(time.monotonic() - saving, 3), total_seconds=round(time.monotonic() - started, 3))
                    row.payload = timed
                    row.events = list(row.events or []) + [{"message": "候选已保存，等待预览和应用", "seconds": round(time.monotonic() - started, 1)}]
        except asyncio.CancelledError:
            self._fail(job_id, "cancelled", "已取消结果接收，不会应用正文；模型服务可能仍在结束调用")
        except Exception as error:
            self._fail(job_id, "failed", str(error)[:2000])

    def _fail(self, job_id, status, message):
        with self.blocks.write() as db:
            row = db.get(DocumentJob, job_id)
            if row and row.status in ("queued", "running"):
                row.status, row.error = status, message
                value = deepcopy(row.payload); value.setdefault('_timings', {})['failure'] = message; row.payload = value

    def cancel(self, document_id, actor, job_id):
        self.get(document_id, actor, job_id)
        self._fail(job_id, "cancelled", "已取消结果接收，不会应用正文；模型服务可能仍在结束调用")
        if job_id in self.tasks: self.tasks[job_id].cancel()
        return self.get(document_id, actor, job_id)

    def retry(self, document_id, actor, job_id, request_id, expected_revision):
        old = self.get(document_id, actor, job_id)
        if old["status"] in ("queued", "running", "applied"): raise BlockError("当前任务不需要重试")
        payload = {k: v for k, v in old['payload'].items() if k not in ('_knowledge', '_timings')}
        return self.enqueue(document_id, actor, request_id, payload, expected_revision)

    def recover(self):
        with self.blocks.write() as db:
            for row in db.query(DocumentJob).filter(DocumentJob.status.in_(["queued", "running"])):
                row.status, row.error = "failed", "服务已重启，执行中断；已保存的候选仍保留，可基于当前版本重试"

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
