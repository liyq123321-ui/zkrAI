# Decomposition Stall Recovery Design

## Goal

Make asynchronous task decomposition finish or fail with actionable progress in a
bounded time, and prevent one Session from running duplicate decomposition jobs.

## Evidence and Constraints

The failing base `decompose_spec` call starts a Codex thread and turn but emits no
final response. Its strict schema has 69 properties and 30 arrays because it still
contains the complete per-task `ImplementationPlan`, even though the prompt requires
that field to be null during the base stage. Removing that unused nested contract
reduces the base schema to 40 properties and 21 arrays. Larger PRD requests have
completed successfully, so request byte size alone is not the failure trigger.

The current SSE and five-second polling paths work, but the durable job exposes only
`processing`. The backend buffers Codex JSONL until exit, uses a 2,000-second hard
timeout, and permits different command IDs for the same Session to run concurrently.

## Considered Approaches

1. **Lightweight base schema plus bounded progress (chosen).** Use a dedicated base
   Agent Spec contract without `implementation_plan`, restore the field as null after
   parsing, and keep the existing per-task planning stage. This directly removes the
   unused complex schema while preserving validation, checkpoints, semantic review,
   and repair behavior.
2. **Only lower model reasoning or switch models.** This may improve latency but does
   not prevent duplicate jobs or silent stalls and makes correctness depend on local
   runtime configuration.
3. **Generate every Agent Spec in a new per-task stage.** This provides the smallest
   individual outputs but adds another fan-out, checkpoint format, and repair path.
   It is unnecessary until the lighter base contract is shown insufficient.

## Backend Design

`CodexAgentGateway.decompose_spec` uses `BaseWorkBreakdown`, whose Agent Spec type has
all current base fields except `implementation_plan`. A successful response is
converted to the existing durable `WorkBreakdown` with every implementation plan set
to null. Revision calls keep their current contract because they replace only affected
records and are not the observed initial stall.

The Codex runner consumes stdout JSONL while the process is alive instead of waiting
for `communicate()` to return. It reports sanitized lifecycle events, retains bounded
stdout/stderr for diagnostics, terminates the child on either the existing total
timeout or a new no-progress timeout, and still validates only the final output file.
The no-progress timeout is configurable and must be lower than the hard timeout.

Command jobs persist `progress_stage`, `progress_message`, and `last_activity_at`.
Every visible progress change increments `status_version`, so existing SSE and polling
deliver it without a second event system. Logical decomposition stages and Codex JSONL
lifecycle events use one task-local progress reporter bound by the command coordinator.

Submission is serialized inside the single application process. Before creating or
retrying a job, the coordinator looks for another `pending` or `processing` job for
the Session. If one exists, it returns that job instead of scheduling another runner,
even when the browser supplied a different command ID. Existing command-ID input-hash
conflicts remain errors.

## Frontend Design

The command status DTO accepts the three progress fields. The workspace uses durable
`progress_message` when present, while keeping its existing generic fallback. SSE and
the exact non-overlapping five-second poll continue to share one reconciliation path.
If a submission is deduplicated to an existing command ID, local storage is replaced
with the server-returned ID.

## Recovery and Compatibility

SQLite startup performs an additive, idempotent migration for the progress columns.
Existing active jobs are still marked interrupted at application restart. A retry is
safe because existing command attempts and Agent-call checkpoints remain authoritative.
No public command action or synchronous command response changes.

## Verification

Automated regression tests must prove that the old behavior fails before production
changes and that the implementation:

- omits `implementation_plan` from the initial strict schema and restores null plans;
- returns one active Session job for concurrent/different command IDs;
- persists and emits increasing progress snapshots;
- terminates a silent Codex subprocess on the no-progress timeout while preserving
  JSONL diagnostics;
- keeps the five-second poll and renders durable progress; and
- migrates an existing SQLite `command_jobs` table idempotently.

After unit/integration/build verification, restart the local backend, submit the
approved v6 Session once, observe its stages through the status endpoint, and verify
that WorkItems and Agent Specs are materialized exactly once.
