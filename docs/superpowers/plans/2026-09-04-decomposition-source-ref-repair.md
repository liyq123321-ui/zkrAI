# Decomposition Source Reference Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically correct an Agent-generated `context_refs` identifier when it differs by exactly one character from one uniquely matching approved input reference, while preserving strict rejection for ambiguous or unrelated references.

**Architecture:** Add a small normalization boundary to `app.agents.output_validation` before the existing strict breakdown validator runs. The gateway-owned normalization mutates only typed Agent output; persistence-facing `validate_breakdown` remains exact and unchanged, so external or ambiguous invalid references cannot bypass the safety gate.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing Codex structured-output gateway.

## Global Constraints

- Do not relax `validate_breakdown` exact membership checks.
- Only repair references with the same namespace and a single insertion, deletion, or substitution.
- Repair only when exactly one approved reference matches; otherwise retain the invalid value so normal validation rejects it.
- Do not add third-party dependencies.

---

### Task 1: Deterministic Agent Source-Reference Normalization

**Files:**
- Modify: `backend/app/agents/output_validation.py`
- Test: `backend/tests/unit/test_output_repair.py`

**Interfaces:**
- Consumes: `WorkBreakdown | WorkBreakdownRevision`, plus `payload["input_refs"]`.
- Produces: `_repair_context_refs(result: WorkBreakdown | WorkBreakdownRevision, allowed_refs: set[str]) -> None` used by `validate_node_output` before structural validation.

- [ ] **Step 1: Write failing tests for unique repair and ambiguous rejection**

Add one test where the only model response contains `artifact:a505e774-c9c0-49b4-a71e-604ed8bcae9c` while the approved value is `artifact:a505e774-c9c0-49b4-a71e-604edb8cae9c`. Assert the gateway succeeds in one attempt and returns the approved value. Add a second test with two one-edit candidates and assert `AgentOutputError` after the bounded attempts.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
./.venv/bin/pytest tests/unit/test_output_repair.py -q
```

Expected: the unique-repair test fails with `INVALID_SOURCE_REF`; the ambiguous-reference test continues to reject.

- [ ] **Step 3: Implement one-edit unique matching**

Implement a dependency-free helper that returns true only for a single insertion, deletion, or substitution, filters candidates by the identifier namespace before `:`, and replaces an invalid `context_refs` entry only when the candidate set has length one. Invoke it before merging/validating breakdown output.

- [ ] **Step 4: Run focused and adjacent validation tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/unit/test_output_repair.py tests/unit/test_decomposition_validation.py tests/unit/test_agent_gateway.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the backend regression suite**

Run:

```bash
cd backend
./.venv/bin/pytest -q
```

Expected: all tests pass without new warnings or errors.

- [ ] **Step 6: Commit the verified fix**

```bash
git add backend/app/agents/output_validation.py backend/tests/unit/test_output_repair.py docs/superpowers/plans/2026-09-04-decomposition-source-ref-repair.md
git commit -m "fix: repair near-match decomposition source refs"
```
