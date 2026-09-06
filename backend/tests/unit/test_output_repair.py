"""Exercise automatic repairs through the real gateway and structured runner."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.agents.codex import AgentOutputError
from app.agents.output_validation import OutputConsistencyError, validate_node_output
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
async def test_generated_spec_repairs_invalid_drawio_xml(tmp_path, monkeypatch, valid_spec):
    diagram = {
        "diagram_id": "data-model",
        "title": "Core data model",
        "after_section": "core_objects",
        "drawio_xml": (
            '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="project" value="Project" style="shape=table;html=1;" vertex="1" parent="1">'
            '<mxGeometry width="180" height="100" as="geometry"/></mxCell>'
            '<mxCell id="session" value="Session" style="shape=table;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="240" width="180" height="100" as="geometry"/></mxCell>'
            '<mxCell id="owns" value="owns" edge="1" parent="1" source="project" target="session">'
            '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>'
        ),
    }
    good_payload = valid_spec.model_dump(mode="json")
    good_payload["er_diagrams"] = [diagram]
    good = type(valid_spec).model_validate(good_payload)
    bad_payload = good.model_dump(mode="json")
    bad_payload["er_diagrams"][0]["drawio_xml"] = "<mxfile><diagram>compressed</diagram></mxfile>"
    gateway, prompts = gateway_with_outputs(
        tmp_path,
        monkeypatch,
        [json.dumps(bad_payload), good],
    )

    result = await gateway.generate_spec({"input_refs": ["artifact:brief-1"]})

    assert result.er_diagrams[0].diagram_id == "data-model"
    assert len(prompts) == 2
    assert "uncompressed" in prompts[1]


def test_new_agent_er_table_fields_are_normalized_into_horizontal_cell_children(valid_spec):
    diagram = {
        "diagram_id": "data-model",
        "title": "Core data model",
        "after_section": "core_objects",
        "drawio_xml": (
            '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="project" value="Project" style="shape=table;html=1;" vertex="1" parent="1">'
            '<mxGeometry width="180" height="100" as="geometry"/></mxCell>'
            '<mxCell id="session" value="Session" style="shape=table;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="240" width="180" height="100" as="geometry"/></mxCell>'
            '<mxCell id="project-name-row" value="Project name" style="shape=tableRow;html=1;" vertex="1" parent="project">'
            '<mxGeometry y="30" width="180" height="30" as="geometry"/></mxCell>'
            '<mxCell id="owns" value="owns" edge="1" parent="1" source="project" target="session">'
            '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>'
        ),
    }
    payload = valid_spec.model_dump(mode="json")
    payload["er_diagrams"] = [diagram]
    generated = type(valid_spec).model_validate(payload)

    validate_node_output(generated, {"input_refs": generated.source_refs})

    normalized = generated.er_diagrams[0].drawio_xml
    assert 'id="project-name-row" value=""' in normalized
    assert 'id="project-name-row-cell" value="Project name"' in normalized
    assert 'shape=partialRectangle;html=1;whiteSpace=wrap;' in normalized
    assert 'parent="project-name-row"' in normalized


def test_new_agent_er_table_rows_accept_the_canonical_horizontal_cell(valid_spec):
    xml_prefix = (
        '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="project" value="Project" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry width="180" height="100" as="geometry"/></mxCell>'
        '<mxCell id="session" value="Session" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry x="240" width="180" height="100" as="geometry"/></mxCell>'
    )
    xml_suffix = (
        '<mxCell id="owns" value="owns" edge="1" parent="1" source="project" target="session">'
        '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>'
    )
    row = (
        '<mxCell id="project-name-row" value="" style="shape=tableRow;horizontal=0;" vertex="1" parent="project">'
        '<mxGeometry y="30" width="180" height="30" as="geometry"/></mxCell>'
    )
    cell = (
        '<mxCell id="project-name-cell" value="Project name" '
        'style="shape=partialRectangle;html=1;whiteSpace=wrap;" vertex="1" parent="project-name-row">'
        '<mxGeometry width="180" height="30" as="geometry"/></mxCell>'
    )

    def generated(xml: str):
        payload = valid_spec.model_dump(mode="json")
        payload["er_diagrams"] = [{
            "diagram_id": "data-model", "title": "Core data model", "after_section": "core_objects", "drawio_xml": xml,
        }]
        return type(valid_spec).model_validate(payload)

    valid = generated(xml_prefix + row + cell + xml_suffix)
    validate_node_output(valid, {"input_refs": valid.source_refs})


def test_new_agent_er_normalizes_relative_one_by_one_field_cells_to_the_row_size(valid_spec):
    """A 1×1 relative child is present in the accessibility tree but invisible in draw.io."""
    xml_prefix = (
        '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="project" value="Project" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry width="180" height="100" as="geometry"/></mxCell>'
        '<mxCell id="session" value="Session" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry x="240" width="180" height="100" as="geometry"/></mxCell>'
    )
    row = (
        '<mxCell id="project-name-row" value="" style="shape=tableRow;horizontal=0;" vertex="1" parent="project">'
        '<mxGeometry y="30" width="180" height="30" as="geometry"/></mxCell>'
        '<mxCell id="project-name-cell" value="Project name" '
        'style="shape=partialRectangle;html=1;whiteSpace=wrap;" vertex="1" parent="project-name-row">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry"/></mxCell>'
    )
    xml_suffix = (
        '<mxCell id="owns" value="owns" edge="1" parent="1" source="project" target="session">'
        '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>'
    )
    payload = valid_spec.model_dump(mode="json")
    payload["er_diagrams"] = [{
        "diagram_id": "data-model", "title": "Core data model", "after_section": "core_objects",
        "drawio_xml": xml_prefix + row + xml_suffix,
    }]
    generated = type(valid_spec).model_validate(payload)

    validate_node_output(generated, {"input_refs": generated.source_refs})

    normalized = generated.er_diagrams[0].drawio_xml
    assert '<mxGeometry width="180" height="30" as="geometry"' in normalized
    assert 'id="project-name-cell"' in normalized
    assert 'relative="1"' not in normalized.split('id="project-name-cell"', 1)[1].split('</mxCell>', 1)[0]


def test_new_agent_er_accepts_multiple_field_cells_and_normalizes_each_geometry(valid_spec):
    """Extra field-layout cells must not block an otherwise renderable ER diagram."""
    xml_prefix = (
        '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="project" value="Project" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry width="180" height="100" as="geometry"/></mxCell>'
        '<mxCell id="session" value="Session" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry x="240" width="180" height="100" as="geometry"/></mxCell>'
    )
    row_and_cells = (
        '<mxCell id="project-name-row" value="" style="shape=tableRow;horizontal=0;" vertex="1" parent="project">'
        '<mxGeometry y="30" width="180" height="30" as="geometry"/></mxCell>'
        '<mxCell id="project-name" value="Project name" '
        'style="shape=partialRectangle;html=1;whiteSpace=wrap;" vertex="1" parent="project-name-row">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry"/></mxCell>'
        '<mxCell id="project-code" value="Project code" '
        'style="shape=partialRectangle;html=1;whiteSpace=wrap;" vertex="1" parent="project-name-row">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry"/></mxCell>'
        '<mxCell id="project-layout-marker" value="" style="shape=label;" vertex="1" parent="project-name-row">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry"/></mxCell>'
    )
    xml_suffix = (
        '<mxCell id="owns" value="owns" edge="1" parent="1" source="project" target="session">'
        '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>'
    )
    payload = valid_spec.model_dump(mode="json")
    payload["er_diagrams"] = [{
        "diagram_id": "data-model", "title": "Core data model", "after_section": "core_objects",
        "drawio_xml": xml_prefix + row_and_cells + xml_suffix,
    }]
    generated = type(valid_spec).model_validate(payload)

    validate_node_output(generated, {"input_refs": generated.source_refs})

    normalized = generated.er_diagrams[0].drawio_xml
    for field_id in ("project-name", "project-code"):
        field = normalized.split(f'id="{field_id}"', 1)[1].split("</mxCell>", 1)[0]
        assert 'width="180" height="30"' in field
        assert 'relative="1"' not in field


def test_new_agent_er_removes_empty_table_layout_rows_from_the_rendered_table(valid_spec):
    """Blank layout rows must not create a visible extra field row."""
    xml_prefix = (
        '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="project" value="Project" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry width="180" height="100" as="geometry"/></mxCell>'
        '<mxCell id="session" value="Session" style="shape=table;html=1;" vertex="1" parent="1">'
        '<mxGeometry x="240" width="180" height="100" as="geometry"/></mxCell>'
    )
    row = (
        '<mxCell id="project-name-row" value="Project name" style="shape=tableRow;html=1;" vertex="1" parent="project">'
        '<mxGeometry y="30" width="180" height="30" as="geometry"/></mxCell>'
        '<mxCell id="project-layout-row" value="" style="shape=tableRow;html=1;" vertex="1" parent="project">'
        '<mxGeometry y="60" width="180" height="30" as="geometry"/></mxCell>'
    )
    xml_suffix = (
        '<mxCell id="owns" value="owns" edge="1" parent="1" source="project" target="session">'
        '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>'
    )
    payload = valid_spec.model_dump(mode="json")
    payload["er_diagrams"] = [{
        "diagram_id": "data-model", "title": "Core data model", "after_section": "core_objects",
        "drawio_xml": xml_prefix + row + xml_suffix,
    }]
    generated = type(valid_spec).model_validate(payload)

    validate_node_output(generated, {"input_refs": generated.source_refs})

    normalized = generated.er_diagrams[0].drawio_xml
    assert 'id="project-name-row-cell" value="Project name"' in normalized
    assert 'id="project-layout-row"' not in normalized
    assert '<mxGeometry width="180" height="60" as="geometry"' in normalized


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
@pytest.mark.parametrize("defect", ["dependency", "coverage", "source"])
async def test_decomposition_repairs_related_structural_gaps(
    tmp_path, monkeypatch, valid_spec, valid_breakdown, defect
):
    broken = valid_breakdown.model_copy(deep=True)
    if defect == "dependency":
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
async def test_base_decomposition_discards_embedded_plans_before_validation(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    """A base-stage response must not fail because the model emitted a bad full plan."""

    candidate = valid_breakdown.model_copy(deep=True)
    candidate.agent_specs[0].implementation_plan.steps[0].requirement_ids = ["FR-999"]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [candidate])

    result = await gateway.decompose_spec({
        "decomposition_stage": "base",
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": valid_spec.source_refs,
    })

    assert all(task.implementation_plan is None for task in result.agent_specs)
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_base_revision_discards_plans_retained_from_unchanged_tasks(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    changed = valid_breakdown.agent_specs[1].model_copy(deep=True)
    changed.extension_points = ["Only owner-approved sources"]
    revision = json.dumps({
        "milestones": [],
        "tasks": [],
        "agent_specs": [changed.model_dump(mode="json")],
    })
    gateway, _ = gateway_with_outputs(tmp_path, monkeypatch, [revision])

    result = await gateway.decompose_spec({
        "decomposition_stage": "base",
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": valid_spec.source_refs,
        "previous_breakdown": valid_breakdown.model_dump(mode="json"),
        "review_feedback": {"verdict": "REJECT", "findings": []},
    })

    assert all(task.implementation_plan is None for task in result.agent_specs)


@pytest.mark.asyncio
async def test_decomposition_promotes_only_the_first_output_when_all_are_optional(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    broken = valid_breakdown.model_copy(deep=True)
    first = broken.agent_specs[0].outputs[0].model_copy(update={"required": False})
    second = first.model_copy(update={"name": "model documentation"})
    broken.agent_specs[0].outputs = [first, second]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [broken])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": valid_spec.source_refs,
    })

    assert [item.required for item in result.agent_specs[0].outputs] == [True, False]
    assert [item.name for item in result.agent_specs[0].outputs] == [
        "models",
        "model documentation",
    ]
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_decomposition_revision_promotes_an_existing_optional_output(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    revised = valid_breakdown.agent_specs[0].model_copy(deep=True)
    revised.outputs[0].required = False
    revision = json.dumps({
        "milestones": [],
        "tasks": [],
        "agent_specs": [revised.model_dump(mode="json")],
    })
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [revision])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": valid_spec.source_refs,
        "previous_breakdown": valid_breakdown.model_dump(mode="json"),
        "review_feedback": {"verdict": "REJECT", "findings": []},
    })

    assert result.agent_specs[0].outputs[0].required is True
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_decomposition_does_not_normalize_an_empty_output_list(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    invalid = valid_breakdown.model_dump(mode="json")
    invalid["agent_specs"][0]["outputs"] = []
    encoded = json.dumps(invalid)
    gateway, prompts = gateway_with_outputs(
        tmp_path, monkeypatch, [encoded, encoded, encoded]
    )

    with pytest.raises(AgentOutputError, match="outputs"):
        await gateway.decompose_spec({
            "approved_spec": valid_spec.model_dump(mode="json"),
            "input_refs": valid_spec.source_refs,
        })

    assert len(prompts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mistake", "mistyped"),
    [
        ("insertion", "artifact:source-abcdefx"),
        ("deletion", "artifact:source-abcde"),
        ("substitution", "artifact:source-abcdxf"),
        ("transposition", "artifact:source-abcedf"),
    ],
)
async def test_decomposition_repairs_a_unique_single_edit_source_ref(
    tmp_path, monkeypatch, valid_spec, valid_breakdown, mistake, mistyped
):
    """The gateway canonicalizes only an unambiguous single edit before validation."""
    approved = "artifact:source-abcdef"
    broken = valid_breakdown.model_copy(deep=True)
    for agent_spec in broken.agent_specs:
        agent_spec.context_refs = [approved]
    broken.agent_specs[0].context_refs = [mistyped]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [broken])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": [approved],
    })

    assert result.agent_specs[0].context_refs == [approved], mistake
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_decomposition_repairs_a_unique_source_ref_in_a_valid_revision(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    approved = valid_spec.source_refs[0]
    mistyped = "artifact:brier-1"
    revised = valid_breakdown.agent_specs[1].model_copy(deep=True)
    revised.context_refs = [mistyped]
    revision = json.dumps({
        "milestones": [],
        "tasks": [],
        "agent_specs": [revised.model_dump(mode="json")],
    })
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [revision])

    result = await gateway.decompose_spec({
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": [approved],
        "previous_breakdown": valid_breakdown.model_dump(mode="json"),
        "review_feedback": {"verdict": "REJECT", "findings": []},
    })

    assert result.agent_specs[1].context_refs == [approved]
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_decomposition_does_not_guess_when_source_ref_typo_is_ambiguous(
    tmp_path, monkeypatch, valid_spec, valid_breakdown
):
    allowed = ["artifact:source-abc", "artifact:source-abd"]
    ambiguous = "artifact:source-abe"
    broken = valid_breakdown.model_copy(deep=True)
    for agent_spec in broken.agent_specs:
        agent_spec.context_refs = [allowed[0]]
    broken.agent_specs[0].context_refs = [ambiguous]
    gateway, prompts = gateway_with_outputs(
        tmp_path, monkeypatch, [broken, broken, broken]
    )

    with pytest.raises(AgentOutputError, match="INVALID_SOURCE_REF"):
        await gateway.decompose_spec({
            "approved_spec": valid_spec.model_dump(mode="json"),
            "input_refs": allowed,
        })

    assert len(prompts) == 3


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
