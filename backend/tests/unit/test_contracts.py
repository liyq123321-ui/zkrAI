import pytest
from pydantic import ValidationError

from app.domain.types import AgentSpecProposal, ClarificationAnalysis


def test_agent_output_rejects_workflow_control_fields():
    with pytest.raises(ValidationError):
        ClarificationAnalysis.model_validate(
            {"ready_for_spec": True, "questions": [], "assumptions": [], "status": "APPROVED"}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "ready_for_spec": True,
            "questions": [
                {
                    "question_id": "Q-1",
                    "question": "Who owns this?",
                    "reason": "Ownership is required.",
                    "affected_areas": ["permissions"],
                    "blocking": True,
                }
            ],
            "assumptions": [],
        },
        {"ready_for_spec": False, "questions": [], "assumptions": []},
    ],
)
def test_clarification_analysis_rejects_contradictory_readiness(payload):
    """Contradictory PM output must fail the typed boundary instead of deadlocking intake."""
    with pytest.raises(ValidationError):
        ClarificationAnalysis.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("question_id", "   "),
        ("question", "\t"),
        ("reason", "\n"),
        ("affected_areas", ["  "]),
    ],
)
def test_clarification_analysis_rejects_blank_question_fields(field, value):
    """Whitespace-only prompt fields would persist a clarification no human can answer."""
    question = {
        "question_id": "Q-1",
        "question": "Who owns final approval?",
        "reason": "The approval owner is required.",
        "affected_areas": ["permissions"],
        "blocking": True,
    }
    question[field] = value

    with pytest.raises(ValidationError):
        ClarificationAnalysis.model_validate(
            {"ready_for_spec": False, "questions": [question], "assumptions": []}
        )


def test_agent_spec_proposal_uses_local_key_not_database_id():
    with pytest.raises(ValidationError):
        AgentSpecProposal.model_validate(
            {
                "work_item_key": "api",
                "work_item_id": "model-chosen-id",
                "objective": "Expose the API",
                "scope": ["session API"],
                "exclusions": ["frontend"],
                "context_refs": ["SPEC-1"],
                "inputs": ["approved spec"],
                "outputs": [{"name": "router", "format": "python", "required": True}],
                "fixed_constraints": ["FastAPI"],
                "configurable_parts": [],
                "extension_points": [],
                "acceptance_criteria": [
                    {
                        "requirement_ids": ["FR-001"],
                        "criterion": "route works",
                        "verification_method": "HTTP test",
                        "expected_result": "201",
                    }
                ],
                "required_skills": ["backend-development"],
                "allowed_tools": ["pytest"],
                "allowed_paths": ["app/api"],
                "responsible_role": "Backend Engineer",
                "suggested_assignee": "backend-agent",
                "dependency_keys": [],
                "test_obligations": ["API integration test"],
                "risks": [],
                "open_questions": [],
            }
        )


def test_complete_factories_build_strict_contracts(
    complete_brief, valid_spec, passing_semantic_review, valid_breakdown
):
    assert complete_brief.final_objective
    assert len(valid_spec.functional_requirements) == 1
    assert len(valid_spec.non_functional_requirements) == 1
    assert passing_semantic_review.verdict.value == "PASS"
    assert [item.local_key for item in valid_breakdown.milestones] == ["m-api"]
    assert [item.local_key for item in valid_breakdown.tasks] == ["t-domain", "t-api"]
    assert valid_breakdown.tasks[1].dependency_keys == ["t-domain"]


def test_request_and_state_contracts_reject_extra_fields(complete_brief):
    from app.schemas.workflow import ProjectBrief, SessionCommandRequest

    brief_payload = complete_brief.model_dump()
    brief_payload["unknown"] = "nope"
    with pytest.raises(ValidationError):
        ProjectBrief.model_validate(brief_payload)
    with pytest.raises(ValidationError):
        SessionCommandRequest.model_validate(
            {
                "command_id": "cmd-1",
                "action": "message",
                "expected_state_version": 0,
                "actor_id": "actor-1",
                "unknown": "nope",
            }
        )

