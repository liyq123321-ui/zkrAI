"""Pure dependency graph helpers for staged task planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from app.domain.types import AgentSpecProposal


def _index_tasks(tasks: Sequence[AgentSpecProposal]) -> dict[str, AgentSpecProposal]:
    indexed: dict[str, AgentSpecProposal] = {}
    for task in tasks:
        if task.work_item_key in indexed:
            raise ValueError(f"duplicate task key: {task.work_item_key}")
        indexed[task.work_item_key] = task
    for task in tasks:
        for dependency in task.dependency_keys:
            if dependency not in indexed:
                raise ValueError(f"unknown dependency key: {dependency}")
    return indexed


def topological_plan_waves(
    tasks: Sequence[AgentSpecProposal], selected_keys: set[str] | None = None
) -> list[list[AgentSpecProposal]]:
    """Return deterministic Kahn layers, optionally for a selected induced graph."""
    indexed = _index_tasks(tasks)
    selected = set(indexed) if selected_keys is None else set(selected_keys)
    unknown = selected - indexed.keys()
    if unknown:
        raise ValueError(f"unknown selected key: {next(iter(unknown))}")

    remaining = {key: 0 for key in selected}
    dependents: dict[str, list[str]] = {key: [] for key in selected}
    for key in selected:
        for dependency in indexed[key].dependency_keys:
            if dependency in selected:
                remaining[key] += 1
                dependents[dependency].append(key)

    waves: list[list[AgentSpecProposal]] = []
    completed: set[str] = set()
    while len(completed) < len(selected):
        wave_keys = [task.work_item_key for task in tasks if task.work_item_key in selected and remaining[task.work_item_key] == 0 and task.work_item_key not in completed]
        if not wave_keys:
            raise ValueError("dependency cycle detected")
        waves.append([indexed[key] for key in wave_keys])
        for key in wave_keys:
            completed.add(key)
            for dependent in dependents[key]:
                remaining[dependent] -= 1
    return waves


def transitive_dependent_keys(
    tasks: Sequence[AgentSpecProposal], seed_keys: set[str]
) -> set[str]:
    indexed = _index_tasks(tasks)
    unknown = set(seed_keys) - indexed.keys()
    if unknown:
        raise ValueError(f"unknown seed key: {next(iter(unknown))}")
    reverse: dict[str, list[str]] = {key: [] for key in indexed}
    for task in tasks:
        for dependency in task.dependency_keys:
            reverse[dependency].append(task.work_item_key)
    result = set(seed_keys)
    pending = list(seed_keys)
    while pending:
        key = pending.pop()
        for dependent in reverse[key]:
            if dependent not in result:
                result.add(dependent)
                pending.append(dependent)
    return result


def dependency_contracts(
    task: AgentSpecProposal, tasks_by_key: Mapping[str, AgentSpecProposal]
) -> list[dict[str, object]]:
    contracts: list[dict[str, object]] = []
    for dependency_key in task.dependency_keys:
        if dependency_key not in tasks_by_key:
            raise ValueError(f"unknown dependency key: {dependency_key}")
        dependency = tasks_by_key[dependency_key]
        if dependency.implementation_plan is None:
            raise ValueError(f"dependency has no validated implementation plan: {dependency_key}")
        snapshot = dependency.model_dump(mode="json")
        plan = snapshot.pop("implementation_plan")
        canonical = json.dumps(
            {"task_spec": snapshot, "implementation_plan": plan},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        contracts.append({
            "work_item_key": dependency_key,
            "task_spec": snapshot,
            "implementation_plan": plan,
            "contract_hash": hashlib.sha256(canonical).hexdigest(),
        })
    return contracts
