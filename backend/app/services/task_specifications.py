"""Validate actionable plans and copy their requirements from the approved PRD."""

from app.domain.implementation_plan import ImplementationPlan
from app.domain.types import AgentSpecProposal


class ImplementationPlanError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def task_requirement_ids(task: AgentSpecProposal) -> set[str]:
    return {rid for criterion in task.acceptance_criteria for rid in criterion.requirement_ids}


def validate_implementation_plan(plan: ImplementationPlan | None, task: AgentSpecProposal, approved_ids: set[str]) -> None:
    if plan is None:
        raise ImplementationPlanError("MISSING_IMPLEMENTATION_PLAN", "Task needs concrete implementation steps and interface definitions")
    # Re-parse to protect service callers that mutated a Pydantic model after creation.
    plan = ImplementationPlan.model_validate(plan.model_dump(mode="json"))
    assigned = task_requirement_ids(task)
    covered: set[str] = set()
    step_ids: set[str] = set()
    for step in plan.steps:
        key = step.step_id.strip()
        if key in step_ids:
            raise ImplementationPlanError("DUPLICATE_IMPLEMENTATION_STEP", f"Repeated step identifier: {key}")
        step_ids.add(key)
        refs = set(step.requirement_ids)
        if len(refs) != len(step.requirement_ids):
            raise ImplementationPlanError("DUPLICATE_IMPLEMENTATION_REQUIREMENT", f"Step {key} repeats a requirement")
        if refs - approved_ids:
            raise ImplementationPlanError("UNKNOWN_REQUIREMENT_ID", f"Step {key} references undefined requirements: {sorted(refs - approved_ids)}")
        if refs - assigned:
            raise ImplementationPlanError("UNASSIGNED_IMPLEMENTATION_REQUIREMENT", f"Step {key} exceeds its task's assigned requirements: {sorted(refs - assigned)}")
        covered.update(refs)
    if assigned - covered:
        raise ImplementationPlanError("MISSING_IMPLEMENTATION_COVERAGE", f"Requirements without implementation steps: {sorted(assigned - covered)}")
    names = [interface.name.strip() for interface in plan.interfaces]
    if len(names) != len(set(names)):
        raise ImplementationPlanError("DUPLICATE_INTERFACE", "Interface names must be unique within a task")
    names = [structure.name.strip() for structure in plan.data_structures]
    if len(names) != len(set(names)):
        raise ImplementationPlanError("DUPLICATE_DATA_STRUCTURE", "Data structure names must be unique within a task")
    for interface in plan.interfaces:
        for fields in (interface.inputs, interface.outputs):
            names = [field.name.strip() for field in fields]
            if len(names) != len(set(names)):
                raise ImplementationPlanError("DUPLICATE_INTERFACE_FIELD", f"Interface {interface.name} repeats an input/output field")


def requirement_snapshots(task: AgentSpecProposal, approved_spec: dict) -> list[dict]:
    ids = task_requirement_ids(task)
    return [dict(requirement) for section in ("functional_requirements", "non_functional_requirements")
            for requirement in approved_spec.get(section, []) if requirement["requirement_id"] in ids]
