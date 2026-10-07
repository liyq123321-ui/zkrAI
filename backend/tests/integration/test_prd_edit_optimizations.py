import pytest

from app.ai.provider import BlockResult, DocumentEditStep, DocumentReview, DocumentReviewIssue, DocumentRevision, DocumentRevisionBlock, SpecFragment
from app.models import AgentRun
from app.services.ai_service import AIService
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService
from tests.integration.test_block_prd import service, prepared, manual


def test_undo_preview_conflict_and_single_restore_version(service):
    snap = prepared(service, '# A\n# B')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Original')
    edits = DocumentEditService(service, None)
    changed = edits.apply(doc, 'owner', snap['document']['revision'], [DocumentEditStep(
        action='replace_text', block_id=snap['blocks'][0]['id'], old_text='Original', new_text='Changed')])
    plan = edits.restore_preview(doc, 'owner')
    assert plan['preview'][0]['after'] == 'Original'
    assert len(service.document_versions(doc, 'owner')) == changed['document']['latest_version']
    restored = edits.restore_apply(doc, 'owner', plan['base_revision'], plan['restore_version'])
    assert restored['blocks'][0]['content'] == 'Original'
    assert restored['document']['latest_version'] == changed['document']['latest_version'] + 1
    with pytest.raises(BlockError, match='最近一次保存'):
        edits.restore_preview(doc, 'owner')
    plan = edits.restore_preview(doc, 'owner', changed['document']['latest_version'])
    manual(service, doc, restored['blocks'][0], 'New human edit')
    with pytest.raises(BlockError, match='已有修改'):
        edits.restore_apply(doc, 'owner', plan['base_revision'], plan['restore_version'])
    assert service.snapshot(doc, 'owner')['blocks'][0]['content'] == 'New human edit'


class Provider:
    async def review_block(self, context):
        return BlockResult(block_id=context['block_id'], base_version=context['base_version'],
                           content=context['content'], summary='已同步', affected_blocks=[], spec_fragment=SpecFragment())

    async def review_document(self, context):
        return DocumentReview(document_id=context['document_id'], base_revision=context['base_revision'], summary='已审核', issues=[])


def test_text_rebase_preserves_unrelated_edits_and_rejects_ambiguous_anchor(service):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Keep this. Change this.')
    step = DocumentEditStep(action='replace_text', block_id=snap['blocks'][0]['id'], old_text='Change this.', new_text='Updated.')
    snap = manual(service, doc, snap['blocks'][0], 'Human addition. Keep this. Change this.')
    edits = DocumentEditService(service, None)
    plan = edits.rebase(doc, 'owner', [step])
    assert plan['preview'][0]['after'] == 'Human addition. Keep this. Updated.'
    snap = manual(service, doc, snap['blocks'][0], 'Change this. Change this.')
    with pytest.raises(BlockError, match='定位不唯一'):
        edits.rebase(doc, 'owner', [step])


@pytest.mark.asyncio
async def test_explicit_submission_review_syncs_fragments_without_changing_text(service):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Current text')
    with pytest.raises(BlockError, match='手动审核当前版本'):
        service.submit(doc, 'owner', snap['document']['revision'])
    ai = AIService(service, Provider())
    run = ai.enqueue_document_review(doc, 'owner', 'explicit-review', snap['document']['revision'], prepare_submission=True)
    await ai.tasks[run['id']]
    after = service.snapshot(doc, 'owner')
    assert after['blocks'][0]['content'] == 'Current text'
    assert after['blocks'][0]['fragment_version'] == after['blocks'][0]['version']
    assert after['document']['latest_version'] == snap['document']['latest_version']
    report = after['runs'][-1]
    assert report['status'] == 'completed' and report['apply_status'] == 'applied' and not report['stale']
    assert report['result']['elapsed_seconds'] >= 0


@pytest.mark.asyncio
async def test_automatic_fix_saves_but_does_not_create_review_receipt(service):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Old text')
    block = snap['blocks'][0]
    with service.write() as db:
        db.add(AgentRun(id='review', document_id=doc, type='document_review', status='completed',
            base_version=snap['document']['revision'], context_revision=snap['document']['context_revision'],
            dependency_versions={block['id']: block['version']}, request_id='seed', actor_id='owner', apply_status='applied',
            input_snapshot={}, result=DocumentReview(document_id=doc, base_revision=snap['document']['revision'], summary='需修改',
                issues=[DocumentReviewIssue(severity='warning', block_ids=[block['id']], description='需更新', suggestion='改正文')]).model_dump(mode='json')))

    class FixProvider(Provider):
        async def revise_document(self, context):
            return DocumentRevision(document_id=doc, base_revision=context['base_revision'], summary='已修订', blocks=[
                DocumentRevisionBlock(block_id=block['id'], base_version=block['version'], content='New text', summary='新正文', spec_fragment=SpecFragment())])

        async def review_document(self, _context):
            raise AssertionError('修订不能追加审核')

    ai = AIService(service, FixProvider())
    run = ai.enqueue_document_revision(doc, 'owner', 'review', 'fix', snap['document']['revision'])
    await ai.tasks[run['id']]
    after = service.snapshot(doc, 'owner')
    assert after['blocks'][0]['content'] == 'New text'
    assert len([r for r in after['runs'] if r['type'] == 'document_review']) == 1
    assert after['runs'][-1]['result']['review_pending'] is True
    assert after['runs'][0]['stale'] is True


@pytest.mark.asyncio
async def test_review_never_marks_concurrent_manual_content_as_verified(service):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Review this')

    class ConcurrentProvider(Provider):
        async def review_document(self, context):
            manual(service, doc, snap['blocks'][0], 'New manual content')
            return await super().review_document(context)

    ai = AIService(service, ConcurrentProvider())
    run = ai.enqueue_document_review(doc, 'owner', 'concurrent-review', snap['document']['revision'], prepare_submission=True)
    await ai.tasks[run['id']]
    after = service.snapshot(doc, 'owner')
    assert after['blocks'][0]['content'] == 'New manual content'
    assert after['blocks'][0]['fragment_version'] is None
    assert after['runs'][-1]['apply_status'] == 'conflict'


@pytest.mark.asyncio
async def test_completed_text_preview_has_no_fixed_typing_delay(service, monkeypatch):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    with service.write() as db:
        db.add(AgentRun(id='stream', document_id=doc, block_id=snap['blocks'][0]['id'], type='generate', status='running',
            base_version=1, context_revision=1, dependency_versions={}, request_id='stream', actor_id='owner',
            apply_status='pending', input_snapshot={}, result={}))
    delays = []

    async def yield_only(delay):
        delays.append(delay)

    monkeypatch.setattr('app.services.ai_service.asyncio.sleep', yield_only)
    ai = AIService(service, Provider())
    content = '完整句子。' * 300
    assert await ai.publish_block_stream('stream', content)
    run = service.snapshot(doc, 'owner')['runs'][-1]
    assert run['result']['stream_content'] == content
    assert run['result']['stream_complete'] is True
    assert len(delays) <= 8 and all(delay == 0 for delay in delays)
