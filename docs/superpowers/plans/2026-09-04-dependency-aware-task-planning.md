# Dependency-Aware Task Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan every child Agent Spec in dependency order, pass validated upstream contracts to downstream planners, and invalidate downstream plans when an upstream contract is repaired.

**Architecture:** Add a small pure graph module that computes topological planning waves, transitive dependents, and dependency-contract payloads from the existing `dependency_keys` DAG. Both decomposition and legacy AgentSpec enrichment consume these helpers; each planning wave retains bounded concurrency, while Agent-call checkpoints bind downstream results to exact upstream contract hashes.

**Tech Stack:** Python 3.12, asyncio, Pydantic v2, SQLAlchemy, pytest/pytest-asyncio.

## Global Constraints

- Keep the complete base task decomposition as one validated, plan-free result.
- Preserve the existing maximum of two simultaneous `plan_task` calls.
- Tasks in one dependency layer may run concurrently; a downstream layer starts only after all selected prerequisites have validated plans.
- Repairing an upstream task also replans all of its transitive dependents, but leaves unrelated branches unchanged.
- Continue to persist WorkItems, dependencies, and AgentSpecs only after full validation and semantic review pass.
- Use UUIDs and canonical hashes for durable bindings; never look up persisted resources by display name.
- Do not merge implementation and test tasks automatically based on project size.
- Do not add a database table, third-party dependency, or new Agent operation.

---

### Task 1: Pure Dependency Planning Graph

**Files:**
- Create: `backend/app/services/task_plan_graph.py`
- Create: `backend/tests/unit/test_task_plan_graph.py`

**Interfaces:**
- Consumes: `Sequence[AgentSpecProposal]` whose `work_item_key` and `dependency_keys` already passed base-breakdown validation.
- Produces: `topological_plan_waves(tasks, selected_keys=None) -> list[list[AgentSpecProposal]]`.
- Produces: `transitive_dependent_keys(tasks, seed_keys) -> set[str]`.
- Produces: `dependency_contracts(task, tasks_by_key) -> list[dict[str, object]]`, with one ordered entry per direct dependency containing `work_item_key`, `task_spec`, `implementation_plan`, and `contract_hash`.

- [ ] **Step 1: Write failing graph-order tests**

Create tests with four lightweight `AgentSpecProposal` values arranged as `T1 -> T2 -> T3` plus independent `T4`. Assert:

```python
waves = topological_plan_waves(tasks)
assert [[task.work_item_key for task in wave] for wave in waves] == [
    ["T1", "T4"], ["T2"], ["T3"],
]

selected = topological_plan_waves(tasks, {"T2", "T3"})
assert [[task.work_item_key for task in wave] for wave in selected] == [
    ["T2"], ["T3"],
]
```

Also assert unknown selected keys, unknown dependency keys, and cycles raise `ValueError` before any scheduling result is returned.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/unit/test_task_plan_graph.py`

Expected: FAIL because `app.services.task_plan_graph` does not exist.

- [ ] **Step 3: Implement deterministic topological waves**

Implement Kahn layering over the selected induced subgraph. Preserve the input task order within each wave, while dependencies outside `selected_keys` count as already completed. Reject duplicate task keys, unknown dependencies, unknown selected keys, and cycles.

- [ ] **Step 4: Write failing dependent-closure and contract tests**

Assert:

```python
assert transitive_dependent_keys(tasks, {"T1"}) == {"T1", "T2", "T3"}
assert transitive_dependent_keys(tasks, {"T2"}) == {"T2", "T3"}
assert transitive_dependent_keys(tasks, {"T4"}) == {"T4"}

contracts = dependency_contracts(t2, {task.work_item_key: task for task in tasks})
assert [entry["work_item_key"] for entry in contracts] == ["T1"]
assert contracts[0]["implementation_plan"] == t1.implementation_plan.model_dump(mode="json")
assert len(contracts[0]["contract_hash"]) == 64
```

Verify `dependency_contracts` rejects a dependency with no validated plan, and that changing the upstream plan changes `contract_hash`.

- [ ] **Step 5: Run the focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/unit/test_task_plan_graph.py`

Expected: FAIL because the closure and contract functions are not implemented.

- [ ] **Step 6: Implement closure and canonical contract serialization**

Use a reverse adjacency map for iterative dependent expansion. Serialize the complete upstream task snapshot and plan with sorted canonical JSON, then SHA-256 that combined value. Return direct dependency entries in the consumer's declared dependency order.

- [ ] **Step 7: Run the focused tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/unit/test_task_plan_graph.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/task_plan_graph.py backend/tests/unit/test_task_plan_graph.py
git commit -m "feat: add task planning graph helpers"
```

### Task 2: Dependency-Ordered Initial Decomposition

**Files:**
- Modify: `backend/app/services/decomposition_service.py`
- Modify: `backend/prompts/nodes/pm_plan_task.txt`
- Modify: `backend/tests/integration/test_decomposition_service.py`

**Interfaces:**
- Consumes: `topological_plan_waves` and `dependency_contracts` from Task 1.
- Produces: `_plan_tasks(...) -> list[str]` with the existing public behavior and canonical Agent Spec evidence order.
- Adds to every planner payload: `dependency_contracts: list[dict[str, object]]` and `dependency_contract_hashes: dict[str, str]`.

- [ ] **Step 1: Write a failing dependent-start-order test**

Add a gateway whose T1 planner waits on an event and whose T2 planner records whether T1 completed. Give T2 `dependency_keys=["T1"]`. Assert T2 never starts while T1 is active, then assert the call order is T1 followed by T2.

- [ ] **Step 2: Write a failing contract-payload test**

Capture T2's payload and assert its `dependency_contracts` contains the exact validated T1 task and plan, plus a matching `dependency_contract_hashes["T1"]`. Assert T1 receives an empty dependency-contract list.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/integration/test_decomposition_service.py -k "dependency or staged_decomposition_plans_each"`

Expected: FAIL because `_plan_tasks` still gathers all missing task plans in one wave and payloads lack dependency contracts.

- [ ] **Step 4: Schedule one topological wave at a time**

Replace the single all-target `asyncio.gather` in `_plan_tasks` with a loop over `topological_plan_waves`. Gather only one wave, settle every started result, attach all successful validated plans, and stop before later waves after a failure. Accumulate call UUIDs by task key, then return them in the original `breakdown.agent_specs` order for tasks actually planned.

- [ ] **Step 5: Add authoritative dependency contracts to `_plan_task`**

Build contracts immediately before checkpoint lookup so every dependency plan is already attached. Store both full `dependency_contracts` and the compact key-to-hash binding in the AgentCall request. Keep `related_tasks` for non-authoritative context.

- [ ] **Step 6: Bind checkpoint adoption to upstream hashes**

Extend `_task_plan_checkpoint` to require exact equality for `dependency_contract_hashes` and `dependency_contracts`. This makes an old T2 result ineligible whenever T1's task snapshot or plan changes, while unrelated branch results remain reusable.

- [ ] **Step 7: Update the planner prompt**

State that `dependency_contracts` are validated authoritative upstream handoffs, the consumer must reuse their exact schemas/locators/enums or define an explicit adapter, and `related_tasks` without a plan are context only.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/integration/test_decomposition_service.py`

Expected: PASS, including the existing independent-task maximum concurrency of two.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/decomposition_service.py backend/prompts/nodes/pm_plan_task.txt backend/tests/integration/test_decomposition_service.py
git commit -m "feat: plan decomposed tasks in dependency order"
```

### Task 3: Dependency-Aware Semantic Plan Repair and Recovery

**Files:**
- Modify: `backend/app/services/decomposition_service.py`
- Modify: `backend/tests/integration/test_semantic_breakdown_repair.py`

**Interfaces:**
- Consumes: `transitive_dependent_keys` and dependency-aware `_plan_tasks`.
- Produces: `_plan_repair_targets(...)` expanded to every transitive dependent of a directly implicated plan.
- Preserves: one current plan-call UUID per Agent Spec in canonical breakdown order.

- [ ] **Step 1: Write a failing upstream-repair cascade test**

Construct T2 with `dependency_keys=["T1"]`. Return a blocking Reviewer finding at `agent_specs[T1].implementation_plan.interfaces[0]`, then PASS. Assert the second round replans T1 first, T2 second, and the repaired T2 payload contains the newly repaired T1 contract rather than the initial one.

- [ ] **Step 2: Write a failing leaf-repair isolation test**

Return a finding only for T2 and assert the repair round replans T2 without replanning T1. Verify the retained T1 plan appears in T2's authoritative dependency contract.

- [ ] **Step 3: Write a failing recovery-hash test**

Create a first command where T1 succeeds and T2 fails. On retry, verify T1 is adopted from its checkpoint. Then exercise an upstream repaired-plan change and assert the old T2 checkpoint is not adopted, while an independent task checkpoint is adopted.

- [ ] **Step 4: Run focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/integration/test_semantic_breakdown_repair.py -k "cascade or leaf or checkpoint"`

Expected: FAIL because repair targets currently contain only explicitly mentioned tasks and checkpoints do not include upstream contract hashes.

- [ ] **Step 5: Expand upstream repair targets to dependent closure**

Keep exact-path parsing for direct findings. If parsing falls back to all tasks, schedule all tasks topologically. Otherwise compute `transitive_dependent_keys`, select those tasks in canonical Agent Spec order, save their previous plans, clear only that set, and delegate scheduling to dependency-aware `_plan_tasks`.

- [ ] **Step 6: Keep evidence mapping deterministic**

Continue mapping repaired AgentCall UUIDs by the persisted request's exact `task_spec.work_item_key`. Update `plan_calls_by_key`, then build Reviewer evidence in original Agent Spec order. Do not infer mappings from completion or finding order.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/integration/test_semantic_breakdown_repair.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/decomposition_service.py backend/tests/integration/test_semantic_breakdown_repair.py
git commit -m "fix: cascade task plan repairs through dependencies"
```

### Task 4: Align Existing AgentSpec Enrichment

**Files:**
- Modify: `backend/app/services/agent_spec_details.py`
- Modify: `backend/tests/integration/test_agent_spec_details.py`

**Interfaces:**
- Consumes: all three Task 1 graph helpers.
- Produces: dependency-ordered planning and repair in `AgentSpecDetailService.enrich` without changing its external API.
- Adds to persisted enrichment AgentCall requests: exact `dependency_contracts` and `dependency_contract_hashes`.

- [ ] **Step 1: Write a failing enrichment-order test**

Use the persisted WorkItem dependency in `legacy_project`. Record active planner keys and assert the dependent task starts only after its producer's plan is attached. Assert unrelated root-layer tasks can still reach a peak concurrency of two.

- [ ] **Step 2: Write a failing enrichment-repair cascade test**

Reject the producer plan, then PASS. Assert the repair round regenerates the producer and all transitive consumers in topological order, while independent plans remain unchanged.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/integration/test_agent_spec_details.py -k "dependency or repair"`

Expected: FAIL because enrichment currently gathers all targets concurrently and repairs only direct findings.

- [ ] **Step 4: Replace round-wide gather with shared planning waves**

For each repair round, expand direct targets through `transitive_dependent_keys`, compute topological waves, then gather one wave at a time with the existing semaphore. Attach all validated wave plans before starting the next wave. Preserve full-round atomic persistence.

- [ ] **Step 5: Add dependency contracts to enrichment calls**

Construct authoritative contracts from the reconstructed canonical breakdown immediately before each `_plan` call. Include their hashes in the durable request. Keep persisted `work_item_id`, `parent_work_item_id`, and `agent_spec_id` UUID bindings unchanged.

- [ ] **Step 6: Run focused and correction tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/integration/test_agent_spec_details.py tests/integration/test_agent_spec_detail_corrections.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/agent_spec_details.py backend/tests/integration/test_agent_spec_details.py
git commit -m "feat: order AgentSpec enrichment by dependencies"
```

### Task 5: Regression and Runtime Verification

**Files:**
- Modify only if a regression test exposes a defect in files already listed above.

**Interfaces:**
- Verifies all existing backend and frontend contracts; introduces no new interface.

- [ ] **Step 1: Run the dependency-planning regression group**

Run:

```bash
cd backend
.venv/bin/pytest -q \
  tests/unit/test_task_plan_graph.py \
  tests/integration/test_decomposition_service.py \
  tests/integration/test_semantic_breakdown_repair.py \
  tests/integration/test_agent_spec_details.py \
  tests/integration/test_agent_spec_detail_corrections.py
```

Expected: PASS.

- [ ] **Step 2: Run the complete backend suite under deterministic test identity**

Run: `cd backend && env FIRSTFLIGHT_IDENTITY_MODE=legacy .venv/bin/pytest -q`

Expected: PASS with no failed tests.

- [ ] **Step 3: Run frontend regression checks**

Run:

```bash
cd frontend
npm test
npm run lint
npm run build
```

Expected: all commands exit zero.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check && git status --short && git diff --stat 49ae5e3..HEAD`

Expected: no whitespace errors; only planned files changed; no runtime database, environment, or generated build artifacts are tracked.

- [ ] **Step 5: Commit any test-driven corrections**

If regression fixes were required, commit only the relevant planned files:

```bash
git add backend/app/services/task_plan_graph.py backend/app/services/decomposition_service.py backend/app/services/agent_spec_details.py backend/prompts/nodes/pm_plan_task.txt backend/tests/unit/test_task_plan_graph.py backend/tests/integration/test_decomposition_service.py backend/tests/integration/test_semantic_breakdown_repair.py backend/tests/integration/test_agent_spec_details.py
git commit -m "test: verify dependency-aware task planning"
```
