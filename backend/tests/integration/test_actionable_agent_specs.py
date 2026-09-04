import json

import pytest

from app.domain.types import WorkBreakdown
from app.services.decomposition_service import BreakdownValidationError, DecompositionService, validate_breakdown
from tests.helpers.implementation_plans import implementation_plan
from tests.helpers.scripted_codex import gateway_with_outputs
from tests.integration.test_decomposition_service import _approved_project


def test_new_task_spec_cannot_omit_implementation_plan(valid_breakdown, valid_spec):
    raw = valid_breakdown.model_dump(mode="json")
    for spec in raw["agent_specs"]:
        spec.pop("implementation_plan", None)
    legacy = WorkBreakdown.model_validate(raw)
    with pytest.raises(BreakdownValidationError, match="MISSING_IMPLEMENTATION_PLAN"):
        validate_breakdown(legacy, valid_spec)


def test_task_spec_rejects_conflicting_duplicate_data_definitions(valid_breakdown, valid_spec):
    raw = valid_breakdown.model_dump(mode="json")
    plan = raw["agent_specs"][0]["implementation_plan"]
    original = plan["data_structures"][0]
    plan["data_structures"].append({**original, "name": f" {original['name']} ",
                                    "definition": "A conflicting definition for the same shared type"})
    with pytest.raises(BreakdownValidationError, match="DUPLICATE_DATA_STRUCTURE"):
        validate_breakdown(WorkBreakdown.model_validate(raw), valid_spec)


@pytest.mark.parametrize("defect,code", [
    ("unknown", "UNKNOWN_REQUIREMENT_ID"),
    ("uncovered", "MISSING_IMPLEMENTATION_COVERAGE"),
    ("duplicate", "DUPLICATE_IMPLEMENTATION_STEP"),
    ("foreign", "UNASSIGNED_IMPLEMENTATION_REQUIREMENT"),
])
def test_steps_cannot_lose_requirement_traceability(valid_breakdown, valid_spec, defect, code):
    raw = valid_breakdown.model_dump(mode="json")
    plan = implementation_plan()
    if defect == "unknown":
        plan["steps"][0]["requirement_ids"] = ["FR-999"]
    elif defect == "uncovered":
        plan["steps"][0]["requirement_ids"] = ["FR-001"]
    elif defect == "duplicate":
        plan["steps"] *= 2
    else:
        raw["agent_specs"][0]["implementation_plan"] = implementation_plan()
    raw["agent_specs"][1]["implementation_plan"] = plan
    with pytest.raises(BreakdownValidationError, match=code):
        validate_breakdown(WorkBreakdown.model_validate(raw), valid_spec)


@pytest.mark.asyncio
async def test_missing_plan_is_repaired_and_original_requirements_saved(
    tmp_path, monkeypatch, session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid = valid_breakdown.model_dump(mode="json")
    for spec in valid["agent_specs"]:
        ids = sorted({rid for ac in spec["acceptance_criteria"] for rid in ac["requirement_ids"]})
        spec["implementation_plan"] = implementation_plan(ids)
    legacy = valid_breakdown.model_dump(mode="json")
    for spec in legacy["agent_specs"]:
        spec.pop("implementation_plan", None)
    plans = [spec["implementation_plan"] for spec in valid["agent_specs"]]
    gateway, prompts = gateway_with_outputs(
        tmp_path, monkeypatch,
        [json.dumps(legacy), *map(json.dumps, plans), passing_semantic_review],
    )

    specs = await DecompositionService(session_factory, gateway).convert(project.id)

    assert len(prompts) == 4
    assert "base decomposition stage" in prompts[0]
    assert "ImplementationPlan" in prompts[1]
    api = next(s for s in specs if len(s.content["requirements"]) == 2)
    assert api.content["requirements"] == [
        {"requirement_id": "FR-001", "statement": "The system creates a workflow session from a brief", "priority": "MUST"},
        {"requirement_id": "NFR-001", "statement": "Commands are idempotent by command identifier", "priority": "MUST"},
    ]
    assert api.content["implementation_plan"]["interfaces"][0]["definition"] == "POST /sessions"
    assert "implementation_plan" in prompts[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["blank_method", "blank_type", "missing_interfaces", "unknown_requirement", "duplicate_field"])
async def test_task_planner_repairs_incomplete_methods_and_interface_contracts(
    tmp_path, monkeypatch, valid_breakdown, valid_spec, defect
):
    broken = implementation_plan()
    if defect == "blank_method":
        broken["steps"][0]["implementation_method"] = "  "
    elif defect == "blank_type":
        broken["interfaces"][0]["inputs"][0]["data_type"] = "\t"
    elif defect == "missing_interfaces":
        broken.pop("interfaces")
    elif defect == "unknown_requirement":
        broken["steps"][0]["requirement_ids"] = ["FR-999"]
    else:
        broken["interfaces"][0]["inputs"] *= 2
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [json.dumps(broken), json.dumps(implementation_plan())])

    result = await gateway.plan_task({
        "task_spec": valid_breakdown.agent_specs[1].model_dump(mode="json"),
        "approved_spec": valid_spec.model_dump(mode="json"),
    })

    assert len(prompts) == 2
    assert result.interfaces[0].inputs[0].data_type == "string"
    assert len(result.interfaces[0].inputs) == 1
    assert result.steps[0].requirement_ids == ["FR-001", "NFR-001"]
    assert result.steps[0].implementation_method.startswith("Validate the request model")
