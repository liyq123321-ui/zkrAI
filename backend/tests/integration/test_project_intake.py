from collections import deque
import asyncio
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock, Thread

import pytest

from app.database.models import (
    AgentCall,
    AgentSession,
    Artifact,
    ClarificationRequest,
    ClarificationResponse,
    IntakeAnalysisClaim,
    ProcessedCommand,
    Project,
    SpecVersion,
    WorkItem,
)
from app.database.database import Base, create_engine_for_url, make_session_factory
from app.domain.types import (
    ClarificationAnalysis,
    ClarificationQuestion,
    ProjectPhase,
    SpecStatus,
)
from app.schemas.workflow import ProjectBrief, SessionCreateRequest
from app.services.project_service import (
    ClarificationAlreadyAnswered,
    ProjectService,
    RequestConflict,
    _canonical_hash,
)
from app.services.spec_service import SpecService
from tests.helpers.fake_agent import ScriptedAgentGateway


def _unclear_analysis() -> ClarificationAnalysis:
    return ClarificationAnalysis(
        ready_for_spec=False,
        questions=[
            {
                "question_id": "Q1",
                "question": "Who uses it?",
                "reason": "User is missing",
                "affected_areas": ["users"],
                "blocking": True,
            }
        ],
        assumptions=[],
    )


def _ready_analysis() -> ClarificationAnalysis:
    return ClarificationAnalysis(
        ready_for_spec=True,
        questions=[],
        assumptions=["The supplied brief is current."],
    )


def _request(brief, *, request_id: str = "create-1") -> SessionCreateRequest:
    return SessionCreateRequest(request_id=request_id, actor_id="owner", brief=brief)


@pytest.mark.asyncio
async def test_intake_creates_required_records_then_persists_clarification(
    session_factory, complete_brief
):
    """A missing intake record must prevent a durable, answerable clarification."""
    agent = ScriptedAgentGateway(analyze_results=deque([_unclear_analysis()]))

    state = await ProjectService(session_factory, agent).create_session(_request(complete_brief))

    with session_factory() as db:
        assert db.query(Project).count() == 1
        assert db.query(WorkItem).filter_by(kind="ROOT").count() == 1
        assert db.query(AgentSession).filter_by(role="PM").count() == 1
        assert db.query(Artifact).filter_by(kind="ORIGINAL_REQUIREMENT").count() == 1
        assert db.query(ProcessedCommand).count() == 1
        assert db.query(ClarificationRequest).count() == 1
        assert db.query(AgentCall).filter_by(status="SUCCEEDED").count() == 1
    assert state.phase is ProjectPhase.NEED_CLARIFICATION
    assert state.state_version == 1
    assert [question.question_id for question in state.outstanding_questions] == ["Q1"]


@pytest.mark.asyncio
async def test_intake_revalidates_agent_questions_before_persistence(
    session_factory, complete_brief
):
    """A constructed or third-party gateway result must not bypass the durable gate."""
    malformed = ClarificationAnalysis.model_construct(
        ready_for_spec=False,
        questions=[
            ClarificationQuestion.model_construct(
                question_id="Q-blank",
                question="   ",
                reason="Missing ownership detail.",
                affected_areas=["permissions"],
                blocking=True,
            )
        ],
        assumptions=[],
    )
    agent = ScriptedAgentGateway(analyze_results=deque([malformed]))

    state = await ProjectService(session_factory, agent).create_session(
        _request(complete_brief, request_id="blank-agent-question")
    )

    assert state.phase is ProjectPhase.INTAKE
    with session_factory() as db:
        assert db.query(ClarificationRequest).count() == 0
        call = db.query(AgentCall).filter_by(operation="analyze_brief").one()
        assert call.status == "FAILED"
        assert call.response == {"error_code": "INVALID_AGENT_RESULT"}


@pytest.mark.asyncio
async def test_failed_intake_analysis_is_retryable_without_duplicate_intake_records(
    session_factory, complete_brief
):
    """Removing retry recovery would leave a project stuck or duplicate its root records."""
    agent = ScriptedAgentGateway(
        analyze_results=deque([RuntimeError("PM unavailable"), _ready_analysis()])
    )
    service = ProjectService(session_factory, agent)

    failed_state = await service.create_session(_request(complete_brief))
    recovered_state = await service.create_session(_request(complete_brief))

    assert failed_state.phase is ProjectPhase.INTAKE
    assert failed_state.state_version == 0
    assert recovered_state.phase is ProjectPhase.SPECIFICATION
    assert recovered_state.state_version == 1
    with session_factory() as db:
        assert db.query(Project).count() == 1
        assert db.query(WorkItem).filter_by(kind="ROOT").count() == 1
        assert db.query(AgentSession).filter_by(role="PM").count() == 1
        assert db.query(Artifact).filter_by(kind="ORIGINAL_REQUIREMENT").count() == 1
        assert db.query(AgentCall).filter_by(status="FAILED").count() == 1
        assert db.query(AgentCall).filter_by(status="SUCCEEDED").count() == 1
        assert db.query(ProcessedCommand).count() == 1


@pytest.mark.asyncio
async def test_reused_intake_request_id_rejects_a_different_request_body(
    session_factory, complete_brief
):
    """Ignoring a changed body under the same idempotency key would lose user intent."""
    agent = ScriptedAgentGateway(analyze_results=deque([_ready_analysis()]))
    service = ProjectService(session_factory, agent)
    await service.create_session(_request(complete_brief))
    changed_brief = ProjectBrief(
        **{**complete_brief.model_dump(), "final_objective": "A different objective"}
    )

    with pytest.raises(RequestConflict):
        await service.create_session(_request(changed_brief))


@pytest.mark.asyncio
async def test_clarification_response_is_immutable_and_reanalysis_receives_full_history(
    session_factory, complete_brief
):
    """Dropping prior questions or answers would let re-analysis make an uninformed transition."""
    agent = ScriptedAgentGateway(
        analyze_results=deque([_unclear_analysis(), _ready_analysis()])
    )
    service = ProjectService(session_factory, agent)
    state = await service.create_session(_request(complete_brief))

    state = await service.answer_clarification(
        state.project_id, actor_id="owner", message="Operations managers use it."
    )

    assert state.phase is ProjectPhase.SPECIFICATION
    assert state.state_version == 2
    operation, payload = agent.calls[-1]
    assert operation == "analyze_brief"
    assert payload["brief"] == complete_brief.model_dump(mode="json")
    history = payload["clarification_history"]
    assert len(history) == 1
    assert history[0]["request_id"]
    assert history[0]["questions"] == [_unclear_analysis().questions[0].model_dump(mode="json")]
    assert history[0]["answers"] == {"message": "Operations managers use it."}
    assert history[0]["actor_id"] == "owner"
    with session_factory() as db:
        assert db.query(ClarificationResponse).count() == 1
        assert db.query(ClarificationRequest).count() == 1


@pytest.mark.asyncio
async def test_clarification_agent_failure_keeps_safe_phase_and_records_failure(
    session_factory, complete_brief
):
    """Advancing after a failed re-analysis would bypass the PM clarity gate."""
    agent = ScriptedAgentGateway(
        analyze_results=deque([_unclear_analysis(), RuntimeError("PM unavailable")])
    )
    service = ProjectService(session_factory, agent)
    state = await service.create_session(_request(complete_brief))

    state = await service.answer_clarification(
        state.project_id, actor_id="owner", message="Operations managers use it."
    )

    assert state.phase is ProjectPhase.NEED_CLARIFICATION
    assert state.state_version == 1
    with session_factory() as db:
        assert db.query(ClarificationResponse).count() == 1
        assert db.query(AgentCall).filter_by(status="FAILED").count() == 1


@pytest.mark.asyncio
async def test_direct_duplicate_answer_from_stale_concurrent_selection_is_domain_error(
    tmp_path, complete_brief, monkeypatch
):
    """A concurrent stale read must not leak the database uniqueness exception."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'answer-race.sqlite'}")
    Base.metadata.create_all(engine)
    concurrent_sessions = make_session_factory(engine)
    service = ProjectService(concurrent_sessions, ScriptedAgentGateway())
    with concurrent_sessions() as db:
        db.add_all(
            [
                Project(
                    id="answer-race-project",
                    session_id="answer-race-session",
                    creation_request_id="answer-race-create",
                    brief=complete_brief.model_dump(mode="json"),
                    final_approver="owner",
                    phase=ProjectPhase.NEED_CLARIFICATION.value,
                    state_version=1,
                ),
                ClarificationRequest(
                    id="answer-race-request",
                    project_id="answer-race-project",
                    boundary_key="INTAKE",
                    questions=_unclear_analysis().model_dump(mode="json")["questions"],
                    analysis_round=1,
                ),
            ]
        )
        db.commit()

    from app.services.state_projection import active_clarification_request as select_active

    selection_gate = Barrier(2)
    selection_lock = Lock()
    selection_count = 0

    def synchronize_stale_selection(db, project):
        nonlocal selection_count
        request = select_active(db, project)
        with selection_lock:
            selection_count += 1
            ordinal = selection_count
        if ordinal > 2:
            return request
        # Release SQLite's read transaction before synchronizing the callers;
        # both still hold the same selected request, just as on a database with
        # row-level concurrency, without manufacturing a SQLite lock deadlock.
        db.commit()
        selection_gate.wait(timeout=5)
        return request

    async def skip_reanalysis(project_id, *, propagate_agent_errors=False):
        return None

    monkeypatch.setattr(
        "app.services.project_service.active_clarification_request",
        synchronize_stale_selection,
    )
    monkeypatch.setattr(service, "_run_analysis", skip_reanalysis)
    results, errors = [], []

    def answer(message):
        try:
            results.append(
                asyncio.run(
                    service.answer_clarification(
                        "answer-race-project", actor_id="owner", message=message
                    )
                )
            )
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=answer, args=(message,)) for message in ("First", "Second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ClarificationAlreadyAnswered)
    assert errors[0].code == "CLARIFICATION_ALREADY_ANSWERED"
    assert errors[0].__cause__.__class__.__name__ == "IntegrityError"
    with concurrent_sessions() as db:
        responses = db.query(ClarificationResponse).filter_by(
            clarification_request_id="answer-race-request"
        ).all()
        assert len(responses) == 1
        assert responses[0].response_slot == "PRIMARY"
        assert responses[0].answers["message"] in {"First", "Second"}
    engine.dispose()


@pytest.mark.asyncio
async def test_ready_reanalysis_after_spec_clarification_enables_revision_without_new_initial_spec(
    session_factory, complete_brief, valid_spec, passing_semantic_review
):
    """Leaving a ready project at SPECIFICATION with a current Spec would strand it in an invalid state."""
    agent = ScriptedAgentGateway(analyze_results=deque([_unclear_analysis(), _ready_analysis()]))
    service = ProjectService(session_factory, agent)
    state = await service.create_session(_request(complete_brief))
    with session_factory() as db:
        project = db.get(Project, state.project_id)
        version = SpecVersion(
            id="spec-needs-answer",
            project_id=project.id,
            revision=1,
            content=valid_spec.model_dump(mode="json"),
            markdown="# Project Spec\n",
            generation_source="PM_AGENT",
            input_refs=["artifact:brief-1"],
            generator_agent_session_id="pm-session",
            generator_call_id="pm-call",
            parent_version_id=None,
            change_summary="Initial specification",
            content_hash="d" * 64,
            status=SpecStatus.NEED_CLARIFICATION.value,
        )
        db.add(version)
        project.current_spec_version_id = version.id
        project.phase = ProjectPhase.REVIEW.value
        db.query(ClarificationRequest).filter_by(project_id=project.id).one().spec_version_id = version.id
        db.commit()

    continued = await service.answer_clarification(
        state.project_id, actor_id="owner", message="The delivery owner approves."
    )

    assert continued.phase is ProjectPhase.REVIEW
    assert continued.current_spec_status is SpecStatus.REWORK
    assert continued.next_action == "REVISE_SPEC"
    revised_payload = valid_spec.model_copy(
        update={"main_flows": ["Submit, clarify, revise, review, and decompose"]}
    )
    revision_agent = ScriptedAgentGateway(
        generate_results=deque([revised_payload]), review_results=deque([passing_semantic_review])
    )
    revised = await SpecService(session_factory, revision_agent).revise_spec(
        state.project_id, "Apply the answered ownership decision."
    )
    assert revised.revision == 2
    assert revised.parent_version_id == "spec-needs-answer"
    with session_factory() as db:
        assert db.query(SpecVersion).filter_by(project_id=state.project_id).count() == 2


@pytest.mark.asyncio
async def test_direct_spec_clarification_followup_stays_on_same_spec(
    session_factory, complete_brief, valid_spec
):
    """Direct service callers must use the same multi-round Spec binding as commands."""
    second_question = ClarificationAnalysis(
        ready_for_spec=False,
        questions=[
            {
                "question_id": "DIRECT-SPEC-Q2",
                "question": "Who is the backup owner?",
                "reason": "Continuity ownership is unresolved.",
                "affected_areas": ["permissions"],
                "blocking": True,
            }
        ],
        assumptions=[],
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque([_ready_analysis(), second_question, _ready_analysis()])
    )
    service = ProjectService(session_factory, agent)
    state = await service.create_session(_request(complete_brief, request_id="direct-spec-chain"))
    with session_factory() as db:
        project = db.get(Project, state.project_id)
        version = SpecVersion(
            id="direct-spec-chain-v1",
            project_id=project.id,
            revision=1,
            content=valid_spec.model_dump(mode="json"),
            markdown="# Project Spec\n",
            generation_source="PM_AGENT",
            input_refs=["artifact:brief-1"],
            generator_agent_session_id="pm-session",
            generator_call_id="pm-call",
            parent_version_id=None,
            change_summary="Initial specification",
            content_hash="e" * 64,
            status=SpecStatus.NEED_CLARIFICATION.value,
        )
        request = ClarificationRequest(
            id="direct-spec-chain-q1",
            project_id=project.id,
            spec_version_id=version.id,
            questions=[
                {
                    "question_id": "DIRECT-SPEC-Q1",
                    "question": "Who owns the handoff?",
                    "reason": "Ownership is unresolved.",
                    "affected_areas": ["permissions"],
                    "blocking": True,
                }
            ],
            analysis_round=1,
            blocking=True,
        )
        db.add_all([version, request])
        project.current_spec_version_id = version.id
        project.phase = ProjectPhase.REVIEW.value
        db.commit()

    still_blocked = await service.answer_clarification(
        state.project_id, actor_id="owner", message="Operations Director."
    )

    assert still_blocked.phase is ProjectPhase.REVIEW
    assert still_blocked.current_spec_status is SpecStatus.NEED_CLARIFICATION
    assert still_blocked.outstanding_questions[0].question_id == "DIRECT-SPEC-Q2"
    continued = await service.answer_clarification(
        state.project_id, actor_id="owner", message="Platform Director."
    )
    assert continued.phase is ProjectPhase.REVIEW
    assert continued.current_spec_status is SpecStatus.REWORK
    with session_factory() as db:
        requests = db.query(ClarificationRequest).filter_by(
            project_id=state.project_id, spec_version_id="direct-spec-chain-v1"
        ).all()
        assert len(requests) == 2


@pytest.mark.asyncio
async def test_concurrent_identical_intake_uses_one_durable_pm_analysis_claim(
    tmp_path, complete_brief
):
    """Two HTTP-equivalent retries must not both invoke the PM before either commits."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'intake-race.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    class BlockingAgent:
        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def analyze_brief(self, payload):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return _ready_analysis()

    agent = BlockingAgent()
    service = ProjectService(factory, agent)
    request = _request(complete_brief, request_id="concurrent-intake")
    try:
        first = asyncio.create_task(service.create_session(request))
        await agent.started.wait()
        second = asyncio.create_task(service.create_session(request))
        await asyncio.sleep(0.05)
        agent.release.set()
        first_state, second_state = await asyncio.gather(first, second)

        assert agent.calls == 1
        assert first_state == second_state
        assert first_state.phase is ProjectPhase.SPECIFICATION
        assert first_state.state_version == 1
        with factory() as db:
            assert db.query(AgentCall).filter_by(operation="analyze_brief").count() == 1
            assert db.query(ClarificationRequest).count() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_intake_owner_releases_claim_for_same_request_retry(
    tmp_path, complete_brief
):
    """Cancellation must not leave a durable PREPARING owner that can never finish."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'intake-cancel.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    class CancelThenReadyAgent:
        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()
            self.never = asyncio.Event()

        async def analyze_brief(self, payload):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.never.wait()
            return _ready_analysis()

    agent = CancelThenReadyAgent()
    service = ProjectService(factory, agent)
    request = _request(complete_brief, request_id="cancelled-intake")
    try:
        owner = asyncio.create_task(service.create_session(request))
        await agent.started.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner

        with factory() as db:
            claim = db.query(IntakeAnalysisClaim).one()
            call = db.get(AgentCall, claim.agent_call_id)
            assert claim.status == "FAILED"
            assert call.status == "FAILED"

        recovered = await service.create_session(request)

        assert recovered.phase is ProjectPhase.SPECIFICATION
        assert agent.calls == 2
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_stale_intake_owner_is_reclaimed_with_compare_and_swap(
    tmp_path, complete_brief, monkeypatch
):
    """A dead process must not leave the same request permanently stuck in PREPARING."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'intake-stale.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    agent = ScriptedAgentGateway(analyze_results=deque([_ready_analysis()]))
    service = ProjectService(factory, agent)
    request = _request(complete_brief, request_id="stale-intake")
    request_body = request.model_dump(mode="json")
    try:
        with factory() as db:
            project = service._create_intake_records(
                db, request, request_body, _canonical_hash(request_body)
            )
            db.commit()
            project_id = project.id
        owns_claim, abandoned_call_id = service._claim_initial_analysis(project_id)
        assert owns_claim is True
        with factory() as db:
            claim = db.query(IntakeAnalysisClaim).one()
            claim.updated_at = datetime.now(UTC) - timedelta(minutes=5)
            db.commit()

        ticks = iter([0.0, 31.0])
        monkeypatch.setattr(
            "app.services.project_service.time.monotonic",
            lambda: next(ticks, 31.0),
        )

        recovered = await service.create_session(request)

        assert recovered.phase is ProjectPhase.SPECIFICATION
        assert [operation for operation, _ in agent.calls] == ["analyze_brief"]
        with factory() as db:
            claim = db.query(IntakeAnalysisClaim).one()
            abandoned = db.get(AgentCall, abandoned_call_id)
            assert claim.status == "COMPLETED"
            assert claim.agent_call_id != abandoned_call_id
            assert abandoned.status == "FAILED"
            assert db.query(AgentCall).filter_by(operation="analyze_brief").count() == 2
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_replaced_intake_owner_cannot_complete_the_new_claim(
    tmp_path, complete_brief
):
    """A stale owner result must be fenced after another process wins takeover."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'intake-fence.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    service = ProjectService(factory, ScriptedAgentGateway())
    request = _request(complete_brief, request_id="fenced-intake")
    request_body = request.model_dump(mode="json")
    try:
        with factory() as db:
            project = service._create_intake_records(
                db, request, request_body, _canonical_hash(request_body)
            )
            db.commit()
            project_id = project.id
        owns_claim, abandoned_call_id = service._claim_initial_analysis(project_id)
        assert owns_claim is True
        with factory() as db:
            claim = db.query(IntakeAnalysisClaim).one()
            claim_id = claim.id
            claim.updated_at = datetime.now(UTC) - timedelta(minutes=5)
            db.commit()
        replacement_owns, replacement_call_id = service._claim_initial_analysis(project_id)
        assert replacement_owns is True

        with factory() as db:
            assert (
                service._fence_initial_analysis_completion(
                    db, claim_id, abandoned_call_id
                )
                is False
            )
            db.rollback()

        with factory() as db:
            claim = db.get(IntakeAnalysisClaim, claim_id)
            assert claim.agent_call_id == replacement_call_id
            assert claim.status == "PREPARING"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_failure_during_analysis_cancels_owner_and_releases_claim(
    tmp_path, complete_brief, monkeypatch
):
    """A failed lease renewal must stop Agent work instead of running unowned."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'heartbeat-fail.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    class BlockingAgent:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def analyze_brief(self, payload):
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    agent = BlockingAgent()
    service = ProjectService(factory, agent)

    async def failing_heartbeat(claim_id, agent_call_id):
        await agent.started.wait()
        raise RuntimeError("heartbeat database write failed")

    monkeypatch.setattr(service, "_heartbeat_initial_analysis", failing_heartbeat)
    try:
        state = await asyncio.wait_for(
            service.create_session(
                _request(complete_brief, request_id="heartbeat-failure")
            ),
            timeout=1,
        )

        assert state.phase is ProjectPhase.INTAKE
        assert agent.cancelled.is_set()
        with factory() as db:
            assert db.query(IntakeAnalysisClaim).one().status == "FAILED"
            assert db.query(AgentCall).one().status == "FAILED"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_failure_immediately_before_result_is_fail_closed(
    tmp_path, complete_brief, monkeypatch
):
    """A result racing with lost lease renewal must not bypass ownership checks."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'heartbeat-race.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    class CoordinatedAgent:
        def __init__(self):
            self.finishing = asyncio.Event()
            self.heartbeat_failed = asyncio.Event()

        async def analyze_brief(self, payload):
            self.finishing.set()
            await self.heartbeat_failed.wait()
            return _ready_analysis()

    agent = CoordinatedAgent()
    service = ProjectService(factory, agent)

    async def failing_heartbeat(claim_id, agent_call_id):
        await agent.finishing.wait()
        agent.heartbeat_failed.set()
        raise RuntimeError("heartbeat failed before completion")

    monkeypatch.setattr(service, "_heartbeat_initial_analysis", failing_heartbeat)
    try:
        state = await service.create_session(
            _request(complete_brief, request_id="heartbeat-result-race")
        )

        assert state.phase is ProjectPhase.INTAKE
        with factory() as db:
            assert db.query(IntakeAnalysisClaim).one().status == "FAILED"
            assert db.query(AgentCall).one().status == "FAILED"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_threaded_identical_intake_races_share_project_and_pm_claim(
    tmp_path, complete_brief, monkeypatch
):
    """Separate request workers may race before both the Project and claim commits."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'intake-thread-race.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    class CountingAgent:
        def __init__(self):
            self.calls = 0
            self.lock = Lock()

        async def analyze_brief(self, payload):
            with self.lock:
                self.calls += 1
            return _ready_analysis()

    agent = CountingAgent()
    service = ProjectService(factory, agent)
    project_gate = Barrier(2)
    claim_gate = Barrier(2)
    gate_lock = Lock()
    project_calls = 0
    claim_calls = 0
    create_records = service._create_intake_records
    analysis_payload = service._analysis_payload

    def synchronized_create(*args, **kwargs):
        nonlocal project_calls
        with gate_lock:
            project_calls += 1
            ordinal = project_calls
        if ordinal <= 2:
            project_gate.wait(timeout=5)
        return create_records(*args, **kwargs)

    def synchronized_claim_payload(*args, **kwargs):
        nonlocal claim_calls
        with gate_lock:
            claim_calls += 1
            ordinal = claim_calls
        if ordinal <= 2:
            claim_gate.wait(timeout=5)
        return analysis_payload(*args, **kwargs)

    monkeypatch.setattr(service, "_create_intake_records", synchronized_create)
    monkeypatch.setattr(service, "_analysis_payload", synchronized_claim_payload)
    request = _request(complete_brief, request_id="threaded-concurrent-intake")
    states, errors = [], []

    def create():
        try:
            states.append(asyncio.run(service.create_session(request)))
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=create) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(states) == 2
        assert states[0] == states[1]
        assert agent.calls == 1
        with factory() as db:
            assert db.query(Project).count() == 1
            assert db.query(AgentCall).filter_by(operation="analyze_brief").count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

