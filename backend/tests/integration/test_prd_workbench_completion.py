import asyncio
from types import SimpleNamespace
import pytest
from app.ai.provider import DocumentEditPlan, DocumentEditStep, DocumentReview
from app.ai.codex_provider import CodexProvider
from app.models import DocumentWorkspace, Block, DocumentJob
from app.services.block_service import BlockError
from app.services.document_workbench import DocumentWorkbench
from app.services.document_edit_service import DocumentEditService
from app.services.document_jobs import DocumentJobs
from app.services.document_decisions import update_policy, knowledge_context
from tests.integration.test_block_prd import service, prepared, manual


class Planner:
    def __init__(self): self.contexts = []
    async def plan_chat_edit(self, context):
        self.contexts.append(context)
        return DocumentEditPlan(summary='更新所选内容', steps=[DocumentEditStep(action='replace_text', block_id=b['id'], old_text='Original', new_text='Updated') for b in context['blocks']])


def setup(service, count=1):
    snap = prepared(service, '\n'.join(f'# Section {i}' for i in range(count)))
    for b in snap['blocks']:
        manual(service, snap['document']['id'], b, 'Original\n待确认访问权限\n为什么采用这个标题？')
    snap = service.snapshot(snap['document']['id'], 'owner')
    provider = Planner(); edits = DocumentEditService(service, provider)
    workbench = DocumentWorkbench(service, edits)
    return snap, provider, workbench, DocumentJobs(workbench)


@pytest.mark.asyncio
async def test_linkage_splits_idempotently_and_never_expands_selected_scope(service):
    snap, provider, workbench, jobs = setup(service, 10)
    doc, rev = snap['document']['id'], snap['document']['revision']
    ids = [b['id'] for b in snap['blocks'][:9]]
    group = jobs.enqueue_linked(doc, 'owner', 'group', rev, '同步更新规则', ids, ids[0])
    assert len(group['job_ids']) == 2 and len(group['omitted_ids']) == 1
    assert jobs.enqueue_linked(doc, 'owner', 'group', rev, '同步更新规则', ids, ids[0])['id'] == group['id']
    await asyncio.gather(*(jobs.tasks[k] for k in group['job_ids']))
    assert all(jobs.get(doc, 'owner', k)['status'] == 'ready' for k in group['job_ids'])
    assert {b['id'] for c in provider.contexts for b in c['blocks']} == set(ids)
    assert service.snapshot(doc, 'owner')['document']['revision'] == rev


@pytest.mark.asyncio
async def test_question_ignored_dedup_and_applied_receipt_only_after_write(service):
    snap, provider, workbench, jobs = setup(service)
    doc = snap['document']['id']
    state = workbench.inspect(doc, 'owner')
    state = workbench.collect(doc, 'owner', state['revision'])
    assert len(state['state']['questions']) == 1  # A plain question mark is not a missing requirement.
    question = state['state']['questions'][0]
    state = workbench.update(doc, 'owner', state['revision'], 'question_state', {'id': question['id'], 'question_status': 'ignored'})
    state = workbench.collect(doc, 'owner', state['revision'])
    assert len(state['state']['questions']) == 1 and state['state']['questions'][0]['status'] == 'ignored'
    state = workbench.update(doc, 'owner', state['revision'], 'question', {**question, 'answer': '游客只读'})
    job = jobs.enqueue(doc, 'owner', 'answers', {'kind': 'answers', 'item_ids': [question['id']], 'target_ids': [snap['blocks'][0]['id']]}, snap['document']['revision'])
    await jobs.tasks[job['id']]
    assert workbench.inspect(doc, 'owner')['state']['questions'][0]['status'] == 'answered'
    result = jobs.get(doc, 'owner', job['id'])['result']
    updated = workbench.edits.apply(doc, 'owner', result['base_revision'], [DocumentEditStep.model_validate(s) for s in result['steps']], job_id=job['id'])
    q = workbench.inspect(doc, 'owner')['state']['questions'][0]
    assert q['status'] == 'applied' and q['applied_receipt']['version'] == updated['document']['latest_version']


@pytest.mark.asyncio
async def test_policy_snapshot_once_consumption_and_revocation_version_guard(service):
    snap, provider, workbench, jobs = setup(service)
    doc = snap['document']['id']; state = workbench.inspect(doc, 'owner')
    state = update_policy(workbench, doc, 'owner', state['revision'], {'kind': 'decision', 'scope': 'document', 'status': 'active', 'text': '游客只读', 'reason': '本期明确范围', 'block_ids': []})
    decision = state['policies'][0]
    state = update_policy(workbench, doc, 'owner', state['revision'], {'kind': 'preference', 'scope': 'once', 'status': 'active', 'text': '简短表达', 'reason': '', 'block_ids': []})
    snap = service.snapshot(doc, 'owner')
    job = jobs.enqueue(doc, 'owner', 'knowledge', {'kind': 'chat', 'instruction': '修改正文', 'target_ids': [snap['blocks'][0]['id']]}, snap['document']['revision'])
    await jobs.tasks[job['id']]
    assert provider.contexts[0]['confirmed_knowledge']['decisions'][0]['text'] == '游客只读'
    assert provider.contexts[0]['confirmed_knowledge']['preferences'][0]['text'] == '简短表达'
    state = workbench.inspect(doc, 'owner')
    assert next(p for p in state['policies'] if p['scope'] == 'once')['status'] == 'used'
    result = jobs.get(doc, 'owner', job['id'])['result']
    update_policy(workbench, doc, 'owner', state['revision'], {**decision, 'status': 'revoked'})
    with pytest.raises(BlockError): workbench.edits.apply(doc, 'owner', result['base_revision'], [DocumentEditStep.model_validate(s) for s in result['steps']], job_id=job['id'])
    from app.services.document_candidate_reuse import reuse
    with pytest.raises(BlockError, match='业务决定或文档规则'):
        reuse(workbench.edits, doc, 'owner', job['id'], [0])
    with service.factory() as db:
        d = service.document(db, doc, 'owner')
        assert knowledge_context(db, d, snap['blocks'])['decisions'] == []


def test_cross_chapter_acceptance_coverage_uses_only_current_fragments(service):
    snap, provider, workbench, jobs = setup(service, 2)
    with service.write() as db:
        a, b = [db.get(Block, x['id']) for x in snap['blocks']]
        a.fragment = {'functional_requirements': [{'requirement_id': 'FR-1'}]}; a.fragment_version = a.version
        b.fragment = {'acceptance_criteria': [{'requirement_ids': ['FR-1']}]}; b.fragment_version = b.version
    state = workbench.inspect(snap['document']['id'], 'owner')
    assert not any('未找到关联验收' in r for h in state['health'] for r in h['reasons'])
    with service.write() as db: db.get(Block, snap['blocks'][1]['id']).fragment_version = None
    assert any('未找到关联验收' in r for h in workbench.inspect(snap['document']['id'], 'owner')['health'] for r in h['reasons'])


@pytest.mark.asyncio
async def test_review_cache_skips_repeat_but_bypasses_next_stage_and_rule_changes():
    provider = CodexProvider(SimpleNamespace(codex_model='test-model', codex_binary='test'))
    calls = []
    async def run(operation, context, contract, **kwargs):
        calls.append(context)
        return DocumentReview(document_id=context['document_id'], base_revision=context['base_revision'], summary='检查完成', issues=[])
    provider._call_uncached = run
    ctx = {'document_id': 'doc', 'base_revision': 1, 'background': 'A', 'global_rules': '', 'blocks': []}
    await provider._call('document_quality_review', ctx, DocumentReview)
    second = await provider._call('document_quality_review', {**ctx, 'base_revision': 2}, DocumentReview)
    assert second.base_revision == 2 and len(calls) == 1
    await provider._call('document_quality_review', {**ctx, 'global_rules': 'new rule'}, DocumentReview)
    await provider._call('document_quality_review', {**ctx, 'prepare_submission': True}, DocumentReview)
    assert len(calls) == 3
