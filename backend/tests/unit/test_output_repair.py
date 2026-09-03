"""Exercise automatic repairs through the real gateway and structured runner."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.agents.codex import AgentOutputError
from app.services.decomposition_service import validate_breakdown
from app.services.spec_review import run_rule_review
from tests.helpers.scripted_codex import gateway_with_outputs


def test_services_can_load_before_gateway_in_a_fresh_process():
    backend = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-c", "from app.services.decomposition_service import DecompositionService"],
        cwd=backend, env={**os.environ, "PYTHONPATH": str(backend)}, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_generated_spec_repairs_unknown_reference_and_other_gaps_together(
    tmp_path, monkeypatch, valid_spec
):
    broken = valid_spec.model_copy(deep=True)
    broken.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    broken.acceptance_criteria[1].verification_method = "TBD"
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [broken, valid_spec])

    result = await gateway.generate_spec({"input_refs": ["artifact:brief-1"]})

    assert run_rule_review(result, {"artifact:brief-1"}) == []
    assert len(prompts) == 2
    assert "UNKNOWN_REQUIREMENT_ID" in prompts[1]
    assert "MISSING_ACCEPTANCE_COVERAGE" in prompts[1]
    assert "UNVERIFIABLE_ACCEPTANCE" in prompts[1]
    assert "deliverable-001" in prompts[1]


@pytest.mark.asyncio
async def test_rewrite_repairs_reference_without_losing_comment_responses(
    tmp_path, monkeypatch, valid_spec
):
    from app.domain.types import PrdRewriteOutput

    good = PrdRewriteOutput(
        spec=valid_spec, responses=[{"comment_id": 10, "action": "MODIFIED", "note": "Updated."}],
        change_summary="Corrected the acceptance reference.",
    )
    bad = good.model_copy(deep=True)
    bad.spec.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [bad, good])

    result = await gateway.rewrite_prd({
        "spec": {"content": valid_spec.model_dump(mode="json")}, "comment_ids": [10],
    })

    assert run_rule_review(result.spec, {"artifact:brief-1"}) == []
    assert result.responses[0].comment_id == 10
    assert len(prompts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("comment_ids", [[10, 999], [10, 10], [10]])
async def test_rewrite_repairs_missing_duplicate_and_unknown_comment_responses(
    tmp_path, monkeypatch, valid_spec, comment_ids
):
    from app.domain.types import PrdRewriteOutput

    good = PrdRewriteOutput(
        spec=valid_spec,
        responses=[{"comment_id": i, "action": "MODIFIED", "note": "Updated."} for i in [10, 20]],
        change_summary="Corrected references and addressed both comments.",
    )
    bad = good.model_copy(deep=True)
    bad.responses = [good.responses[0].model_copy(update={"comment_id": i}) for i in comment_ids]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [bad, good])

    result = await gateway.rewrite_prd({
        "spec": {"content": valid_spec.model_dump(mode="json")}, "comment_ids": [10, 20],
        "comments": [{"id": 10}, {"id": 20}],
    })

    assert [item.comment_id for item in result.responses] == [10, 20]
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_decomposition_repairs_legacy_reference_and_keeps_approved_spec(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    legacy = valid_spec.model_dump(mode="json")
    legacy["acceptance_criteria"][0]["requirement_ids"] = ["deliverable-001"]
    payload = {"approved_spec": legacy, "input_refs": ["artifact:brief-1"]}
    snapshot = json.dumps(payload, sort_keys=True)
    broken = valid_breakdown.model_copy(deep=True)
    broken.agent_specs[0].acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [broken, valid_breakdown])

    result = await gateway.decompose_spec(payload)

    validate_breakdown(result, valid_spec)
    assert json.dumps(payload, sort_keys=True) == snapshot
    assert len(prompts) == 2
    assert "UNKNOWN_REQUIREMENT_ID" in prompts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["outputs", "dependency", "coverage", "source"])
async def test_decomposition_repairs_related_structural_gaps(
    tmp_path, monkeypatch, valid_spec, valid_breakdown, defect
):
    broken = valid_breakdown.model_copy(deep=True)
    if defect == "outputs":
        broken.agent_specs[0].outputs[0].required = False
    elif defect == "dependency":
        broken.agent_specs[1].dependency_keys = ["absent-task"]
    elif defect == "coverage":
        for item in broken.agent_specs:
            for criterion in item.acceptance_criteria:
                criterion.requirement_ids = ["FR-001"]
    else:
        broken.agent_specs[0].context_refs = ["artifact:absent"]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [broken, valid_breakdown])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"), "input_refs": ["artifact:brief-1"],
    })

    validate_breakdown(result, valid_spec)
    assert result == valid_breakdown
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_schema_and_consistency_repairs_share_bounded_attempt_budget(
    tmp_path, monkeypatch, valid_spec
):
    broken = valid_spec.model_copy(deep=True)
    broken.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, ["{}", broken, broken, valid_spec])

    with pytest.raises(AgentOutputError, match="UNKNOWN_REQUIREMENT_ID"):
        await gateway.generate_spec({"input_refs": ["artifact:brief-1"]})

    assert len(prompts) == 3


@pytest.mark.asyncio
async def test_human_decisions_still_reach_review_instead_of_being_invented(
    tmp_path, monkeypatch, valid_spec
):
    from app.domain.types import OpenQuestion

    valid_spec.open_questions = [OpenQuestion(question="Who approves production access?", blocking=True)]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [valid_spec])

    result = await gateway.generate_spec({"input_refs": ["artifact:brief-1"]})

    assert result.open_questions[0].blocking
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_semantic_revision_merges_only_changed_tasks_and_preserves_the_rest(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    previous = valid_breakdown.model_dump(mode="json")
    changed = valid_breakdown.agent_specs[1].model_copy(deep=True)
    changed.extension_points = ["Only owner-approved sources"]
    revision = json.dumps({"milestones": [], "tasks": [], "agent_specs": [changed.model_dump(mode="json")]})
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [revision])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"), "input_refs": valid_spec.source_refs,
        "previous_breakdown": previous, "review_feedback": {"verdict": "REJECT", "findings": []},
    })

    assert len(prompts) == 1
    assert result.milestones == valid_breakdown.milestones
    assert result.tasks == valid_breakdown.tasks
    assert result.agent_specs[0] == valid_breakdown.agent_specs[0]
    assert result.agent_specs[1].extension_points == ["Only owner-approved sources"]
    assert previous == valid_breakdown.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown_key", "duplicate_key", "bad_requirement", "bad_dependency"])
async def test_partial_revision_checks_keys_and_validates_the_merged_breakdown(
    tmp_path, monkeypatch, valid_spec, valid_breakdown, invalid
):
    good = valid_breakdown.agent_specs[1].model_copy(deep=True)
    good.extension_points = ["Only owner-approved sources"]
    bad = good.model_copy(deep=True)
    patches = [bad]
    if invalid == "unknown_key":
        bad.work_item_key = "unapproved-new-task"
    elif invalid == "duplicate_key":
        patches.append(bad)
    elif invalid == "bad_requirement":
        bad.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    else:
        bad.dependency_keys = []
    encode = lambda items: json.dumps({"milestones": [], "tasks": [], "agent_specs": [i.model_dump(mode="json") for i in items]})
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [encode(patches), encode([good])])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"), "input_refs": valid_spec.source_refs,
        "previous_breakdown": valid_breakdown.model_dump(mode="json"),
    })

    validate_breakdown(result, valid_spec)
    assert result.agent_specs[0] == valid_breakdown.agent_specs[0]
    assert result.agent_specs[1] == good
    assert len(prompts) == 2
