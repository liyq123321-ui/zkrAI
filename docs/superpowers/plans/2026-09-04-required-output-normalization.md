# Required Output Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent otherwise valid decomposition results from failing when their non-empty output list marks every existing output as optional.

**Architecture:** Add one narrow normalization helper beside the existing context-reference repair in `output_validation.py`. It mutates only the first existing output's `required` flag when no output is required, then the existing `validate_breakdown` gate validates the complete result as before.

**Tech Stack:** Python 3, Pydantic, pytest, pytest-asyncio

## Global Constraints

- Normalize only `WorkBreakdown` and `WorkBreakdownRevision` Agent Spec proposals.
- Require `outputs` to be non-empty before normalization.
- If any output already has `required=true`, preserve every output unchanged.
- When all existing outputs have `required=false`, change only the first output to `required=true`.
- Do not add, remove, rename, or reorder outputs.
- Empty outputs and every unrelated contract violation must continue through the existing bounded repair and rejection path.

---

### Task 1: Normalize an Existing Output as Required

**Files:**
- Modify: `backend/tests/unit/test_output_repair.py`
- Modify: `backend/app/agents/output_validation.py`

**Interfaces:**
- Consumes: parsed `WorkBreakdown | WorkBreakdownRevision` objects containing `AgentSpecProposal.outputs`.
- Produces: `_repair_required_outputs(result: WorkBreakdown | WorkBreakdownRevision) -> None`, invoked before `validate_breakdown`.

- [ ] **Step 1: Write failing gateway tests**

Add tests to `backend/tests/unit/test_output_repair.py` that exercise the real `CodexAgentGateway` repair path:

```python
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
    assert [item.name for item in result.agent_specs[0].outputs] == ["models", "model documentation"]
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
```

Also add a test that serializes a breakdown with `outputs=[]`, supplies that invalid JSON for all three runner attempts, and asserts `AgentOutputError` plus `len(prompts) == 3`. This proves an empty list is not normalized.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd backend
/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest tests/unit/test_output_repair.py -q
```

Expected: the new non-empty normalization tests fail with `MISSING_OUTPUT`; the empty-list rejection test passes through the existing schema gate.

- [ ] **Step 3: Implement the minimal normalizer**

Add this helper to `backend/app/agents/output_validation.py`:

```python
def _repair_required_outputs(
    result: WorkBreakdown | WorkBreakdownRevision,
) -> None:
    """Promote one existing deliverable when the Agent marked all as optional."""

    for agent_spec in result.agent_specs:
        if agent_spec.outputs and not any(output.required for output in agent_spec.outputs):
            agent_spec.outputs[0].required = True
```

Invoke `_repair_required_outputs(result)` inside the existing `isinstance(result, (WorkBreakdown, WorkBreakdownRevision))` normalization block, before strict validation. Do not change `validate_breakdown` or the Pydantic minimum-length constraint.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
cd backend
/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest tests/unit/test_output_repair.py -q
```

Expected: all output-repair tests pass, including the one-response normalization cases and the three-attempt empty-output rejection.

- [ ] **Step 5: Run complete backend verification**

Run:

```bash
cd backend
/Users/tangtang/Desktop/zkrAI/backend/.venv/bin/pytest -q
```

Expected: the complete backend suite passes.

- [ ] **Step 6: Commit the implementation**

```bash
git add backend/tests/unit/test_output_repair.py backend/app/agents/output_validation.py
git commit -m "fix: normalize required decomposition outputs"
```

