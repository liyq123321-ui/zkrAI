import pytest
from app.ai.provider import DocumentEditPlan, DocumentEditStep, SpecFragment
from app.models import Block
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService
from app.services.document_workbench import DocumentWorkbench, reconcile_anchors
from app.services.ai_service import AIService
from tests.integration.test_block_prd import service, prepared, manual, seed_current_review
from tests.integration.test_prd_edit_optimizations import Provider


def test_anchor_move_keeps_identity_and_changed_text_does_not_guess():
    original = reconcile_anchors([], [{"id": "a", "title": "A", "content": "Unique paragraph\n\nSecond"}])
    moved = reconcile_anchors(original, [{"id": "b", "title": "Renamed", "content": "Unique paragraph"}])
    assert moved[0]["id"] == original[0]["id"] and moved[0]["block_id"] == "b"
    rewritten = reconcile_anchors(moved, [{"id": "b", "title": "Renamed", "content": "Different paragraph"}])
    assert rewritten[0]["id"] != original[0]["id"]
    assert next(a for a in rewritten if a["id"] == original[0]["id"])["missing"]


def test_workspace_collection_conflicts_and_health_do_not_write_document(service):
    snap = prepared(service, '# A\n# B')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'FR-001 登录方式待确认？')
    workbench = DocumentWorkbench(service, DocumentEditService(service, None))
    with service.write() as db:
        b = db.get(Block, snap['blocks'][0]['id'])
        b.fragment = SpecFragment(functional_requirements=[dict(requirement_id='FR-001', statement='登录', priority='MUST')]).model_dump()
        b.fragment_version = b.version
    data = workbench.inspect(doc, 'owner')
    assert any('FR-001' in reason for h in data['health'] for reason in h['reasons'])
    collected = workbench.collect(doc, 'owner', data['revision'])
    assert len(collected['state']['questions']) == 1
    with pytest.raises(BlockError, match='工作台已更新'):
        workbench.collect(doc, 'owner', data['revision'])
    again = workbench.collect(doc, 'owner', collected['revision'])
    assert len(again['state']['questions']) == 1
    after = service.snapshot(doc, 'owner')
    assert after['document']['revision'] == snap['document']['revision']
    assert after['document']['latest_version'] == snap['document']['latest_version']
    with pytest.raises(BlockError): workbench.inspect(doc, 'stranger')


@pytest.mark.asyncio
async def test_alternatives_are_persistent_preview_only_then_single_version_apply(service):
    snap = prepared(service, '# A')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Original')
    class Planner:
        async def plan_chat_edit(self, context):
            return DocumentEditPlan(summary=context['instruction'], steps=[DocumentEditStep(
                action='replace_text', block_id=snap['blocks'][0]['id'], old_text='Original', new_text=context['instruction'])])
    edits = DocumentEditService(service, Planner())
    workbench = DocumentWorkbench(service, edits)
    a = await workbench.plan(doc, 'owner', 'alternative', 'Option A', [snap['blocks'][0]['id']], [])
    await workbench.plan(doc, 'owner', 'alternative', 'Option B', [snap['blocks'][0]['id']], [])
    assert service.snapshot(doc, 'owner')['blocks'][0]['content'] == 'Original'
    current = edits.apply(doc, 'owner', a['base_revision'], [DocumentEditStep.model_validate(s) for s in a['steps']], a['alternative_id'])
    assert current['document']['latest_version'] == snap['document']['latest_version'] + 1
    data = workbench.inspect(doc, 'owner')
    assert [a['status'] for a in data['state']['alternatives']] == ['applied', 'draft']
    with pytest.raises(BlockError):
        edits.apply(doc, 'owner', a['base_revision'], [DocumentEditStep.model_validate(s) for s in a['steps']], a['alternative_id'])
    meeting = workbench.meeting(doc, 'owner', snap['document']['latest_version'], current['document']['latest_version'])
    assert '正文' in meeting['markdown'] and 'Option B' in meeting['markdown']


@pytest.mark.asyncio
async def test_incremental_review_uses_affected_scope_but_never_passes_full_gate(service):
    snap = prepared(service, '# A\n# B')
    doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Original')
    snap = manual(service, doc, snap['blocks'][1], 'Unrelated')
    seed_current_review(service, snap)
    snap = manual(service, doc, snap['blocks'][0], 'Changed')
    class Capture(Provider):
        async def review_document(self, context):
            self.context = context
            return await super().review_document(context)
    provider = Capture()
    ai = AIService(service, provider)
    run = ai.enqueue_document_review(doc, 'owner', 'affected-review', snap['document']['revision'], scope='affected')
    await ai.tasks[run['id']]
    assert provider.context['affected_block_ids'] == [snap['blocks'][0]['id']]
    assert not provider.context['prepare_submission']
    result = service.snapshot(doc, 'owner')['runs'][-1]
    assert result['status'] == 'completed' and result['review_scope'] == 'affected'
    with service.factory() as db:
        document = service.document(db, doc, 'owner')
        assert service._clean_document_review(db, document, service.blocks(db, document)) is None
