import pytest

from app.domain.types import AgentSpecProposal
from app.domain.implementation_plan import ImplementationPlan
from app.services.task_plan_graph import (
    dependency_contracts,
    topological_plan_waves,
    transitive_dependent_keys,
)
from tests.helpers.implementation_plans import implementation_plan


def _task(key: str, dependencies: list[str] | None = None, plan=None) -> AgentSpecProposal:
    return AgentSpecProposal(
        work_item_key=key,
        objective=f"Objective for {key}",
        scope=["scope"],
        exclusions=[],
        context_refs=["context"],
        inputs=["input"],
        outputs=[{"name": "output", "format": "text", "required": True}],
        fixed_constraints=[],
        configurable_parts=[],
        extension_points=[],
        acceptance_criteria=[{
            "requirement_ids": ["FR-001"],
            "criterion": "criterion",
            "verification_method": "test",
            "expected_result": "result",
        }],
        required_skills=["python"],
        allowed_tools=[],
        allowed_paths=[],
        responsible_role="developer",
        suggested_assignee="agent",
        dependency_keys=dependencies or [],
        test_obligations=["tests"],
        risks=[],
        open_questions=[],
        implementation_plan=plan,
    )


def test_topological_plan_waves_preserves_input_order_within_layers():
    tasks = [_task("T1"), _task("T2", ["T1"]), _task("T3", ["T2"]), _task("T4")]

    waves = topological_plan_waves(tasks)
    assert [[task.work_item_key for task in wave] for wave in waves] == [
        ["T1", "T4"], ["T2"], ["T3"],
    ]

    selected = topological_plan_waves(tasks, {"T2", "T3"})
    assert [[task.work_item_key for task in wave] for wave in selected] == [["T2"], ["T3"]]


@pytest.mark.parametrize(
    "tasks, selected",
    [
        ([_task("T1")], {"missing"}),
        ([_task("T2", ["missing"])], None),
        ([_task("T1", ["T2"]), _task("T2", ["T1"])], None),
        ([_task("T1"), _task("T1")], None),
    ],
)
def test_topological_plan_waves_rejects_invalid_graphs(tasks, selected):
    with pytest.raises(ValueError):
        topological_plan_waves(tasks, selected)


def test_transitive_dependent_keys_returns_seed_and_all_dependents():
    tasks = [_task("T1"), _task("T2", ["T1"]), _task("T3", ["T2"]), _task("T4")]

    assert transitive_dependent_keys(tasks, {"T1"}) == {"T1", "T2", "T3"}
    assert transitive_dependent_keys(tasks, {"T2"}) == {"T2", "T3"}
    assert transitive_dependent_keys(tasks, {"T4"}) == {"T4"}


def test_dependency_contracts_include_ordered_plan_and_hash():
    t1 = _task("T1", plan=ImplementationPlan.model_validate(implementation_plan()))
    t2 = _task("T2", ["T1"])
    tasks = [t1, t2]

    contracts = dependency_contracts(t2, {task.work_item_key: task for task in tasks})
    assert [entry["work_item_key"] for entry in contracts] == ["T1"]
    assert contracts[0]["implementation_plan"] == t1.implementation_plan.model_dump(mode="json")
    assert len(contracts[0]["contract_hash"]) == 64

    changed = _task("T1", plan=ImplementationPlan.model_validate(implementation_plan(["FR-999"])))
    changed_contract = dependency_contracts(t2, {"T1": changed, "T2": t2})
    assert changed_contract[0]["contract_hash"] != contracts[0]["contract_hash"]


def test_dependency_contracts_rejects_unplanned_dependency():
    t1 = _task("T1")
    t2 = _task("T2", ["T1"])

    with pytest.raises(ValueError):
        dependency_contracts(t2, {"T1": t1, "T2": t2})
