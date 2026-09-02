# Gitea PRD Review Residual Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse durable semantic-review results across PREPARING crash recovery and reject duplicate Gitea comment IDs across the whole pull request.

**Architecture:** Extend `SpecService.prepare_external_revision` with exact reviewer-call reconciliation before any external reviewer invocation. Move Gitea comment identity tracking from review-local scope to pull-request scope while retaining existing review/path/line grouping.

**Tech Stack:** Python 3.12, SQLAlchemy, SQLite, httpx MockTransport, pytest, pytest-asyncio.

## Global Constraints

- Preserve the public API, database schema, workflow states, and Gitea 1.27 contract from the parent design.
- Recovery identity is the exact deterministic `review_spec` payload, including `command_id`, `input_hash`, and `spec_hash`; partial matches are insufficient.
- Exact `RESULT_READY` semantic calls are reused; exact `PENDING` or `AMBIGUOUS` calls fail closed and never trigger a second external call.
- Comment IDs are unique across all reviews returned for one pull request; any duplicate fails the entire read with `GITEA_INCOMPATIBLE`.
- Every production change follows RED -> GREEN; tests use fakes only and perform no real network or Agent execution.
- `.DS_Store`, `.superpowers/`, `tests/helpers/__init__.py`, credentials, and generated caches must not enter commits.

---

### Task 1: Recover durable semantic reviewer results

**Files:**
- Modify: `app/services/spec_service.py:140-280`
- Modify: `tests/integration/test_review_publish_pipeline.py`
- Test: `tests/integration/test_pm_rewrite_command.py`

**Interfaces:**
- Consumes: deterministic payload returned by `SpecService._spec_reviewer_payload(...)`.
- Produces: `PreparedCommand.agent_call_ids == [generation_call_id, reviewer_call_id]` for both new and recovered semantic review calls.

- [ ] **Step 1: Write the failing crash-recovery test**

Add an integration regression named `test_preparing_retry_reuses_durable_semantic_review_result`. Use the existing publication fixtures and `ScriptedAgentGateway`. Interrupt the first command after `_record_call_result` has committed the `review_spec` `RESULT_READY` row but before preparation returns. Retry the same task/command and assert:

```python
assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
assert db.query(AgentCall).filter_by(operation="review_spec").count() == 1
assert db.query(AgentCall).filter_by(
    operation="review_spec", status="SUCCEEDED"
).count() == 1
```

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/firstflight-residual-pycache .venv/bin/pytest -q -p no:cacheprovider tests/integration/test_review_publish_pipeline.py::test_preparing_retry_reuses_durable_semantic_review_result
```

Expected: FAIL because retry creates a second `review_spec` call or invokes the fake reviewer twice.

- [ ] **Step 3: Reconcile exact reviewer calls before creation**

In `prepare_external_revision`, build `reviewer_payload` before choosing a call. Query project `AgentCall` rows with `operation="review_spec"`, newest first, and compare `call.request == reviewer_payload`.

Implement these exact branches:

```python
if exact.status == "RESULT_READY":
    semantic = SemanticReview.model_validate(exact.response)
    reviewer_call_id = exact.id
    reviewer_session_id = exact.agent_session_id
elif exact.status in {"PENDING", "AMBIGUOUS"}:
    raise CommandHandlerFailure(
        "Spec semantic review result is unavailable",
        agent_call_ids=[generation_call_id, exact.id],
    )
else:
    create_and_execute_new_call()
```

Invalid `RESULT_READY` response data must raise `CommandHandlerFailure` with the durable call IDs. Do not call `review_spec` on the recovered path.

- [ ] **Step 4: Verify GREEN and focused regression**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/firstflight-residual-pycache .venv/bin/pytest -q -p no:cacheprovider tests/integration/test_review_publish_pipeline.py tests/integration/test_pm_rewrite_command.py tests/integration/test_command_service.py
```

Expected: all pass with only the existing allowed Starlette/httpx deprecation warning, if that suite imports TestClient.

- [ ] **Step 5: Commit**

```bash
git add app/services/spec_service.py tests/integration/test_review_publish_pipeline.py tests/integration/test_pm_rewrite_command.py
git commit -m "fix: recover semantic review preparation"
```

---

### Task 2: Reject PR-wide duplicate comment IDs

**Files:**
- Modify: `app/services/gitea.py:318-365`
- Modify: `tests/unit/test_gitea_service.py`

**Interfaces:**
- Consumes: paginated Gitea review metadata and each review's official comment array.
- Produces: a complete unambiguous `list[GiteaThread]`, or `GiteaError(code="GITEA_INCOMPATIBLE")` before returning any partial result.

- [ ] **Step 1: Write the failing cross-review duplicate test**

Add `test_list_comment_threads_rejects_duplicate_comment_id_across_reviews`. The MockTransport must return two distinct review IDs, each with one comment using ID `501` but different anchors. Assert:

```python
with pytest.raises(GiteaError) as captured:
    await client.list_comment_threads(7)
assert captured.value.code == "GITEA_INCOMPATIBLE"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/firstflight-residual-pycache .venv/bin/pytest -q -p no:cacheprovider tests/unit/test_gitea_service.py::test_list_comment_threads_rejects_duplicate_comment_id_across_reviews
```

Expected: FAIL because the current `seen_comment_ids` set is recreated for every review.

- [ ] **Step 3: Make identity tracking pull-request scoped**

Initialize exactly one set before the review loop:

```python
seen_comment_ids: set[int] = set()
for review in reviews:
    ...
    for comment in comments:
        if comment.id in seen_comment_ids:
            raise self._incompatible()
        seen_comment_ids.add(comment.id)
```

Keep grouping, sort order, pagination, and malformed-record validation unchanged.

- [ ] **Step 4: Verify GREEN and focused regression**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/firstflight-residual-pycache .venv/bin/pytest -q -p no:cacheprovider tests/unit/test_gitea_service.py
```

Expected: all Gitea service tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/gitea.py tests/unit/test_gitea_service.py
git commit -m "fix: reject duplicate review comment ids"
```

---

## Final verification

- [ ] Run both focused suites and the complete regression suite.
- [ ] Run `PYTHONPYCACHEPREFIX=/tmp/firstflight-residual-pycache .venv/bin/python -m compileall -q app main.py tests`.
- [ ] Run `git diff --check` and confirm `git status --short` contains only the two known untracked files.
- [ ] Perform task-scoped reviews and one final whole-range review before pushing.

