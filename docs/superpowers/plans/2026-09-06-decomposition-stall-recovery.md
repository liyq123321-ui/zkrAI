# Decomposition Stall Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound silent Codex decomposition calls, expose durable progress, and reuse one active decomposition job per Session.

**Architecture:** The base Agent call uses a schema that excludes the later per-task implementation plan. A task-local progress reporter connects streaming Codex JSONL and decomposition stages to the durable command job, while the single-process coordinator serializes submission and returns an existing active Session job.

**Tech Stack:** Python 3, asyncio subprocesses, FastAPI, SQLAlchemy/SQLite, Pydantic, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Keep SSE as the primary observation channel and the non-overlapping status poll at exactly 5,000 ms.
- Keep existing `CommandService` receipts, Agent-call checkpoints, semantic review, and materialization authoritative.
- Do not expose raw prompts, model output, stderr, credentials, or filesystem paths through progress messages.
- Preserve synchronous command API behavior and the single-process deployment model.

---

### Task 1: Make the base decomposition contract lightweight

**Files:**
- Modify: `backend/app/domain/types.py`
- Modify: `backend/app/agents/codex.py`
- Modify: `backend/tests/unit/test_agent_gateway.py`

**Interfaces:**
- Produces: `BaseAgentSpecProposal`, `BaseWorkBreakdown`, and conversion to `WorkBreakdown` with null plans.

- [ ] Add a gateway regression test that captures the strict schema used by the initial `pm_decompose` call, asserts that it contains no `implementation_plan`, and asserts that the returned durable proposal has `implementation_plan is None`.
- [ ] Run the focused test and confirm it fails because `WorkBreakdown` is currently passed directly to the runner.
- [ ] Add the base-only Pydantic types and convert their validated output to `WorkBreakdown` in `decompose_spec`.
- [ ] Run the gateway and output-validation unit tests.

### Task 2: Stream Codex lifecycle events and enforce inactivity timeout

**Files:**
- Create: `backend/app/agents/progress.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/agents/codex.py`
- Modify: `backend/tests/unit/test_agent_gateway.py`

**Interfaces:**
- Produces: `bind_agent_progress(reporter)`, `report_agent_progress(stage, message)`, and `CODEX_INACTIVITY_TIMEOUT_SECONDS`.

- [ ] Add subprocess tests whose fake process emits JSONL and then remains silent; assert lifecycle progress is reported and the process is terminated at the inactivity limit.
- [ ] Run the tests and confirm they fail because the current runner buffers `communicate()` until exit.
- [ ] Implement task-local progress binding and concurrent stdout/stderr draining with separate hard and inactivity deadlines.
- [ ] Run all Agent gateway tests.

### Task 3: Persist progress and prevent duplicate Session jobs

**Files:**
- Modify: `backend/app/database/models.py`
- Modify: `backend/app/database/database.py`
- Modify: `backend/app/schemas/workflow.py`
- Modify: `backend/app/services/command_jobs.py`
- Modify: `backend/tests/unit/test_database_schema.py`
- Modify: `backend/tests/integration/test_command_jobs.py`

**Interfaces:**
- Produces: `CommandJobCoordinator.update_progress(job_id, expected_version, stage, message)` and progress fields on `CommandJobRead`.

- [ ] Add failing migration, progress-version, and different-command-ID active-job reuse tests.
- [ ] Run each focused test and confirm the missing behavior is the reason for failure.
- [ ] Add the idempotent SQLite column migration, progress CAS updates, task-local reporter binding, and coordinator submission lock/active lookup.
- [ ] Run command-job and database tests.

### Task 4: Report logical decomposition stages

**Files:**
- Modify: `backend/app/services/decomposition_service.py`
- Modify: `backend/tests/integration/test_decomposition_service.py`

**Interfaces:**
- Consumes: `report_agent_progress(stage, message)`.

- [ ] Add a failing service test that records base decomposition, task planning, semantic review, and repair/materialization-ready progress.
- [ ] Add sanitized reports at existing stage boundaries without changing validation order.
- [ ] Run decomposition integration tests.

### Task 5: Render durable progress in the frontend

**Files:**
- Modify: `frontend/aios-main/src/api/dto.ts`
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/commandJobs.test.ts`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`

**Interfaces:**
- Consumes: optional `progress_stage`, `progress_message`, and `last_activity_at` from command status snapshots.

- [ ] Add failing tests for progress rendering and server-side command-ID deduplication persistence.
- [ ] Update DTO validation and workspace status handling while retaining the 5,000 ms observer interval.
- [ ] Run focused frontend tests and the production build.

### Task 6: End-to-end recovery verification

**Files:**
- Modify: `backend/readme.md`

**Interfaces:**
- Verifies the approved v6 Session through the public HTTP command/status/resources APIs.

- [ ] Run the complete backend suite and frontend suite/build from clean processes.
- [ ] Restart the backend so abandoned jobs become `PROCESS_INTERRUPTED` and the migration runs.
- [ ] Submit one decomposition command for Session `732bf96b-9639-4135-a96d-474219d5b568`, poll durable progress, and verify terminal success.
- [ ] Query WorkItems, Agent Specs, command jobs, and Agent calls to prove one materialization and no concurrent active job.
- [ ] Record exact verification evidence and commit the completed implementation.
