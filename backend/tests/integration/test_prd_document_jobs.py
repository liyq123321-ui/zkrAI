import asyncio
import pytest
from app.ai.provider import DocumentEditPlan, DocumentEditStep
from app.models import DocumentJob
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService
from app.services.document_workbench import DocumentWorkbench
from app.services.document_jobs import DocumentJobs
from app.services.document_commands import dispatch
from tests.integration.test_block_prd import service, prepared, manual


class Planner:
    async def plan_chat_edit(self, context):
        self.context = context
        return DocumentEditPlan(summary='修改预览', steps=[DocumentEditStep(action='replace_text',
            block_id=context['blocks'][0]['id'], old_text='Original', new_text='Updated')])


def setup(service, provider=None):
    snap = prepared(service, '# A')
    snap = manual(service, snap['document']['id'], snap['blocks'][0], 'Original\n\n待确认登录方式？')
    edits = DocumentEditService(service, provider or Planner())
    workbench = DocumentWorkbench(service, edits)
    return snap, edits, workbench, DocumentJobs(workbench)


@pytest.mark.asyncio
async def test_job_deduplicates_and_apply_is_atomic_and_idempotent(service):
    snap, edits, workbench, jobs = setup(service)
    doc, rev = snap['document']['id'], snap['document']['revision']
    payload = dict(kind='alternative', instruction='更新', target_ids=[snap['blocks'][0]['id']])
    job = jobs.enqueue(doc, 'owner', 'one', payload, rev)
    assert jobs.enqueue(doc, 'owner', 'one', payload, rev)['id'] == job['id']
    await jobs.tasks[job['id']]
    detail = jobs.get(doc, 'owner', job['id'])
    assert detail['status'] == 'ready'
    assert service.snapshot(doc, 'owner')['document']['latest_version'] == snap['document']['latest_version']
    result = detail['result']
    steps = [DocumentEditStep.model_validate(s) for s in result['steps']]
    after = edits.apply(doc, 'owner', rev, steps, result['alternative_id'], job['id'])
    assert after['document']['latest_version'] == snap['document']['latest_version'] + 1
    again = edits.apply(doc, 'owner', rev, steps, result['alternative_id'], job['id'])
    assert again['document']['latest_version'] == after['document']['latest_version']
    assert jobs.get(doc, 'owner', job['id'])['status'] == 'applied'
    assert workbench.inspect(doc, 'owner')['state']['alternatives'][0]['status'] == 'applied'
    with pytest.raises(BlockError): jobs.get(doc, 'stranger', job['id'])


@pytest.mark.asyncio
async def test_cancel_and_restart_never_apply_or_lose_finished_candidates(service):
    class Slow(Planner):
        def __init__(self): self.started = asyncio.Event()
        async def plan_chat_edit(self, context):
            self.started.set()
            await asyncio.Event().wait()
    provider = Slow()
    snap, edits, workbench, jobs = setup(service, provider)
    doc, rev = snap['document']['id'], snap['document']['revision']
    job = jobs.enqueue(doc, 'owner', 'slow', dict(kind='update', instruction='改写', target_ids=[snap['blocks'][0]['id']]), rev)
    task = jobs.tasks[job['id']]
    await provider.started.wait()
    jobs.cancel(doc, 'owner', job['id'])
    await task
    assert jobs.get(doc, 'owner', job['id'])['status'] == 'cancelled'
    with service.write() as db:
        db.add(DocumentJob(id='interrupted', document_id=doc, actor_id='owner', request_id='lost', kind='update',
                           status='running', base_revision=rev, payload={'kind': 'update'}, events=[]))
    jobs.recover()
    assert jobs.get(doc, 'owner', 'interrupted')['status'] == 'failed'
    assert service.snapshot(doc, 'owner')['document']['revision'] == rev


@pytest.mark.asyncio
async def test_chat_collect_answer_freezes_input_then_returns_preview(service):
    provider = Planner()
    snap, edits, workbench, jobs = setup(service, provider)
    doc, rev = snap['document']['id'], snap['document']['revision']
    targets = [snap['blocks'][0]['id']]
    result = dispatch(workbench, jobs, doc, 'owner', '收集待确认信息', targets, None, 'collect', rev)
    assert '1.' in result['summary']
    result = dispatch(workbench, jobs, doc, 'owner', '第一个问题的答案是管理员登录，生成修改预览', targets, None, 'answer', rev)
    job = result['job']
    data = workbench.inspect(doc, 'owner')
    question = data['state']['questions'][0]
    workbench.update(doc, 'owner', data['revision'], 'question', {**question, 'answer': '后来修改的答案'})
    await jobs.tasks[job['id']]
    assert '管理员登录' in provider.context['instruction']
    assert '后来修改的答案' not in provider.context['instruction']
    assert jobs.get(doc, 'owner', job['id'])['status'] == 'ready'
    assert service.snapshot(doc, 'owner')['blocks'][0]['content'] == snap['blocks'][0]['content']


@pytest.mark.asyncio
async def test_changed_document_rejects_old_candidate_and_retry_keeps_old_record(service):
    snap, edits, workbench, jobs = setup(service)
    doc, rev = snap['document']['id'], snap['document']['revision']
    job = jobs.enqueue(doc, 'owner', 'old', dict(kind='update', instruction='调整', target_ids=[snap['blocks'][0]['id']]), rev)
    await jobs.tasks[job['id']]
    old = jobs.get(doc, 'owner', job['id'])['result']
    changed = manual(service, doc, snap['blocks'][0], 'Human change\nOriginal')
    with pytest.raises(BlockError, match='新版本'):
        edits.apply(doc, 'owner', rev, [DocumentEditStep.model_validate(s) for s in old['steps']], job_id=job['id'])
    retry = jobs.retry(doc, 'owner', job['id'], 'retry', changed['document']['revision'])
    await jobs.tasks[retry['id']]
    assert jobs.get(doc, 'owner', job['id'])['status'] == 'ready'
    assert jobs.get(doc, 'owner', retry['id'])['result']['base_revision'] == changed['document']['revision']


class LocatingPlanner(Planner):
    """记录「先定位、后规划」两步各自拿到什么上下文。"""

    def __init__(self, block_ids):
        self.block_ids = block_ids
        self.select_context = None
        self.plan_context = None

    async def select_chat_targets(self, context):
        self.select_context = context
        return {"block_ids": self.block_ids, "reason": "指令点名了该章节正文里的表格"}

    async def plan_chat_edit(self, context):
        self.plan_context = context
        return await super().plan_chat_edit(context)


@pytest.mark.asyncio
async def test_document_scope_locates_chapters_before_planning(service):
    """未勾选章节、指令里也没写章节名时：先读整篇目录定位，再只拿命中的章节去规划。"""
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Original\n\n待确认登录方式？')
    target_id = snap['blocks'][0]['id']
    provider = LocatingPlanner([target_id])
    edits = DocumentEditService(service, provider)
    jobs = DocumentJobs(DocumentWorkbench(service, edits))

    job = jobs.enqueue(doc, 'owner', 'doc-scope', dict(kind='chat', instruction='把这个表重排一下',
                                                       target_ids=[], scope='document'), snap['document']['revision'])
    await jobs.tasks[job['id']]

    assert jobs.get(doc, 'owner', job['id'])['status'] == 'ready'
    # 第一步只喂目录 + 摘要，绝不喂整篇正文（本机那份 236 块根本进不了单次上下文）。
    assert provider.select_context['document_map']
    assert 'blocks' not in provider.select_context
    # 第二步才把命中的章节正文交给规划。
    assert [block['id'] for block in provider.plan_context['blocks']] == [target_id]


@pytest.mark.asyncio
async def test_document_scope_fails_with_a_usable_message_when_nothing_is_located(service):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Original\n\n待确认登录方式？')
    provider = LocatingPlanner([])
    edits = DocumentEditService(service, provider)
    jobs = DocumentJobs(DocumentWorkbench(service, edits))

    job = jobs.enqueue(doc, 'owner', 'doc-scope-empty', dict(kind='chat', instruction='优化一下',
                                                            target_ids=[], scope='document'), snap['document']['revision'])
    await jobs.tasks[job['id']]

    detail = jobs.get(doc, 'owner', job['id'])
    assert detail['status'] == 'failed'
    assert '勾选' in detail['error']
    assert provider.plan_context is None  # 定位失败就不该再去规划
