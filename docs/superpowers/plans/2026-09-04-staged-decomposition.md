# Staged Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split decomposition into a recoverable base breakdown, independently validated per-task plans, and one final reviewed atomic publication.

**Architecture:** `DecompositionService` coordinates durable `AgentCall` checkpoints bound to the current project and approved Spec snapshot. `CodexAgentGateway` generates a plan-free base breakdown, task planners run with concurrency two, and materialization adopts the base, final plan, and Reviewer evidence only after full validation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic v2, asyncio, pytest.

## Global Constraints

- Keep the public `convert_to_work_item` command and frontend behavior unchanged.
- Keep all WorkItem and AgentSpec creation atomic and invisible until final review passes.
- Bind checkpoint reuse to project UUID, Spec UUID, Spec hash, input refs, base source-call UUID, and canonical task hash.
- Never look up checkpoints by project/task title or by a bare FR/NFR identifier.
- Preserve the existing three-attempt structured-output and semantic-review limits.
- Run task planning with at most two concurrent Agent calls.
- Do not add a database migration.

---

### Task 1: Base-stage gateway contract

**Files:**
- Modify: `backend/app/agents/output_validation.py`
- Modify: `backend/app/agents/codex.py`
- Modify: `backend/prompts/nodes/pm_decompose.txt`
- Modify: `backend/prompts/nodes/pm_revise_breakdown.txt`
- Test: `backend/tests/unit/test_output_repair.py`

**Interfaces:**
- Consumes: `payload["decomposition_stage"] == "base"` from the trusted service.
- Produces: `WorkBreakdown` with every `AgentSpecProposal.implementation_plan` normalized to `None`, validated with `require_implementation_plan=False`.

- [ ] **Step 1: Write the failing base-output test**

```python
@pytest.mark.asyncio
async def test_base_decomposition_discards_embedded_plans_and_validates_structure(...):
    result = await gateway.decompose_spec({
        "decomposition_stage": "base",
        "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": valid_spec.source_refs,
    })
    assert all(task.implementation_plan is None for task in result.agent_specs)
```

- [ ] **Step 2: Run the focused test and verify it fails because embedded plans remain or full-plan validation runs**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/unit/test_output_repair.py -k base_decomposition`

- [ ] **Step 3: Add stage-aware normalization and validation**

```python
def normalize_base_breakdown(result: WorkBreakdown | WorkBreakdownRevision) -> None:
    for task in result.agent_specs:
        task.implementation_plan = None
```

Have `validate_node_output` pass `require_implementation_plan=False` only for the trusted base-stage payload. Append an immutable base-stage instruction to the PM objective so the model returns null plans instead of spending tokens on them.

- [ ] **Step 4: Run focused gateway and output-repair tests**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/unit/test_output_repair.py tests/unit/test_agent_gateway.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/output_validation.py backend/app/agents/codex.py backend/prompts/nodes/pm_decompose.txt backend/prompts/nodes/pm_revise_breakdown.txt backend/tests/unit/test_output_repair.py
git commit -m "feat: generate plan-free base breakdowns"
```

### Task 2: Durable checkpoint selection and adoption

**Files:**
- Modify: `backend/app/services/decomposition_service.py`
- Modify: `backend/tests/helpers/fake_agent.py`
- Test: `backend/tests/integration/test_decomposition_service.py`

**Interfaces:**
- Produces: `_Checkpoint(call_id: str, source_call_id: str, result: BaseModel)`.
- Produces: `_checkpoint_binding(snapshot, stage, **fields) -> dict[str, object]`.
- Produces: `_find_checkpoint(...)` that revalidates stored responses and never mutates the source call.
- Produces: `_adopt_checkpoint(...)` that creates a current-command `RESULT_READY` AgentCall pointing to `checkpoint_source_call_id`.

- [ ] **Step 1: Write failing tests for exact snapshot reuse and stale/cross-project rejection**

```python
async def test_retry_reuses_base_checkpoint_without_second_decompose_call(...):
    # First command stores a valid base then one task planner fails.
    # A new command UUID retries the unchanged Spec.
    assert decompose_invocations == 1

async def test_changed_spec_or_project_cannot_reuse_staged_checkpoint(...):
    assert decompose_invocations == 2
```

- [ ] **Step 2: Run the focused tests and verify the retry currently invokes decomposition twice**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/integration/test_decomposition_service.py -k checkpoint`

- [ ] **Step 3: Implement canonical bindings, lookup, revalidation, and current-command adoption calls**

```python
def _checkpoint_binding(snapshot, stage, **extra):
    return {
        "stage": stage,
        "source_spec_version_id": snapshot.id,
        "source_spec_content_hash": snapshot.content_hash,
        "input_refs": list(snapshot.input_refs),
        **extra,
    }
```

Checkpoint lookup must filter by `project_id` before comparing JSON fields. A reused response is parsed and validated again, then copied into a new call bound to the current `command_id` and `input_hash` so `CommandService` evidence checks remain unchanged.

- [ ] **Step 4: Run checkpoint and existing command-evidence tests**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/integration/test_decomposition_service.py tests/integration/test_command_service.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/decomposition_service.py backend/tests/helpers/fake_agent.py backend/tests/integration/test_decomposition_service.py
git commit -m "feat: persist decomposition checkpoints"
```

### Task 3: Per-task planning with bounded concurrency

**Files:**
- Modify: `backend/app/services/decomposition_service.py`
- Modify: `backend/app/services/command_service.py`
- Modify: `backend/tests/helpers/fake_agent.py`
- Test: `backend/tests/integration/test_decomposition_service.py`

**Interfaces:**
- Produces: `_plan_tasks(snapshot, base_checkpoint, breakdown, ...) -> list[_Checkpoint]`.
- Produces: `ActionScopedUnitOfWork.mark_task_plan_call_succeeded(call_id: str) -> None`.
- Changes: `PreparedDecomposition` carries one base call UUID, ordered task-plan call UUIDs, and one Reviewer call UUID.

- [ ] **Step 1: Write failing tests for per-task isolation, concurrency two, and no domain rows on sibling failure**

```python
async def test_each_task_is_planned_separately_with_maximum_concurrency_two(...):
    assert max_active_planners == 2
    assert all(call[0] == "plan_task" for call in planner_calls)

async def test_failed_plan_preserves_ready_siblings_without_work_items(...):
    assert ready_plan_count == task_count - 1
    assert work_item_count == 1  # intake ROOT only
```

- [ ] **Step 2: Run focused tests and verify the service currently has no planning stage**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/integration/test_decomposition_service.py -k "planned_separately or preserves_ready_siblings"`

- [ ] **Step 3: Implement two-way planning, task validation, and plan checkpoint reuse**

```python
self._planning_semaphore = asyncio.Semaphore(2)
results = await asyncio.gather(
    *(self._plan_one_task(...) for task in breakdown.agent_specs),
    return_exceptions=True,
)
```

Settle every call before raising. Assemble plans by exact `work_item_key`; run `validate_breakdown(..., require_implementation_plan=True)` before review. Include task hash and base source-call UUID in every reusable plan binding.

- [ ] **Step 4: Extend materialization to adopt plan calls and run focused tests**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/integration/test_decomposition_service.py tests/integration/test_actionable_agent_specs.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/decomposition_service.py backend/app/services/command_service.py backend/tests/helpers/fake_agent.py backend/tests/integration/test_decomposition_service.py
git commit -m "feat: plan decomposed tasks independently"
```

### Task 4: Targeted semantic plan repair

**Files:**
- Modify: `backend/app/services/decomposition_service.py`
- Test: `backend/tests/integration/test_semantic_breakdown_repair.py`
- Test: `backend/tests/integration/test_decomposition_service.py`

**Interfaces:**
- Produces: `_plan_repair_targets(breakdown, review) -> list[AgentSpecProposal]`.
- Planner repair payload includes `previous_plan`, `review_feedback`, and `previous_review_call_id` but cannot change non-plan task fields.

- [ ] **Step 1: Write a failing regression for the observed unassigned-requirement pattern**

```python
async def test_unassigned_requirement_repairs_only_the_affected_task_plan(...):
    # T4-S2 initially references FR-002/003/004 outside T4 acceptance.
    # Only T4 is replanned; the base and sibling plans are reused.
    assert repaired_task_keys == ["T4"]
```

- [ ] **Step 2: Run the regression and verify it fails before targeted repair exists**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/integration/test_semantic_breakdown_repair.py -k unassigned_requirement`

- [ ] **Step 3: Implement exact-path targeting and bounded full-plan review**

Reuse the existing `agent_specs[<key>]` path convention. If every blocking finding maps to a known task, replan only those tasks; otherwise replan all tasks once. Human-decision findings stop immediately. Keep prior calls as non-adopted audit evidence.

- [ ] **Step 4: Run semantic repair and decomposition suites**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q tests/integration/test_semantic_breakdown_repair.py tests/integration/test_decomposition_service.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/decomposition_service.py backend/tests/integration/test_semantic_breakdown_repair.py backend/tests/integration/test_decomposition_service.py
git commit -m "fix: repair task plans without regenerating breakdowns"
```

### Task 5: Documentation, full regression, and live recovery

**Files:**
- Modify: `backend/readme.md`
- Modify: `docs/frontend-backend-integration-contract.md`
- Test: existing backend and frontend suites

**Interfaces:**
- Documents unchanged frontend commands, checkpoint semantics, and failure recovery.

- [ ] **Step 1: Update operator documentation with staged call ordering and retry behavior**

- [ ] **Step 2: Run complete backend verification**

Run: `/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q`
Expected: all backend tests pass.

- [ ] **Step 3: Run complete frontend verification**

Run: `npm test && npm run lint && npm run build`
Expected: 59 or more tests pass; type checking and build succeed.

- [ ] **Step 4: Restart the local backend and verify health**

Run: `curl -fsS http://127.0.0.1:8088/healthz`
Expected: `{"status":"ok","database":"ok"}`.

- [ ] **Step 5: Retry the existing temperature-converter command through the UI**

Expected: the project reaches `AGENT_SPECS_READY`, milestones and tasks are created once, every Agent Spec has a validated implementation plan, and the knowledge-QA project remains unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/readme.md docs/frontend-backend-integration-contract.md
git commit -m "docs: describe staged decomposition recovery"
```
