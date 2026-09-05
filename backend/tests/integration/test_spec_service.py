"""Persistence-level behavior for immutable Spec generation and automatic review."""

from collections import deque
import hashlib
import json

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    AgentCall,
    AgentSession,
    Artifact,
    ClarificationRequest,
    ClarificationResponse,
    Project,
    SpecReview,
    SpecVersion,
)
from app.domain.types import CommandAction, ProjectPhase, ReviewFinding, ReviewKind, ReviewVerdict, SemanticReview, SpecStatus
from app.schemas.workflow import SessionCommandRequest, SessionCreateRequest
from app.services.command_service import CommandService
from app.services.project_service import ProjectService
from app.services.spec_service import SpecService
from tests.helpers.fake_agent import ScriptedAgentGateway


def _add_specification_project(db, brief):
    project = Project(
        id="project-1",
        session_id="session-1",
        creation_request_id="request-1",
        brief=brief.model_dump(mode="json"),
        final_approver=brief.final_approver,
        project_manager_ids=brief.project_manager_ids,
        root_owner_ids=brief.root_owner_ids,
        phase=ProjectPhase.SPECIFICATION.value,
        state_version=1,
    )
    db.add(project)
    db.add(
        AgentSession(
            id="pm-session-1",
            project_id=project.id,
            role="PM",
            purpose="Generate project specifications",
            metadata_json={},
        )
    )
    db.add(
        Artifact(
            id="original-artifact-1",
            project_id=project.id,
            kind="ORIGINAL_REQUIREMENT",
            content=brief.model_dump(mode="json"),
            content_hash="a" * 64,
            source_actor_id="owner",
        )
    )
    db.commit()
    return project


def _semantic_rework() -> SemanticReview:
    return SemanticReview(
        verdict=ReviewVerdict.REJECT,
        findings=[
            ReviewFinding(
                code="SEMANTIC_GAP",
                severity="BLOCKER",
                spec_path="/main_flows",
                message="The flow omits an approval handoff.",
                suggested_resolution="Describe the handoff.",
                blocks_progress=True,
            )
        ],
    )


def _semantic_human_decision() -> SemanticReview:
    return SemanticReview(
        verdict=ReviewVerdict.NEED_INFO,
        findings=[
            ReviewFinding(
                code="NEEDS_HUMAN_DECISION",
                severity="BLOCKER",
                spec_path="/system_boundaries",
                message="Which data residency rule applies to production records?",
                suggested_resolution="Obtain a decision from the data-governance owner.",
                blocks_progress=True,
            )
        ],
    )


def _intake_question_analysis():
    from app.domain.types import ClarificationAnalysis

    return ClarificationAnalysis(
        ready_for_spec=False,
        questions=[
            {
                "question_id": "INTAKE-Q1",
                "question": "Which users need access?",
                "reason": "Users are required for the initial brief.",
                "affected_areas": ["users"],
                "blocking": True,
            }
        ],
        assumptions=[],
    )


def _ready_analysis():
    from app.domain.types import ClarificationAnalysis

    return ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])


def _add_clarification_history(db):
    request = ClarificationRequest(
        id="clarification-request-1",
        project_id="project-1",
        questions=[
            {
                "question_id": "Q1",
                "question": "Who owns approval?",
                "reason": "Ownership is required.",
                "affected_areas": ["permissions"],
                "blocking": True,
            }
        ],
        analysis_round=1,
        blocking=True,
        agent_session_id="pm-session-1",
        agent_call_id=None,
    )
    response = ClarificationResponse(
        id="clarification-response-1",
        project_id="project-1",
        clarification_request_id=request.id,
        actor_id="owner",
        answers={"message": "The delivery owner approves."},
    )
    unanswered = ClarificationRequest(
        id="clarification-request-2",
        project_id="project-1",
        questions=[
            {
                "question_id": "Q2",
                "question": "Which timezone applies?",
                "reason": "Scheduling needs a default.",
                "affected_areas": ["time constraints"],
                "blocking": False,
            }
        ],
        analysis_round=2,
        blocking=False,
        agent_session_id="pm-session-1",
        agent_call_id=None,
    )
    db.add_all([request, response, unanswered])
    db.commit()


@pytest.mark.asyncio
async def test_create_spec_persists_hash_markdown_and_separate_review_receipts(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """Dropping either receipt or reviewer session would make automatic review unauditable."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]), review_results=deque([passing_semantic_review])
    )

    version = await SpecService(session_factory, agent).create_spec("project-1")

    assert version.revision == 1
    assert version.status == SpecStatus.HUMAN_REVIEW.value
    assert version.content_hash
    assert version.markdown.startswith("# Project Spec\n\n## Background and Goals\n")
    assert "## Acceptance Criteria" in version.markdown
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec"]
    generation_payload = agent.calls[0][1]
    assert generation_payload["input_refs"] == ["artifact:original-artifact-1"]
    assert generation_payload["artifacts"] == [
        {
            "id": "original-artifact-1",
            "kind": "ORIGINAL_REQUIREMENT",
            "content": complete_brief.model_dump(mode="json"),
            "external_ref": None,
            "content_hash": "a" * 64,
            "source_actor_id": "owner",
        }
    ]
    review_payload = agent.calls[1][1]
    assert review_payload["generation_source_snapshot"] == generation_payload
    assert version.input_refs == ["artifact:original-artifact-1"]
    assert version.content["source_refs"] == ["artifact:original-artifact-1"]
    with session_factory() as db:
        project = db.get(Project, "project-1")
        reviews = db.query(SpecReview).filter_by(spec_version_id=version.id).all()
        reviewer_session = db.query(AgentSession).filter_by(project_id="project-1", role="REVIEWER").one()
        review_call = db.query(AgentCall).filter_by(operation="review_spec").one()
        assert project.phase == ProjectPhase.REVIEW.value
        assert project.current_spec_version_id == version.id
        assert {item.kind for item in reviews} == {ReviewKind.RULE.value, ReviewKind.AGENT.value}
        assert review_call.agent_session_id == reviewer_session.id
        assert all(item.input_spec_hash == version.content_hash for item in reviews)


@pytest.mark.asyncio
async def test_generation_uses_full_clarification_history_and_server_selected_provenance(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """Letting a model choose provenance would omit durable evidence or invent source references."""
    _add_specification_project(db_session, complete_brief)
    _add_clarification_history(db_session)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]), review_results=deque([passing_semantic_review])
    )

    version = await SpecService(session_factory, agent).create_spec("project-1")

    expected_refs = [
        "artifact:original-artifact-1",
        "clarification_request:clarification-request-1",
        "clarification_response:clarification-response-1",
        "clarification_request:clarification-request-2",
    ]
    generation_payload = agent.calls[0][1]
    assert generation_payload["input_refs"] == expected_refs
    assert generation_payload["clarification_history"] == [
        {
            "request_id": "clarification-request-1",
            "questions": [
                {
                    "question_id": "Q1",
                    "question": "Who owns approval?",
                    "reason": "Ownership is required.",
                    "affected_areas": ["permissions"],
                    "blocking": True,
                }
            ],
            "responses": [
                {
                    "response_id": "clarification-response-1",
                    "answers": {"message": "The delivery owner approves."},
                    "actor_id": "owner",
                }
            ],
        },
        {
            "request_id": "clarification-request-2",
            "questions": [
                {
                    "question_id": "Q2",
                    "question": "Which timezone applies?",
                    "reason": "Scheduling needs a default.",
                    "affected_areas": ["time constraints"],
                    "blocking": False,
                }
            ],
            "responses": [],
        },
    ]
    assert version.input_refs == expected_refs
    assert version.content["source_refs"] == expected_refs


@pytest.mark.asyncio
async def test_materialization_uses_the_persisted_generation_snapshot_after_evidence_changes(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review, monkeypatch
):
    """Re-reading evidence after the Agent call would make its auditable inputs differ from its actual inputs."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]), review_results=deque([passing_semantic_review])
    )
    service = SpecService(session_factory, agent)
    original_checkpoint = service._record_call_result
    added_evidence = False

    def checkpoint_then_add_evidence(project_id, call_id, result):
        nonlocal added_evidence
        original_checkpoint(project_id, call_id, result)
        if added_evidence:
            return
        added_evidence = True
        with session_factory() as db:
            db.add(
                Artifact(
                    id="late-artifact-1",
                    project_id=project_id,
                    kind="REFERENCE",
                    content={"late": True},
                    content_hash="b" * 64,
                    source_actor_id="owner",
                )
            )
            db.commit()

    monkeypatch.setattr(service, "_record_call_result", checkpoint_then_add_evidence)

    version = await service.create_spec("project-1")

    expected_content = valid_spec.model_copy(
        update={"source_refs": ["artifact:original-artifact-1"]}
    ).model_dump(mode="json")
    expected_hash = hashlib.sha256(
        json.dumps(expected_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert version.input_refs == ["artifact:original-artifact-1"]
    assert version.content["source_refs"] == ["artifact:original-artifact-1"]
    assert version.content_hash == expected_hash


@pytest.mark.asyncio
async def test_changed_generation_snapshot_does_not_reuse_a_saved_result(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """A saved response for older evidence must not be reused when canonical evidence changes."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec, valid_spec]), review_results=deque([passing_semantic_review])
    )
    service = SpecService(session_factory, agent)
    failed_once = False

    def fail_version_insert(mapper, connection, target):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("database unavailable")

    event.listen(SpecVersion, "before_insert", fail_version_insert)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.create_spec("project-1")
    finally:
        event.remove(SpecVersion, "before_insert", fail_version_insert)
    with session_factory() as db:
        db.add(
            Artifact(
                id="new-evidence-1",
                project_id="project-1",
                kind="REFERENCE",
                content={"new": True},
                content_hash="c" * 64,
                source_actor_id="owner",
            )
        )
        db.commit()

    version = await service.create_spec("project-1")

    assert [name for name, _ in agent.calls] == ["generate_spec", "generate_spec", "review_spec"]
    assert version.input_refs == ["artifact:original-artifact-1", "artifact:new-evidence-1"]


@pytest.mark.asyncio
async def test_resume_auto_review_reuses_rule_receipt_without_regenerating_or_rehashing(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """A retry must finish the current immutable version instead of generating a second one."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]),
        review_results=deque([RuntimeError("reviewer unavailable"), passing_semantic_review]),
    )
    service = SpecService(session_factory, agent)

    unfinished = await service.create_spec("project-1")
    with session_factory() as db:
        db.get(Project, "project-1").brief = {"tampered": "later database state"}
        db.add(
            Artifact(
                id="late-artifact",
                project_id="project-1",
                kind="REFERENCE",
                content={"late": True},
                content_hash="f" * 64,
                source_actor_id="owner",
            )
        )
        db.commit()
    resumed = await service.create_spec("project-1")

    assert unfinished.id == resumed.id
    assert unfinished.revision == resumed.revision == 1
    assert unfinished.content_hash == resumed.content_hash
    assert resumed.status == SpecStatus.HUMAN_REVIEW.value
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec", "review_spec"]
    assert agent.calls[1][1]["generation_source_snapshot"] == agent.calls[0][1]
    assert agent.calls[2][1]["generation_source_snapshot"] == agent.calls[0][1]
    with session_factory() as db:
        assert db.query(SpecVersion).filter_by(project_id="project-1").count() == 1
        assert db.query(SpecReview).filter_by(spec_version_id=resumed.id, kind=ReviewKind.RULE.value).count() == 1
        assert db.query(SpecReview).filter_by(spec_version_id=resumed.id, kind=ReviewKind.AGENT.value).count() == 1
        assert db.query(AgentCall).filter_by(operation="review_spec", status="FAILED").count() == 1


@pytest.mark.asyncio
async def test_empty_need_info_verdict_keeps_generated_draft_in_rework(
    session_factory, db_session, complete_brief, valid_spec
):
    _add_specification_project(db_session, complete_brief)
    need_info = SemanticReview.model_construct(
        verdict=ReviewVerdict.NEED_INFO, findings=[]
    )
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]), review_results=deque([need_info])
    )

    version = await SpecService(session_factory, agent).create_spec("project-1")

    assert version.status == SpecStatus.REWORK.value
    with session_factory() as db:
        assert db.query(ClarificationRequest).filter_by(spec_version_id=version.id).count() == 0


@pytest.mark.asyncio
async def test_structural_failure_records_skipped_semantic_receipt_without_reviewer_call(
    session_factory, db_session, complete_brief, valid_spec
):
    """Calling a semantic reviewer with invalid source evidence would produce misleading review."""
    _add_specification_project(db_session, complete_brief)
    duplicate = valid_spec.functional_requirements[0].model_copy()
    invalid = valid_spec.model_copy(update={"functional_requirements": [duplicate, duplicate]})
    agent = ScriptedAgentGateway(generate_results=deque([invalid]))

    version = await SpecService(session_factory, agent).create_spec("project-1")

    assert version.status == SpecStatus.REWORK.value
    assert [name for name, _ in agent.calls] == ["generate_spec"]
    with session_factory() as db:
        skipped = db.query(SpecReview).filter_by(spec_version_id=version.id, kind=ReviewKind.AGENT.value).one()
        assert skipped.verdict == "SKIPPED"
        assert skipped.comments == "STRUCTURAL_RULE_FAILURE"
        assert db.query(AgentSession).filter_by(project_id="project-1", role="REVIEWER").count() == 0


@pytest.mark.asyncio
async def test_nonstructural_rule_failure_still_uses_reviewer_agent(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """A responsibility gap is reviewable, so skipping semantic review would lose useful findings."""
    _add_specification_project(db_session, complete_brief)
    actorless = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Approve project specifications"]}
    )
    agent = ScriptedAgentGateway(
        generate_results=deque([actorless]), review_results=deque([passing_semantic_review])
    )

    version = await SpecService(session_factory, agent).create_spec("project-1")

    assert version.status == SpecStatus.REWORK.value
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec"]
    with session_factory() as db:
        receipt = db.query(SpecReview).filter_by(spec_version_id=version.id, kind=ReviewKind.AGENT.value).one()
        assert receipt.verdict == ReviewVerdict.PASS.value


@pytest.mark.asyncio
async def test_uncovered_requirement_still_uses_reviewer_agent(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """Coverage gaps remain legible to semantic review and must not produce a skip receipt."""
    _add_specification_project(db_session, complete_brief)
    uncovered = valid_spec.model_copy(update={"acceptance_criteria": [valid_spec.acceptance_criteria[0]]})
    agent = ScriptedAgentGateway(
        generate_results=deque([uncovered]), review_results=deque([passing_semantic_review])
    )

    version = await SpecService(session_factory, agent).create_spec("project-1")

    assert version.status == SpecStatus.REWORK.value
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec"]


@pytest.mark.asyncio
async def test_spec_human_decision_keeps_draft_reviewable_and_allows_revision(
    session_factory, complete_brief, valid_spec, passing_semantic_review
):
    """Reusing an answered intake request would drop the Spec decision that blocks revision."""
    blocking_spec = type(valid_spec).model_validate(
        {
            **valid_spec.model_dump(),
            "open_questions": [
                {
                    "question": "Which region owns the production data?",
                    "blocking": True,
                    "risk_owner": "delivery-owner",
                }
            ],
        }
    )
    revised_spec = valid_spec.model_copy(
        update={"main_flows": ["Submit, clarify, revise, review, and decompose"]}
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque([_intake_question_analysis(), _ready_analysis(), _ready_analysis()]),
        generate_results=deque([blocking_spec, revised_spec]),
        review_results=deque([passing_semantic_review, passing_semantic_review]),
    )
    project_service = ProjectService(session_factory, agent)
    intake = await project_service.create_session(
        SessionCreateRequest(request_id="project-request-1", actor_id="owner", brief=complete_brief)
    )
    with session_factory() as db:
        stale_request_id = db.query(ClarificationRequest).filter_by(project_id=intake.project_id).one().id
    ready = await project_service.answer_clarification(
        intake.project_id, actor_id="owner", message="Operations managers need access."
    )
    assert ready.phase is ProjectPhase.SPECIFICATION

    spec_service = SpecService(session_factory, agent)
    pending = await spec_service.create_spec(intake.project_id)
    assert pending.status == SpecStatus.REWORK.value
    with session_factory() as db:
        requests = db.query(ClarificationRequest).filter_by(project_id=intake.project_id).all()
        assert [request.id for request in requests] == [stale_request_id]
        assert db.query(ClarificationRequest).filter_by(spec_version_id=pending.id).count() == 0

    revised = await spec_service.revise_spec(
        intake.project_id, "Record EMEA as the production-data owner."
    )
    assert revised.revision == 2
    assert revised.parent_version_id == pending.id


@pytest.mark.asyncio
async def test_semantic_human_decision_keeps_draft_reviewable_and_allows_revision(
    session_factory, complete_brief, valid_spec, passing_semantic_review
):
    """Reviewer-only human decisions need an answerable request, not an AUTO_REVIEW loop."""
    revised_spec = valid_spec.model_copy(
        update={"main_flows": ["Submit, decide residency, revise, review, and decompose"]}
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque([_ready_analysis(), _ready_analysis()]),
        generate_results=deque([valid_spec, revised_spec]),
        review_results=deque([_semantic_human_decision(), passing_semantic_review]),
    )
    project_service = ProjectService(session_factory, agent)
    intake = await project_service.create_session(
        SessionCreateRequest(request_id="semantic-decision-request", actor_id="owner", brief=complete_brief)
    )
    spec_service = SpecService(session_factory, agent)

    pending = await spec_service.create_spec(intake.project_id)

    assert pending.status == SpecStatus.REWORK.value
    with session_factory() as db:
        assert (
            db.query(ClarificationRequest)
            .filter_by(project_id=intake.project_id, spec_version_id=pending.id)
            .count()
            == 0
        )

    revised = await spec_service.revise_spec(intake.project_id, "Apply the EU residency decision.")
    assert revised.revision == 2


def test_spec_and_intake_clarification_requests_can_repeat_across_rounds(
    db_session, complete_brief
):
    """A single Spec may require multiple immutable question-and-answer rounds."""
    _add_specification_project(db_session, complete_brief)
    first = ClarificationRequest(
        id="spec-request-1",
        project_id="project-1",
        spec_version_id="spec-version-1",
        boundary_key="SPEC:spec-version-1",
        analysis_round=1,
        questions=[{"question_id": "Q1", "question": "Choose", "reason": "Needed", "affected_areas": ["x"], "blocking": True}],
    )
    followup = ClarificationRequest(
        id="spec-request-2",
        project_id="project-1",
        spec_version_id="spec-version-1",
        boundary_key="SPEC:spec-version-1",
        analysis_round=2,
        questions=[{"question_id": "Q2", "question": "Choose", "reason": "Needed", "affected_areas": ["x"], "blocking": True}],
    )
    db_session.add(first)
    db_session.commit()
    db_session.add(followup)
    db_session.commit()

    db_session.add_all(
        [
            ClarificationRequest(
                id="intake-request-1",
                project_id="project-1",
                boundary_key="INTAKE",
                analysis_round=1,
                questions=[{"question_id": "Q3", "question": "Choose", "reason": "Needed", "affected_areas": ["x"], "blocking": True}],
            ),
            ClarificationRequest(
                id="intake-request-2",
                project_id="project-1",
                boundary_key="INTAKE",
                analysis_round=2,
                questions=[{"question_id": "Q4", "question": "Choose", "reason": "Needed", "affected_areas": ["x"], "blocking": True}],
            ),
        ]
    )
    db_session.commit()
    assert db_session.query(ClarificationRequest).filter_by(
        project_id="project-1", spec_version_id="spec-version-1"
    ).count() == 2
    assert db_session.query(ClarificationRequest).filter_by(
        project_id="project-1", spec_version_id=None
    ).count() == 2
    db_session.add(
        ClarificationRequest(
            id="duplicate-spec-round",
            project_id="project-1",
            spec_version_id="spec-version-1",
            boundary_key="SPEC:spec-version-1",
            analysis_round=2,
            questions=[],
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.asyncio
async def test_revise_spec_creates_new_version_without_mutating_old_content_or_receipts(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """Mutating a rejected version would erase the audit trail that explains its rejection."""
    _add_specification_project(db_session, complete_brief)
    revised = valid_spec.model_copy(
        update={"main_flows": ["Submit, approve, and decompose the specification"]}
    )
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec, revised]),
        review_results=deque([_semantic_rework(), passing_semantic_review]),
    )
    service = SpecService(session_factory, agent)

    first = await service.create_spec("project-1")
    second = await service.revise_spec("project-1", "Add the approval handoff.")

    assert first.status == SpecStatus.REWORK.value
    assert second.revision == 2
    assert second.parent_version_id == first.id
    assert second.change_summary == "Add the approval handoff."
    assert second.content_hash != first.content_hash
    with session_factory() as db:
        old = db.get(SpecVersion, first.id)
        old_reviews = db.query(SpecReview).filter_by(spec_version_id=first.id).all()
        current = db.get(Project, "project-1")
        assert old.content == valid_spec.model_copy(
            update={"source_refs": ["artifact:original-artifact-1"]}
        ).model_dump(mode="json")
        assert old.status == SpecStatus.REWORK.value
        assert len(old_reviews) == 2
        semantic_receipt = next(item for item in old_reviews if item.kind == ReviewKind.AGENT.value)
        assert semantic_receipt.findings == [_semantic_rework().findings[0].model_dump(mode="json")]
        assert current.current_spec_version_id == second.id
        assert db.query(SpecVersion).filter_by(project_id="project-1").count() == 2
    revision_payload = agent.calls[2][1]
    assert revision_payload["parent_spec_hash"] == first.content_hash
    assert revision_payload["review_comments"] == "Add the approval handoff."


@pytest.mark.asyncio
async def test_revise_command_uses_shared_revision_materializer_without_changing_receipts(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """Losing the shared seam would let revise and comment publication persist different evidence."""
    _add_specification_project(db_session, complete_brief)
    revised = valid_spec.model_copy(
        update={"main_flows": ["Submit, approve, revise, and decompose the specification"]}
    )
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec, revised]),
        review_results=deque([_semantic_rework(), passing_semantic_review]),
    )
    specs = SpecService(session_factory, agent)
    first = await specs.create_spec("project-1")
    with session_factory() as db:
        state_version = db.get(Project, "project-1").state_version
        prior_receipts = [
            (
                receipt.id,
                receipt.kind,
                receipt.verdict,
                list(receipt.findings),
                receipt.reviewer_id,
                receipt.input_spec_hash,
                receipt.command_id,
            )
            for receipt in db.query(SpecReview)
            .filter_by(spec_version_id=first.id)
            .order_by(SpecReview.kind, SpecReview.id)
            .all()
        ]

    request = SessionCommandRequest(
        command_id="shared-revise-command",
        action=CommandAction.REVISE,
        expected_state_version=state_version,
        actor_id="owner",
        message="Add the approval handoff.",
    )
    result = await CommandService(
        session_factory, handlers={CommandAction.REVISE: specs.as_command_handler()}
    ).execute("session-1", request)

    with session_factory() as db:
        versions = (
            db.query(SpecVersion)
            .filter_by(project_id="project-1")
            .order_by(SpecVersion.revision)
            .all()
        )
        assert len(versions) == 2
        assert versions[0].id == first.id
        assert versions[1].id == result.state.current_spec_version_id
        assert versions[1].parent_version_id == first.id
        assert versions[1].generation_source == "PM_AGENT"
        new_receipts = (
            db.query(SpecReview)
            .filter_by(spec_version_id=versions[1].id)
            .order_by(SpecReview.kind)
            .all()
        )
        assert len(new_receipts) == 2
        agent_receipt = next(
            receipt
            for receipt in new_receipts
            if receipt.kind == ReviewKind.AGENT.value
        )
        rule_receipt = next(
            receipt
            for receipt in new_receipts
            if receipt.kind == ReviewKind.RULE.value
        )
        reviewer_call = (
            db.query(AgentCall)
            .filter_by(operation="review_spec")
            .filter(AgentCall.request["command_id"].as_string() == request.command_id)
            .one()
        )
        assert (
            rule_receipt.kind,
            rule_receipt.verdict,
            rule_receipt.findings,
            rule_receipt.reviewer_id,
            rule_receipt.input_spec_hash,
            rule_receipt.command_id,
        ) == (
            ReviewKind.RULE.value,
            ReviewVerdict.PASS.value,
            [],
            "SYSTEM",
            versions[1].content_hash,
            request.command_id,
        )
        assert (
            agent_receipt.kind,
            agent_receipt.verdict,
            agent_receipt.findings,
            agent_receipt.reviewer_id,
            agent_receipt.input_spec_hash,
            agent_receipt.command_id,
        ) == (
            ReviewKind.AGENT.value,
            ReviewVerdict.PASS.value,
            [],
            reviewer_call.agent_session_id,
            versions[1].content_hash,
            request.command_id,
        )
        assert [
            (
                receipt.id,
                receipt.kind,
                receipt.verdict,
                list(receipt.findings),
                receipt.reviewer_id,
                receipt.input_spec_hash,
                receipt.command_id,
            )
            for receipt in db.query(SpecReview)
            .filter_by(spec_version_id=first.id)
            .order_by(SpecReview.kind, SpecReview.id)
            .all()
        ] == prior_receipts


@pytest.mark.asyncio
async def test_identical_revision_is_a_noop_without_advancing_revision_or_hash(
    session_factory, db_session, complete_brief, valid_spec
):
    """Creating a second version with unchanged canonical content would corrupt version history."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec, valid_spec]), review_results=deque([_semantic_rework()])
    )
    service = SpecService(session_factory, agent)

    first = await service.create_spec("project-1")
    unchanged = await service.revise_spec("project-1", "Attempt a revision without changes.")

    assert unchanged.id == first.id
    assert unchanged.revision == 1
    assert unchanged.content_hash == first.content_hash
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec", "generate_spec"]
    with session_factory() as db:
        assert db.query(SpecVersion).filter_by(project_id="project-1").count() == 1


@pytest.mark.asyncio
async def test_generation_persistence_failure_resumes_saved_result_without_second_agent_call(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """A DB failure after external generation must resume the stored result rather than regenerate."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]), review_results=deque([passing_semantic_review])
    )
    service = SpecService(session_factory, agent)
    failed_once = False

    def fail_version_insert(mapper, connection, target):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("database unavailable")

    event.listen(SpecVersion, "before_insert", fail_version_insert)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.create_spec("project-1")
    finally:
        event.remove(SpecVersion, "before_insert", fail_version_insert)

    resumed = await service.create_spec("project-1")

    assert resumed.revision == 1
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec"]
    with session_factory() as db:
        call = db.query(AgentCall).filter_by(operation="generate_spec").one()
        assert call.status == "SUCCEEDED"
        assert call.response == valid_spec.model_dump(mode="json")


@pytest.mark.asyncio
async def test_semantic_receipt_persistence_failure_resumes_saved_result_without_second_review_call(
    session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    """A DB failure after external review must materialize its stored result instead of reviewing twice."""
    _add_specification_project(db_session, complete_brief)
    agent = ScriptedAgentGateway(
        generate_results=deque([valid_spec]), review_results=deque([passing_semantic_review])
    )
    service = SpecService(session_factory, agent)
    failed_once = False

    def fail_agent_receipt(mapper, connection, target):
        nonlocal failed_once
        if target.kind == ReviewKind.AGENT.value and not failed_once:
            failed_once = True
            raise RuntimeError("database unavailable")

    event.listen(SpecReview, "before_insert", fail_agent_receipt)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.create_spec("project-1")
    finally:
        event.remove(SpecReview, "before_insert", fail_agent_receipt)

    resumed = await service.create_spec("project-1")

    assert resumed.status == SpecStatus.HUMAN_REVIEW.value
    assert [name for name, _ in agent.calls] == ["generate_spec", "review_spec"]
    with session_factory() as db:
        call = db.query(AgentCall).filter_by(operation="review_spec").one()
        assert call.status == "SUCCEEDED"
        assert call.response == passing_semantic_review.model_dump(mode="json")
