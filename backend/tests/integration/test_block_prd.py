import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from app.database.database import create_engine_for_url, init_database, make_session_factory
from app.database.models import Project, SpecVersion, SpecReview
from app.models import Block, AgentRun, DocumentVersion
from app.services.block_service import BlockService, BlockError
from app.services.ai_service import AIService
from app.services.prd_templates import parse_outline, validate_plan
from app.ai.provider import (BlockPlan, BlockResult, SpecFragment, DocumentReview, DocumentReviewIssue,
                             DocumentRevision, DocumentRevisionBlock)
from tests.helpers.factories import make_valid_spec
from main import create_app


@pytest.fixture
def service(tmp_path):
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'blocks.db'}")
    init_database(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        db.add(Project(id='p', session_id='s', creation_request_id='req', brief={'motivation': 'Only test background'},
                       final_approver='owner', phase='SPECIFICATION'))
        db.commit()
    yield BlockService(factory)
    engine.dispose()


def prepared(service, outline='# Upstream\n# Target\n# Sibling'):
    snap = service.open('s', 'owner')
    return service.import_outline(snap['document']['id'], 'owner', 'custom', outline, snap['document']['revision'])


def manual(service, doc, block, content, **extra):
    return service.save(doc, block['id'], 'owner', block['version'], {'content': content, **extra})


def seed_current_review(service, snap):
    """Synthetic review evidence for submission fixtures only."""
    with service.write() as db:
        db.add(AgentRun(id='fixture-current-review', document_id=snap['document']['id'], type='document_review',
            status='completed', apply_status='applied', actor_id='owner', request_id='fixture-current-review',
            base_version=snap['document']['revision'], context_revision=snap['document']['context_revision'],
            dependency_versions={b['id']: b['version'] for b in snap['blocks']}, input_snapshot={},
            result={'document_id': snap['document']['id'], 'base_revision': snap['document']['revision'],
                    'summary': 'Synthetic current review', 'issues': []}))


class DelayedProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.contexts = []
        self.bad_id = False
        self.bad_range = False
        self.full_fragment = False

    async def answer(self, context, review=False):
        self.contexts.append(context)
        self.started.set()
        await self.release.wait()
        return BlockResult(block_id='wrong' if self.bad_id else context['block_id'], base_version=context['base_version'],
                           content=context['content'] if review else ('BAD RANGE' if self.bad_range else 'AI content'),
                           summary='AI summary', affected_blocks=[],
                           spec_fragment=SpecFragment.model_validate(make_valid_spec().model_dump()) if self.full_fragment else SpecFragment())

    async def generate_block(self, context): return await self.answer(context)
    async def revise_block(self, context): return await self.answer(context)
    async def review_block(self, context): return await self.answer(context, True)
    async def review_document(self, context):
        self.contexts.append(context)
        self.started.set()
        await self.release.wait()
        return DocumentReview(document_id=context['document_id'], base_revision=context['base_revision'],
                              summary='全文存在跨模块不一致，需要明确验收标准。',
                              issues=[DocumentReviewIssue(severity='warning',
                                      block_ids=['unknown'] if self.bad_id else [b['id'] for b in context['blocks'][:2]],
                                      description='前后两个模块要求不一致', suggestion='统一两个模块的验收标准')])
    async def plan_outline(self, context):
        self.started.set()
        await self.release.wait()
        return parse_outline('# Planned\n## Child')


async def finish(ai, provider, run):
    task = ai.tasks[run['id']]
    await provider.started.wait()
    provider.release.set()
    await task


def test_open_idempotent_and_template_sources(service):
    one = service.open('s', 'owner')
    two = service.open('s', 'owner')
    assert one['document']['id'] == two['document']['id']
    assert [b['id'] for b in one['blocks']] == [b['id'] for b in two['blocks']]
    assert '需求明细' in {b['title'] for b in one['blocks']}
    assert not any(b['content'] for b in one['blocks'])
    assert len({b['id'] for b in one['blocks']}) == len(one['blocks'])


def test_outline_fences_duplicates_cycles_and_missing_refs():
    plan = parse_outline('# A\n```md\n# Not a heading\n```\n## B\n# A')
    assert [b.title for b in plan.blocks] == ['A', 'B', 'A']
    assert plan.blocks[1].parent_key == plan.blocks[0].key
    plan.blocks[0].dependencies = [plan.blocks[1].key]
    plan.blocks[1].dependencies = [plan.blocks[0].key]
    with pytest.raises(ValueError, match='循环'): validate_plan(plan)
    plan.blocks[1].dependencies = ['missing']
    with pytest.raises(ValueError, match='引用'): validate_plan(plan)


def test_atomic_save_history_restore_and_noop(service):
    snap = prepared(service)
    doc, block = snap['document']['id'], snap['blocks'][0]
    a = manual(service, doc, block, 'Human')
    updated = a['blocks'][0]
    assert updated['version'] == 2
    assert manual(service, doc, updated, 'Human')['blocks'][0]['version'] == 2
    with pytest.raises(BlockError): manual(service, doc, block, 'Stale')
    restored = service.restore(doc, block['id'], 'owner', 1, 2)
    assert restored['blocks'][0]['version'] == 3
    assert restored['blocks'][0]['content'] == ''
    assert [v['version'] for v in service.versions(doc, block['id'], 'owner')] == [3, 2, 1]


def test_two_sqlite_writers_cannot_overwrite_each_other(service):
    snap = prepared(service)
    doc, block = snap['document']['id'], snap['blocks'][0]
    def write(content):
        try:
            manual(service, doc, block, content)
            return 'saved'
        except BlockError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, ['one', 'two'])) == ['conflict', 'saved']
    assert len(service.versions(doc, block['id'], 'owner')) == 2


def test_transitive_invalidation_and_current_summary(service):
    snap = prepared(service)
    doc, a, b, c = snap['document']['id'], *snap['blocks']
    manual(service, doc, a, 'A')
    manual(service, doc, b, 'B', dependencies=[a['id']])
    manual(service, doc, c, 'C', dependencies=[b['id']])
    a = service.snapshot(doc, 'owner')['blocks'][0]
    changed = manual(service, doc, a, 'New background')
    assert [b['status'] for b in changed['blocks']] == ['ready', 'outdated', 'outdated']
    assert changed['blocks'][0]['summary'] == 'New background'


def test_authorization_cross_document_and_comments(service):
    snap = prepared(service)
    doc, a, b = snap['document']['id'], *snap['blocks'][:2]
    with pytest.raises(BlockError) as forbidden: service.snapshot(doc, 'intruder')
    assert forbidden.value.status == 403
    with pytest.raises((ValueError, BlockError)): manual(service, doc, a, 'A', dependencies=['unknown'])
    a = manual(service, doc, a, 'one\ntwo\nthree')['blocks'][0]
    targets = [{'block_id': a['id'], 'base_version': a['version'], 'start_line': 2, 'end_line': 2}, {'block_id': b['id'], 'base_version': b['version']}]
    comments = service.comment(doc, 'owner', 'change', targets)['comments']
    assert comments[0]['targets'][0]['quote'] == 'two\n'
    deletion_comments = service.comment(doc, 'owner', 'remove selection', [{
        'block_id': a['id'], 'base_version': a['version'], 'start_line': 2, 'end_line': 2,
        'selected_text': 'two', 'action': 'delete',
    }])['comments']
    deletion = next(comment for comment in deletion_comments if comment['text'] == 'remove selection')
    assert deletion['targets'][0]['quote'] == 'two'
    assert deletion['targets'][0]['action'] == 'delete'
    with pytest.raises(BlockError):
        service.comment(doc, 'owner', 'stale selection', [{
            'block_id': a['id'], 'base_version': a['version'], 'start_line': 2, 'end_line': 2,
            'selected_text': 'missing text', 'action': 'comment',
        }])
    with pytest.raises(BlockError): service.comment(doc, 'owner', 'bad', [{'block_id': a['id'], 'base_version': a['version'], 'start_line': 2, 'end_line': 8}])
    with pytest.raises(BlockError): service.import_outline(doc, 'owner', 'tencent', '', service.snapshot(doc, 'owner')['document']['revision'])


@pytest.mark.asyncio
async def test_single_block_whitelisted_context_candidate_apply(service):
    snap = prepared(service)
    doc, a, b, sibling = snap['document']['id'], *snap['blocks']
    manual(service, doc, a, 'Upstream body secret')
    manual(service, doc, b, 'Target text', dependencies=[a['id']])
    manual(service, doc, sibling, 'Sibling secret')
    with service.write() as db:
        db.get(Block, a['id']).summary = 'Safe short summary'
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'revise', 'r', b['id'], 'Improve target')
    assert ai.enqueue(doc, 'owner', 'revise', 'r', b['id'])['id'] == run['id']
    assert ai.enqueue(doc, 'owner', 'generate', 'other', b['id'])['id'] == run['id']
    await finish(ai, provider, run)
    context = provider.contexts[0]
    assert 'Sibling secret' not in str(context) and 'Upstream body secret' not in str(context)
    assert context['upstream_summaries'][0]['summary'] == 'Safe short summary'
    before = service.snapshot(doc, 'owner')
    assert before['blocks'][1]['content'] == 'Target text'
    ai.apply(doc, run['id'], 'owner', 2)
    after = service.snapshot(doc, 'owner')
    assert after['blocks'][1]['content'] == 'AI content'
    assert after['blocks'][2]['content'] == 'Sibling secret'
    assert after['blocks'][1]['version'] == 3
    ai.apply(doc, run['id'], 'owner', 3)
    assert service.snapshot(doc, 'owner')['blocks'][1]['version'] == 3


@pytest.mark.asyncio
async def test_ai_cannot_overwrite_manual_and_force_is_still_cas(service):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'generate', 'r', b['id'])
    await provider.started.wait()
    manual(service, doc, b, 'Human v2')
    await finish(ai, provider, run)
    snap = service.snapshot(doc, 'owner')
    assert snap['runs'][0]['apply_status'] == 'conflict'
    assert snap['blocks'][0]['content'] == 'Human v2'
    assert snap['blocks'][0]['status'] == 'ready'
    with pytest.raises(BlockError): ai.apply(doc, run['id'], 'owner', 2)
    with pytest.raises(BlockError): ai.apply(doc, run['id'], 'owner', 1, force=True)
    ai.apply(doc, run['id'], 'owner', 2, force=True)
    assert service.snapshot(doc, 'owner')['blocks'][0]['version'] == 3


@pytest.mark.asyncio
async def test_upstream_change_during_generation_conflicts(service):
    snap = prepared(service); doc, a, b = snap['document']['id'], *snap['blocks'][:2]
    a = manual(service, doc, a, 'old')['blocks'][0]
    b = manual(service, doc, b, 'target', dependencies=[a['id']])['blocks'][1]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'generate', 'r', b['id'])
    await provider.started.wait()
    manual(service, doc, a, 'new')
    await finish(ai, provider, run)
    with pytest.raises(BlockError): ai.apply(doc, run['id'], 'owner', b['version'])
    assert service.snapshot(doc, 'owner')['blocks'][1]['status'] == 'outdated'


@pytest.mark.asyncio
async def test_cancelled_run_never_applies_late_result(service):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'generate', 'r', b['id'])
    await provider.started.wait()
    task = ai.tasks[run['id']]
    ai.cancel(doc, run['id'], 'owner')
    provider.release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert service.snapshot(doc, 'owner')['runs'][0]['status'] == 'cancelled'
    with pytest.raises(BlockError): ai.apply(doc, run['id'], 'owner', 1, True)
    assert service.snapshot(doc, 'owner')['blocks'][0]['content'] == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['bad_id', 'bad_range'])
async def test_invalid_ai_result_rejected(service, failure):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    b = manual(service, doc, b, 'first\nselected\nlast')['blocks'][0]
    provider = DelayedProvider(); setattr(provider, failure, True); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'revise', 'r', b['id'], 'adjust', {'start_line': 2, 'end_line': 2})
    await finish(ai, provider, run)
    assert service.snapshot(doc, 'owner')['runs'][0]['status'] == 'failed'
    assert service.snapshot(doc, 'owner')['blocks'][0]['content'] == b['content']


@pytest.mark.asyncio
async def test_plan_guard_and_restart_recovery(service):
    snap = prepared(service); doc = snap['document']['id']
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'plan', 'r')
    await finish(ai, provider, run)
    assert [b['title'] for b in service.snapshot(doc, 'owner')['blocks']] == ['Planned', 'Child']
    provider = DelayedProvider(); ai = AIService(service, provider)
    b = service.snapshot(doc, 'owner')['blocks'][0]
    run = ai.enqueue(doc, 'owner', 'generate', 'r2', b['id'])
    await provider.started.wait()
    ai.recover()
    await finish(ai, provider, run)
    assert service.snapshot(doc, 'owner')['runs'][-1]['error'].startswith('PROCESS_INTERRUPTED')


@pytest.mark.asyncio
async def test_block_review_and_snapshot_matches_downstream(service):
    snap = prepared(service, '# Complete specification')
    doc, b = snap['document']['id'], snap['blocks'][0]
    b = manual(service, doc, b, 'Human canonical content')['blocks'][0]
    with pytest.raises(BlockError): service.submit(doc, 'owner', service.snapshot(doc, 'owner')['document']['revision'])
    provider = DelayedProvider(); provider.full_fragment = True; ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'review', 'r', b['id'])
    await finish(ai, provider, run)
    ai.apply(doc, run['id'], 'owner', b['version'])
    snap = service.snapshot(doc, 'owner')
    assert snap['blocks'][0]['version'] == b['version']
    seed_current_review(service, snap)
    submitted = service.submit(doc, 'owner', snap['document']['revision'])
    # The submit result carries the project so the HTTP layer can start the
    # HTML prototype without a second lookup (see test_prd_submit_prototype).
    assert submitted['project_id'] == 'p'
    assert service.submit(doc, 'owner', snap['document']['revision']) == submitted
    with service.factory() as db:
        spec = db.get(SpecVersion, submitted['spec_version_id'])
        project = db.get(Project, 'p')
        assert spec.status == 'HUMAN_REVIEW'
        assert project.current_spec_version_id == spec.id
        assert spec.content['functional_requirements'] == make_valid_spec().model_dump()['functional_requirements']
        assert 'Human canonical content' in spec.markdown
        assert spec.content['source_refs'] == [f"block:{b['id']}:v{b['version']}"]


def test_current_clean_whole_review_allows_submission_despite_fragment_rule_findings(service):
    snap = prepared(service, '# Complete specification')
    doc, block = snap['document']['id'], snap['blocks'][0]
    snap = manual(service, doc, block, 'A complete document reviewed as a whole')
    block = snap['blocks'][0]
    payload = make_valid_spec().model_dump(mode='json')
    payload['acceptance_criteria'] = payload['acceptance_criteria'][:1]
    payload['permissions_and_responsibilities'] = ['Page user']
    with service.write() as db:
        stored = db.get(Block, block['id'])
        stored.fragment = SpecFragment.model_validate(payload).model_dump(mode='json')
        stored.fragment_version = stored.version
        db.add(AgentRun(
            id='clean-review', document_id=doc, block_id=None, type='document_review', status='completed',
            base_version=snap['document']['revision'], input_snapshot={'document_id': doc},
            dependency_versions={block['id']: block['version']},
            context_revision=snap['document']['context_revision'], result={
                'document_id': doc, 'base_revision': snap['document']['revision'],
                'summary': 'No pending whole-document issues.', 'issues': [],
            }, apply_status='applied', error=None, request_id='clean-review-request', actor_id='owner',
        ))

    result = service.submit(doc, 'owner', snap['document']['revision'])

    with service.factory() as db:
        spec = db.get(SpecVersion, result['spec_version_id'])
        reviews = db.query(SpecReview).filter_by(spec_version_id=spec.id).all()
        assert spec.status == 'HUMAN_REVIEW'
        assert {(review.kind, review.verdict) for review in reviews} == {('RULE', 'PASS'), ('AGENT', 'PASS')}
        assert all(not review.findings for review in reviews)
        assert next(review for review in reviews if review.kind == 'RULE').comments.startswith(
            'CURRENT_DOCUMENT_REVIEW_CLEARANCE:clean-review'
        )


def test_http_identity_validation_and_routes(service, test_settings):
    from dataclasses import replace
    settings = replace(test_settings, identity_mode='local', identity_actor_id='owner')
    app = create_app(settings=settings, session_factory=service.factory, auto_bind_prd_review=False, block_provider=DelayedProvider())
    with TestClient(app) as client:
        opened = client.post('/prd-documents', json={'session_id': 's'})
        assert opened.status_code == 200
        snap = opened.json(); doc = snap['document']['id']; b = snap['blocks'][0]
        invalid = client.put(f'/prd-documents/{doc}/blocks/{b["id"]}', json={'content': 'x'})
        assert invalid.status_code == 422
        assert client.get(f'/prd-documents/{doc}').status_code == 200
        assert client.get(f'/prd-documents/{doc}/blocks/{b["id"]}/history').status_code == 200
        versions = client.get(f'/prd-documents/{doc}/versions')
        assert versions.status_code == 200
        assert client.get(f'/prd-documents/{doc}/versions/{versions.json()[0]["version"]}').status_code == 200
        assert client.get('/prd-documents/templates').status_code == 200


def test_document_versions_capture_whole_document_after_each_save(service):
    snap = prepared(service)
    doc = snap['document']['id']
    before = service.document_versions(doc, 'owner')
    assert before and before[0]['version'] == len(before)
    changed = manual(service, doc, snap['blocks'][0], '新的全文内容')
    versions = service.document_versions(doc, 'owner')
    assert versions[0]['version'] == before[0]['version'] + 1
    assert versions[0]['created_at']
    latest = service.document_version(doc, versions[0]['version'], 'owner')
    assert latest['snapshot']['blocks'][0]['content'] == '新的全文内容'
    assert len(latest['snapshot']['blocks']) == len(changed['blocks'])
    assert service.document_version(doc, before[0]['version'], 'owner')['snapshot']['blocks'][0]['content'] == ''


def test_document_versions_rebuild_existing_block_history(service):
    snap = prepared(service)
    doc = snap['document']['id']
    manual(service, doc, snap['blocks'][0], '历史修改')
    with service.factory() as db:
        db.query(DocumentVersion).filter_by(document_id=doc).delete()
        db.commit()
    versions = service.document_versions(doc, 'owner')
    assert len(versions) >= 2
    assert service.document_version(doc, versions[0]['version'], 'owner')['snapshot']['blocks'][0]['content'] == '历史修改'


@pytest.mark.asyncio
async def test_plan_response_does_not_discard_edit_made_while_planning(service):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'plan', 'r')
    await provider.started.wait()
    manual(service, doc, b, 'Keep this draft')
    await finish(ai, provider, run)
    after = service.snapshot(doc, 'owner')
    assert after['blocks'][0]['id'] == b['id']
    assert after['blocks'][0]['content'] == 'Keep this draft'
    assert after['runs'][0]['apply_status'] == 'conflict'


@pytest.mark.asyncio
async def test_global_context_revision_invalidates_running_result(service):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'generate', 'r', b['id'])
    await provider.started.wait()
    service.context(doc, 'owner', snap['document']['revision'], 'Changed background', 'New rules')
    await finish(ai, provider, run)
    with pytest.raises(BlockError): ai.apply(doc, run['id'], 'owner', b['version'])


@pytest.mark.asyncio
async def test_retry_keeps_feedback_and_uses_current_version(service):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'revise', 'r', b['id'], 'Only clarify wording')
    await finish(ai, provider, run)
    b = manual(service, doc, b, 'Human new text')['blocks'][0]
    retried = ai.retry(doc, run['id'], 'owner', 'retry')
    task = ai.tasks[retried['id']]
    await task
    assert provider.contexts[-1]['feedback'] == 'Only clarify wording'
    assert provider.contexts[-1]['base_version'] == b['version']
    assert provider.contexts[-1]['content'] == 'Human new text'


def test_existing_spec_import_keeps_text_and_does_not_duplicate(service):
    from tests.helpers.factories import make_valid_spec
    with service.write() as db:
        db.add(SpecVersion(id='old', project_id='p', revision=1, content=make_valid_spec().model_dump(),
                          markdown='# Existing\nkeep original\n\n## Feature\nunchanged\n', generation_source='LEGACY',
                          input_refs=[], generator_agent_session_id='old-agent', generator_call_id='old-call',
                          change_summary='old', content_hash='old', status='HUMAN_REVIEW'))
        db.get(Project, 'p').current_spec_version_id = 'old'
    snap = service.open('s', 'owner')
    assert 'keep original' in snap['blocks'][0]['content']
    assert 'unchanged' in snap['blocks'][1]['content']
    assert service.open('s', 'owner')['blocks'] == snap['blocks']
    with service.factory() as db:
        assert db.get(SpecVersion, 'old').markdown == '# Existing\nkeep original\n\n## Feature\nunchanged\n'


def test_pdf_import_and_outline_preview(service, test_settings):
    from dataclasses import replace
    import base64
    settings = replace(test_settings, identity_mode='local', identity_actor_id='owner')
    app = create_app(settings=settings, session_factory=service.factory, auto_bind_prd_review=False, block_provider=DelayedProvider())
    with TestClient(app) as client:
        snap = client.post('/prd-documents', json={'session_id': 's'}).json()
        doc = snap['document']['id']
        result = client.post(f'/prd-documents/{doc}/import', json={
            'filename': 'outline.md', 'expected_revision': snap['document']['revision'],
            'content_base64': base64.b64encode('# 背景\n## 子章节'.encode()).decode(),
        })
        assert result.status_code == 200
        assert result.json()['outline'] == '# 背景\n## 子章节'
        from pypdf import PdfWriter
        import io
        out = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=300, height=300); writer.write(out)
        empty = client.post(f'/prd-documents/{doc}/import', json={
            'filename': 'scan.pdf', 'expected_revision': snap['document']['revision'],
            'content_base64': base64.b64encode(out.getvalue()).decode(),
        })
        assert empty.status_code == 422
        assert '没有可提取文字' in empty.json()['detail']['message']
        invalid = client.put(f'/prd-documents/{doc}/outline', json={'outline': '', 'template': 'custom', 'expected_revision': snap['document']['revision']})
        assert invalid.status_code == 422


def test_canonical_legacy_import_preserves_hierarchy_and_structured_fields(service):
    from app.services.spec_service import _render_markdown
    payload = make_valid_spec().model_dump(mode='json')
    with service.write() as db:
        db.add(SpecVersion(id='old', project_id='p', revision=1, content=payload, markdown=_render_markdown(payload),
                          generation_source='LEGACY', input_refs=payload['source_refs'],
                          generator_agent_session_id='old-agent', generator_call_id='old-call',
                          change_summary='old', content_hash='old', status='HUMAN_REVIEW'))
        db.get(Project, 'p').current_spec_version_id = 'old'
        db.get(Project, 'p').phase = 'REVIEW'
    snap = service.open('s', 'owner')
    assert snap['blocks'][1]['parent_id'] == snap['blocks'][0]['id']
    assert snap['blocks'][1]['fragment']['background_and_goals'] == payload['background_and_goals']
    seed_current_review(service, snap)
    result = service.submit(snap['document']['id'], 'owner', snap['document']['revision'])
    with service.factory() as db:
        spec = db.get(SpecVersion, result['spec_version_id'])
        assert spec.content['functional_requirements'] == payload['functional_requirements']
        assert spec.markdown.count('## Background and Goals') == 1


@pytest.mark.asyncio
async def test_cancel_completion_race_prevents_candidate_application(service):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue(doc, 'owner', 'generate', 'r', b['id'])
    await finish(ai, provider, run)
    ai.cancel(doc, run['id'], 'owner')
    with pytest.raises(BlockError): ai.apply(doc, run['id'], 'owner', b['version'], force=True)
    assert service.snapshot(doc, 'owner')['blocks'][0]['content'] == ''


def initial_run(service, doc):
    return next(r for r in service.snapshot(doc, 'owner')['runs'] if r['type'] == 'initial')


@pytest.mark.asyncio
async def test_initial_generation_is_sequential_dependency_ordered_and_durable(service):
    snap = prepared(service)
    doc, a, b, c = snap['document']['id'], *snap['blocks']
    # The first directory entry depends on the last: generation must reorder them.
    with service.write() as db:
        db.get(Block, a['id']).dependencies = [c['id']]
        db.get(Block, b['id']).dependencies = [a['id']]
    provider = DelayedProvider(); ai = AIService(service, provider)
    ai.start_initial(doc, 'owner')
    parent = initial_run(service, doc); task = ai.tasks[parent['id']]
    await provider.started.wait()
    assert len(provider.contexts) == 1
    assert provider.contexts[0]['block_id'] == c['id']
    for _ in range(3):
        reopened = service.open('s', 'owner')
        ai.start_initial(reopened['document']['id'], 'owner')
    assert len([r for r in service.snapshot(doc, 'owner')['runs'] if r['type'] == 'initial']) == 1
    provider.release.set()
    await task  # No browser applies results or schedules the following blocks.
    assert [ctx['block_id'] for ctx in provider.contexts] == [c['id'], a['id'], b['id']]
    assert provider.contexts[1]['upstream_summaries'] == [{'block_id': c['id'], 'summary': 'AI summary'}]
    after = service.snapshot(doc, 'owner')
    assert all(b['content'] == 'AI content' and b['version'] == 2 for b in after['blocks'])
    assert initial_run(service, doc)['result'] == {'completed': 3, 'total': 3}
    assert initial_run(service, doc)['status'] == 'completed'
    # Reconstructing the service (or later clearing the document) cannot retrigger.
    fresh = AIService(service, provider)
    fresh.start_initial(doc, 'owner')
    assert not fresh.tasks
    assert len(provider.contexts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', ['body', 'cleared', 'previous_job', 'legacy', 'submitted'])
async def test_initial_generation_never_starts_for_existing_work(service, existing):
    snap = prepared(service); doc, b = snap['document']['id'], snap['blocks'][0]
    provider = DelayedProvider(); ai = AIService(service, provider)
    if existing in ('body', 'cleared'):
        current = manual(service, doc, b, 'Human draft')['blocks'][0]
        if existing == 'cleared': manual(service, doc, current, '')
    elif existing == 'previous_job':
        run = ai.enqueue(doc, 'owner', 'generate', 'old', b['id'])
        await finish(ai, provider, run)
    else:
        from app.models import Document
        with service.write() as db:
            setattr(db.get(Document, doc), 'source_spec_version_id' if existing == 'legacy' else 'submitted_spec_id', 'existing-spec')
    ai.start_initial(doc, 'owner')
    assert not any(r['type'] == 'initial' for r in service.snapshot(doc, 'owner')['runs'])


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['cancel_parent', 'cancel_child', 'provider_failure', 'edit_current', 'edit_future', 'context'])
async def test_initial_generation_stops_without_overwriting_or_restarting(service, stop):
    snap = prepared(service); doc, a, b = snap['document']['id'], *snap['blocks'][:2]
    provider = DelayedProvider(); ai = AIService(service, provider)
    ai.start_initial(doc, 'owner')
    parent = initial_run(service, doc); task = ai.tasks[parent['id']]
    await provider.started.wait()
    with pytest.raises(BlockError, match='首版正在连续生成'):
        ai.enqueue(doc, 'owner', 'generate', 'manual', b['id'])
    with pytest.raises(BlockError):
        service.import_outline(doc, 'owner', 'alibaba', '', snap['document']['revision'])
    if stop.startswith('cancel'):
        child = next(r for r in service.snapshot(doc, 'owner')['runs'] if r['type'] == 'generate')
        ai.cancel(doc, parent['id'] if stop == 'cancel_parent' else child['id'], 'owner')
    elif stop == 'provider_failure': provider.bad_id = True
    elif stop == 'edit_current': manual(service, doc, a, 'Keep human input')
    elif stop == 'edit_future': manual(service, doc, b, 'Keep human input')
    else: service.context(doc, 'owner', snap['document']['revision'], 'New context', '')
    provider.release.set()
    await asyncio.gather(task, return_exceptions=True)
    after = service.snapshot(doc, 'owner')
    assert initial_run(service, doc)['status'] in ('failed', 'cancelled')
    assert len(provider.contexts) == 1
    if stop.startswith('edit'):
        assert after['blocks'][0 if stop == 'edit_current' else 1]['content'] == 'Keep human input'
    else:
        assert not any(block['content'] for block in after['blocks'])
    ai.start_initial(doc, 'owner')
    assert len([r for r in service.snapshot(doc, 'owner')['runs'] if r['type'] == 'initial']) == 1
    assert len(provider.contexts) == 1
    await ai.close()


def test_http_editor_entry_starts_first_draft_once_and_enforces_identity(service, test_settings):
    from dataclasses import replace
    settings = replace(test_settings, identity_mode='local', identity_actor_id='owner')
    app = create_app(settings=settings, session_factory=service.factory, auto_bind_prd_review=False, block_provider=DelayedProvider())
    with TestClient(app) as client:
        one = client.post('/prd-documents', json={'session_id': 's', 'auto_generate': True})
        assert one.status_code == 200
        two = client.post('/prd-documents', json={'session_id': 's', 'auto_generate': True})
        first = [r for r in one.json()['runs'] if r['type'] == 'initial']
        second = [r for r in two.json()['runs'] if r['type'] == 'initial']
        assert len(first) == len(second) == 1 and first[0]['id'] == second[0]['id']
        with pytest.raises(BlockError) as denied:
            app.state.block_ai.start_initial(one.json()['document']['id'], 'intruder')
        assert denied.value.status == 403
    # A restart leaves an explicit interruption instead of launching a second draft.
    ai = AIService(service, DelayedProvider())
    ai.recover()
    ai.start_initial(one.json()['document']['id'], 'owner')
    assert not ai.tasks


@pytest.mark.asyncio
async def test_whole_review_reads_all_blocks_without_modifying_prd(service):
    snap = prepared(service)
    doc = snap['document']['id']
    for b in snap['blocks']:
        manual(service, doc, b, 'Original ' + b['title'])
    before = service.snapshot(doc, 'owner')
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue_document_review(doc, 'owner', 'whole', before['document']['revision'])
    assert ai.enqueue_document_review(doc, 'owner', 'whole', before['document']['revision'])['id'] == run['id']
    assert ai.enqueue_document_review(doc, 'owner', 'duplicate', before['document']['revision'])['id'] == run['id']
    with pytest.raises(BlockError): ai.enqueue(doc, 'owner', 'generate', 'other', before['blocks'][0]['id'])
    await finish(ai, provider, run)
    after = service.snapshot(doc, 'owner')
    assert after['blocks'] == before['blocks']
    assert after['document'] == before['document']
    assert len(provider.contexts[0]['blocks']) == 3
    assert [b['content'] for b in provider.contexts[0]['blocks']] == [b['content'] for b in before['blocks']]
    report = after['runs'][-1]
    assert report['status'] == 'completed' and report['stale'] is False
    assert len(report['result']['issues'][0]['block_ids']) == 2
    assert 'input_snapshot' not in report
    with pytest.raises(BlockError, match='全文作业'): ai.apply(doc, run['id'], 'owner', 1, True)
    with service.factory() as db:
        assert db.query(SpecVersion).count() == 0
    for b in before['blocks']:
        assert len(service.versions(doc, b['id'], 'owner')) == 2
    # Comments do not age a report; edits do, even after it has completed.
    service.comment(doc, 'owner', 'Review follow-up', [{'block_id': before['blocks'][0]['id'], 'base_version': 2}])
    assert service.snapshot(doc, 'owner')['runs'][-1]['stale'] is False
    manual(service, doc, before['blocks'][0], 'Changed after review')
    assert service.snapshot(doc, 'owner')['runs'][-1]['stale'] is True


@pytest.mark.asyncio
async def test_auto_resolution_keeps_original_warning_pending_manual_review(service):
    snap = prepared(service)
    doc = snap['document']['id']
    block = snap['blocks'][0]
    snap = manual(service, doc, block, '原始需求')

    class Provider:
        def __init__(self): self.revisions = 0

        async def review_document(self, context):
            return DocumentReview(document_id=doc, base_revision=context['base_revision'],
                summary='仍需处理', issues=[DocumentReviewIssue(severity='warning',
                    block_ids=[block['id']], description='验收规则仍不明确', suggestion='补全验收规则')])

        async def revise_document(self, context):
            self.revisions += 1
            target = context['blocks'][0]
            return DocumentRevision(document_id=doc, base_revision=context['base_revision'],
                summary='已尝试修订', blocks=[DocumentRevisionBlock(
                    block_id=target['id'], base_version=target['version'],
                    content='补充后的需求' if self.revisions == 1 else target['content'],
                    summary='修订摘要', spec_fragment=SpecFragment())])

    provider = Provider()
    ai = AIService(service, provider)
    review = ai.enqueue_document_review(doc, 'owner', 'review-remaining', snap['document']['revision'])
    await ai.tasks[review['id']]
    current = service.snapshot(doc, 'owner')
    revision = ai.enqueue_document_revision(doc, 'owner', review['id'], 'resolve-remaining',
                                             current['document']['revision'])
    await ai.tasks[revision['id']]
    after = service.snapshot(doc, 'owner')
    by_id = {run['id']: run for run in after['runs']}
    assert provider.revisions == 1
    assert by_id[revision['id']]['status'] == 'completed'
    assert by_id[revision['id']]['result']['review_pending'] is True
    assert by_id[review['id']]['apply_status'] != 'resolved'
    assert after['runs'][-1]['type'] == 'document_revision'
    assert len(by_id[review['id']]['result']['issues']) == 1
    assert by_id[review['id']]['stale'] is True
    assert next(item for item in after['blocks'] if item['id'] == block['id'])['content'] == '补充后的需求'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['edit', 'context', 'cancel', 'bad_result'])
async def test_whole_review_handles_changes_cancellation_and_invalid_result(service, change):
    snap = prepared(service); doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Current original')
    provider = DelayedProvider(); ai = AIService(service, provider)
    run = ai.enqueue_document_review(doc, 'owner', 'whole', snap['document']['revision'])
    task = ai.tasks[run['id']]
    await provider.started.wait()
    if change == 'edit': manual(service, doc, snap['blocks'][0], 'Human new text')
    elif change == 'context': service.context(doc, 'owner', snap['document']['revision'], 'Updated context', '')
    elif change == 'cancel': ai.cancel(doc, run['id'], 'owner')
    else: provider.bad_id = True
    provider.release.set()
    await asyncio.gather(task, return_exceptions=True)
    after = service.snapshot(doc, 'owner'); report = after['runs'][-1]
    if change in ('edit', 'context'):
        assert report['status'] == 'completed' and report['apply_status'] == 'conflict' and report['stale']
    elif change == 'cancel': assert report['status'] == 'cancelled' and report['result'] is None
    else: assert report['status'] == 'failed' and '不存在的模块' in report['error']
    assert after['blocks'][0]['content'] == ('Human new text' if change == 'edit' else 'Current original')
    assert after['document']['plan_status'] == snap['document']['plan_status']


@pytest.mark.asyncio
async def test_whole_review_guards_empty_revision_capacity_and_permission(service):
    snap = prepared(service); doc = snap['document']['id']
    ai = AIService(service, DelayedProvider())
    with pytest.raises(BlockError, match='正文'): ai.enqueue_document_review(doc, 'owner', 'empty', snap['document']['revision'])
    snap = manual(service, doc, snap['blocks'][0], 'some content')
    with pytest.raises(BlockError, match='已改变'): ai.enqueue_document_review(doc, 'owner', 'stale', 1)
    with pytest.raises(BlockError) as error: ai.enqueue_document_review(doc, 'intruder', 'forbidden', snap['document']['revision'])
    assert error.value.status == 403
    with service.write() as db:
        db.get(Block, snap['blocks'][0]['id']).content = 'x' * 300001
    with pytest.raises(BlockError, match='未截断'): ai.enqueue_document_review(doc, 'owner', 'large', snap['document']['revision'])
    assert not service.snapshot(doc, 'owner')['runs']


def test_whole_review_http_contract(service, test_settings):
    from dataclasses import replace
    snap = prepared(service); doc = snap['document']['id']
    snap = manual(service, doc, snap['blocks'][0], 'Ready for review')
    app = create_app(settings=replace(test_settings, identity_mode='local', identity_actor_id='owner'),
                     session_factory=service.factory, auto_bind_prd_review=False, block_provider=DelayedProvider())
    with TestClient(app) as client:
        assert client.post(f'/prd-documents/{doc}/review', json={'request_id': 'missing-revision'}).status_code == 422
        response = client.post(f'/prd-documents/{doc}/review', json={'request_id': 'whole', 'expected_revision': snap['document']['revision']})
        assert response.status_code == 202 and response.json()['type'] == 'document_review'
        assert 'input_snapshot' not in response.json()
        assert client.post(f'/prd-documents/{doc}/runs/{response.json()["id"]}/cancel', json={}).status_code == 200
