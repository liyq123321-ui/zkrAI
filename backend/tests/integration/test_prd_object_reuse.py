import pytest
from app.ai.provider import DocumentEditPlan, DocumentEditStep
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService
from app.services.document_jobs import DocumentJobs
from app.services.document_workbench import DocumentWorkbench
from app.services.document_candidate_reuse import reuse
from tests.integration.test_block_prd import service, prepared, manual

TABLE = '| 编号 | 优先级 | 说明 |\n| --- | --- | --- |\n| FR1 | P0 | old |\n| FR2 | P2 | same |'


class Planner:
    def __init__(self, row=1): self.row = row
    async def plan_chat_edit(self, context):
        return DocumentEditPlan(summary='改优先级', steps=[DocumentEditStep(action='table_set',
            block_id=context['blocks'][0]['id'], row=self.row, column='优先级', value='P1')])


async def candidate(service, object_target=False):
    snap = prepared(service, '# A\n# B')
    snap = manual(service, snap['document']['id'], snap['blocks'][0], TABLE)
    edits = DocumentEditService(service, Planner())
    jobs = DocumentJobs(DocumentWorkbench(service, edits))
    payload = {'kind': 'chat', 'instruction': '改成 P1', 'target_ids': [snap['blocks'][0]['id']]}
    if object_target:
        payload['object_target'] = {'kind': 'cell', 'block_id': snap['blocks'][0]['id'], 'block_version': snap['blocks'][0]['version'],
            'headers': ['编号', '优先级', '说明'], 'key_column': '编号', 'key_value': 'FR1', 'column': '优先级'}
    job = jobs.enqueue(snap['document']['id'], 'owner', 'plan', payload, snap['document']['revision'])
    await jobs.tasks[job['id']]
    assert jobs.get(snap['document']['id'], 'owner', job['id'])['status'] == 'ready'
    return snap, edits, jobs, job


@pytest.mark.asyncio
async def test_reuses_sorted_row_preserving_manual_cells_and_one_version(service):
    snap, edits, jobs, job = await candidate(service, True)
    doc = snap['document']['id']
    changed = manual(service, doc, snap['blocks'][0], TABLE.replace('| FR1 | P0 | old |\n| FR2 | P2 | same |', '| FR2 | P2 | same |\n| FR1 | P0 | human |'))
    plan = reuse(edits, doc, 'owner', job['id'], [0])
    assert plan['steps'][0]['row'] == 2 and not plan['reuse_conflicts']
    assert service.snapshot(doc, 'owner')['document']['revision'] == changed['document']['revision']
    result = edits.apply(doc, 'owner', plan['base_revision'], [DocumentEditStep.model_validate(s) for s in plan['steps']], job_id=plan['job_id'])
    assert '| FR1 | P1 | human |' in result['blocks'][0]['content']
    assert '| FR2 | P2 | same |' in result['blocks'][0]['content']
    assert result['document']['latest_version'] == changed['document']['latest_version'] + 1
    with pytest.raises(BlockError): reuse(edits, doc, 'stranger', job['id'], [0])


@pytest.mark.asyncio
async def test_same_cell_conflict_requires_explicit_choice_and_remains_version_guarded(service):
    snap, edits, jobs, job = await candidate(service)
    doc = snap['document']['id']
    changed = manual(service, doc, snap['blocks'][0], TABLE.replace('FR1 | P0', 'FR1 | P3'))
    conflict = reuse(edits, doc, 'owner', job['id'], [0])
    assert not conflict['steps'] and conflict['reuse_conflicts'][0]['can_take']
    chosen = reuse(edits, doc, 'owner', job['id'], [0], [0])
    assert chosen['steps'] and not chosen['reuse_conflicts']
    latest = manual(service, doc, changed['blocks'][1], 'Other chapter changed')
    with pytest.raises(BlockError): edits.apply(doc, 'owner', chosen['base_revision'], [DocumentEditStep.model_validate(s) for s in chosen['steps']], job_id=chosen['job_id'])
    assert 'FR1 | P3' in latest['blocks'][0]['content']


@pytest.mark.asyncio
async def test_explicit_cell_rejects_neighbour_modification_and_stale_context(service):
    snap = prepared(service, '# A')
    snap = manual(service, snap['document']['id'], snap['blocks'][0], TABLE)
    block = snap['blocks'][0]
    target = {'kind': 'cell', 'block_id': block['id'], 'block_version': block['version'], 'headers': ['编号', '优先级', '说明'],
              'key_column': '编号', 'key_value': 'FR1', 'column': '优先级'}
    edits = DocumentEditService(service, Planner(2))
    with pytest.raises(BlockError, match='所选业务行之外'): await edits.plan(snap['document']['id'], 'owner', '改成 P1', [block['id']], object_target=target)
    target['block_version'] -= 1
    with pytest.raises(BlockError, match='来源版本'): await edits.plan(snap['document']['id'], 'owner', '改成 P1', [block['id']], object_target=target)
