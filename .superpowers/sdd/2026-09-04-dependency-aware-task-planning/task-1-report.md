# Task 1 report: Pure Dependency Planning Graph

## Implementation summary

Implemented deterministic dependency planning helpers in `backend/app/services/task_plan_graph.py`:

- `topological_plan_waves` validates keys, computes Kahn layers, preserves input order, supports selected induced subgraphs, and detects cycles.
- `transitive_dependent_keys` validates seeds and expands dependents iteratively through reverse adjacency.
- `dependency_contracts` returns direct dependencies in declared order, rejects unplanned dependencies, and hashes canonical JSON containing the upstream task snapshot and implementation plan.

## Tests and results

- Focused command (equivalent interpreter because the worktree has no local `.venv`):
  `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/unit/test_task_plan_graph.py`
  Result: **8 passed** (warnings from existing FastAPI/httpx compatibility and anyio deprecation notices).
- Full unit suite:
  `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/unit`
  Result: **224 passed** (same 2 pre-existing dependency warnings).

## RED/GREEN evidence

- RED after graph-order tests: collection failed with `ModuleNotFoundError: No module named 'app.services.task_plan_graph'`, confirming the production module was absent.
- Additional closure/contract tests were added before implementation; they likewise depended on the absent module.
- GREEN after implementation: focused suite passed all 8 tests.

## Changed files

- `backend/app/services/task_plan_graph.py`
- `backend/tests/unit/test_task_plan_graph.py`

## Self-review

The implementation is pure and does not mutate proposal objects or external state. Validation occurs before wave generation. Dependencies outside a selected induced subgraph are treated as already completed, and canonical serialization uses sorted keys and compact separators for stable SHA-256 hashes.

## Concerns

The task brief specifies `backend/.venv/bin/pytest`, but that virtualenv is not present in this isolated worktree. Tests were run with the existing equivalent interpreter from the main workspace. Pytest emits two existing dependency deprecation warnings.
