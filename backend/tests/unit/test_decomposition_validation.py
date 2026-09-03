"""Purely structural validation for untrusted breakdown proposals."""

import pytest

from app.domain.types import OpenQuestion
from app.services.decomposition_service import BreakdownValidationError, validate_breakdown


def test_missing_requirement_coverage_is_rejected(valid_breakdown, valid_spec):
    for spec in valid_breakdown.agent_specs:
        for criterion in spec.acceptance_criteria:
            criterion.requirement_ids = ["FR-001"]

    with pytest.raises(BreakdownValidationError, match="MISSING_ACCEPTANCE_COVERAGE.*NFR-001"):
        validate_breakdown(valid_breakdown, valid_spec)


def test_dependency_cycle_is_rejected_with_a_real_cycle_key(valid_breakdown, valid_spec):
    valid_breakdown.milestones[0].dependency_keys = ["t-domain"]
    valid_breakdown.tasks[0].dependency_keys = ["t-api"]
    valid_breakdown.tasks[1].dependency_keys = ["t-domain"]
    valid_breakdown.agent_specs[0].dependency_keys = ["t-api"]

    with pytest.raises(BreakdownValidationError, match=r"DEPENDENCY_CYCLE \[t-api\]"):
        validate_breakdown(valid_breakdown, valid_spec)


def test_duplicate_dependency_key_is_rejected_before_persistence(valid_breakdown, valid_spec):
    valid_breakdown.tasks[1].dependency_keys = ["t-domain", "t-domain"]
    valid_breakdown.agent_specs[1].dependency_keys = ["t-domain", "t-domain"]

    with pytest.raises(BreakdownValidationError, match=r"DUPLICATE_DEPENDENCY \[t-api\]"):
        validate_breakdown(valid_breakdown, valid_spec)


def test_unrequired_output_still_needs_a_name_and_format(valid_breakdown, valid_spec):
    valid_breakdown.agent_specs[1].outputs.append({"name": " ", "format": "json", "required": False})

    with pytest.raises(BreakdownValidationError, match=r"INVALID_OUTPUT \[t-api\]"):
        validate_breakdown(valid_breakdown, valid_spec)


def test_source_refs_must_exactly_match_approved_version_inputs(valid_breakdown, valid_spec):
    valid_breakdown.agent_specs[1].context_refs = [" brief-1 "]

    with pytest.raises(BreakdownValidationError, match=r"INVALID_SOURCE_REF \[t-api\]"):
        validate_breakdown(valid_breakdown, valid_spec)


def test_semantic_wording_is_not_guessed_by_structural_validation(valid_breakdown, valid_spec):
    """Exclusion semantics are reviewed by the dedicated Reviewer Agent, not regexes."""
    valid_spec.exclusions = ["External deployment automation"]
    valid_breakdown.agent_specs[1].fixed_constraints = ["Build External-deployment automation"]

    validate_breakdown(valid_breakdown, valid_spec)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("scope", ["  "], "INVALID_AGENT_SPEC_FIELD"),
        ("context_refs", ["\t"], "INVALID_AGENT_SPEC_FIELD"),
        ("inputs", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("fixed_constraints", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("configurable_parts", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("extension_points", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("required_skills", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("allowed_tools", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("allowed_paths", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("responsible_role", " ", "INVALID_AGENT_SPEC_FIELD"),
        ("suggested_assignee", "\t", "INVALID_AGENT_SPEC_FIELD"),
        ("dependency_keys", [" "], "INVALID_DEPENDENCY"),
        ("test_obligations", [" "], "INVALID_AGENT_SPEC_FIELD"),
        ("risks", [" "], "INVALID_AGENT_SPEC_FIELD"),
    ],
)
def test_agent_spec_blank_structured_fields_are_rejected(
    valid_breakdown, valid_spec, field, value, code
):
    proposal = valid_breakdown.agent_specs[0].model_copy(update={field: value})
    valid_breakdown.agent_specs[0] = proposal

    with pytest.raises(BreakdownValidationError) as error:
        validate_breakdown(valid_breakdown, valid_spec)

    assert error.value.code == code
    assert error.value.local_key == "t-domain"


def test_blank_output_acceptance_and_open_question_fields_are_rejected(valid_breakdown, valid_spec):
    proposal = valid_breakdown.agent_specs[0]
    invalid = proposal.model_copy(
        update={
            "outputs": [proposal.outputs[0].model_copy(update={"name": " "})],
        }
    )
    valid_breakdown.agent_specs[0] = invalid

    with pytest.raises(BreakdownValidationError) as error:
        validate_breakdown(valid_breakdown, valid_spec)

    assert (error.value.code, error.value.local_key) == ("INVALID_OUTPUT", "t-domain")


@pytest.mark.parametrize("requirement_id", [" ", "FR-999"])
def test_acceptance_rejects_blank_and_unknown_requirement_ids(
    valid_breakdown, valid_spec, requirement_id
):
    proposal = valid_breakdown.agent_specs[0]
    criterion = proposal.acceptance_criteria[0]
    proposal.acceptance_criteria[0] = criterion.model_copy(
        update={"requirement_ids": [requirement_id]}
    )

    with pytest.raises(BreakdownValidationError) as error:
        validate_breakdown(valid_breakdown, valid_spec)

    expected = "INVALID_ACCEPTANCE" if not requirement_id.strip() else "UNKNOWN_REQUIREMENT_ID"
    assert (error.value.code, error.value.local_key) == (expected, "t-domain")


def test_blank_open_question_is_rejected_with_task_key(valid_breakdown, valid_spec):
    valid_breakdown.agent_specs[0].open_questions = [
        OpenQuestion.model_construct(
            question=" ",
            blocking=False,
            risk_owner="owner",
            accepted_consequence="accepted",
        )
    ]

    with pytest.raises(BreakdownValidationError) as error:
        validate_breakdown(valid_breakdown, valid_spec)

    assert (error.value.code, error.value.local_key) == ("INVALID_OPEN_QUESTION", "t-domain")
