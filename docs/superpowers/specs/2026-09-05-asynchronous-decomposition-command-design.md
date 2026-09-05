# Asynchronous Decomposition Command Design

## Goal

Move `convert_to_work_item` out of the request/response lifetime so task
decomposition cannot fail merely because an HTTP client, reverse proxy, or browser
times out. The browser receives live state changes over SSE and independently
polls the durable command status every five seconds as a recovery path.

## Scope

This change applies only to the `convert_to_work_item` Session command. All other
Session commands retain their current synchronous `CommandResult` contract.
Decomposition rules, Agent prompts, idempotency, state-version checks, WorkItem
materialization, and Agent Spec generation remain owned by the existing
`CommandService` and `DecompositionService`.

The first version uses FastAPI background tasks in the existing single-process
deployment. It does not introduce Redis, Celery, a distributed queue, WebSockets,
or asynchronous execution for other commands.

## Chosen Approach

Use a durable background job with two observation channels:

1. SSE is the primary live-update channel.
2. A non-overlapping five-second status poll is the fallback and reconciliation
   channel.

The alternatives were polling alone, which provides no immediate updates, and
SSE alone, which is fragile across browser suspension, proxy disconnects, and
missed events. The hybrid keeps the user experience responsive while making the
durable database record authoritative.

## Persistent Model

Add a `command_jobs` table with one row per `(session_id, command_id)`:

- `id`: internal job identifier.
- `project_id`, `session_id`, and `command_id`: ownership and stable client identity.
- `input_hash`: canonical hash of the resolved command request; reusing a command
  ID with different input is a conflict.
- `request`: the complete resolved `SessionCommandRequest`, including actor ID.
- `status`: `pending`, `processing`, `succeeded`, or `failed`.
- `status_version`: monotonic integer incremented on each visible state change and
  used as the SSE event ID.
- `result`: the serialized `CommandResult` after success, otherwise null.
- `error_code` and `error_message`: stable public failure information only.
- `created_at`, `started_at`, `completed_at`, and `updated_at`.

The unique constraint on `(session_id, command_id)` makes submission idempotent.
The existing `command_attempts` and `processed_commands` tables remain the
authority for decomposition preparation/materialization idempotency; the job is
only an execution and observation envelope around that command.

No separate event table is required. The SSE endpoint observes `status_version`
and emits a new snapshot only when it changes. A reconnecting client can send
`Last-Event-ID`; if the persisted version is newer, it receives the current
snapshot immediately. This is sufficient because the first version exposes
state transitions, not a lossless token or log stream.

## HTTP Contracts

### Submit a command

`POST /sessions/{session_id}/commands` keeps its current request body.

For `convert_to_work_item`, it responds with `202 Accepted`:

```json
{
  "command_id": "client-command-id",
  "status": "pending",
  "status_url": "/sessions/session-id/commands/client-command-id",
  "events_url": "/sessions/session-id/commands/client-command-id/events"
}
```

The job row is committed before the response is returned and before the FastAPI
background task is scheduled. A duplicate submission with the same canonical
input returns the existing job snapshot and does not schedule a second live job.
A duplicate command ID with different input returns `409 COMMAND_CONFLICT`.

For every other action, the endpoint continues to return `200 OK` with the
existing `CommandResult` body.

### Query command status

`GET /sessions/{session_id}/commands/{command_id}` returns:

```json
{
  "command_id": "client-command-id",
  "status": "processing",
  "status_version": 2,
  "result": null,
  "error": null,
  "created_at": "2026-09-05T00:00:00Z",
  "started_at": "2026-09-05T00:00:01Z",
  "completed_at": null
}
```

On success, `result` contains the original `CommandResult`. On failure, `error`
contains only `{ "code": "...", "message": "..." }` produced through the
workflow error classifier. An unknown command in the specified Session returns
404 without revealing a job from another Session.

### Observe command state with SSE

`GET /sessions/{session_id}/commands/{command_id}/events` responds as
`text/event-stream`. Each state change is emitted as:

```text
id: 2
event: command.status
data: {"command_id":"client-command-id","status":"processing","status_version":2}
```

The first current snapshot is emitted immediately unless `Last-Event-ID` is
already current. The stream checks durable state at a short server-side cadence,
sends a comment heartbeat while unchanged, and closes after `succeeded` or
`failed`. Client disconnect cancellation stops only the observer, never the job.
Responses disable proxy buffering and caching.

## Backend Components and Flow

Add a focused command-job coordinator responsible for:

- validating and idempotently creating the job;
- claiming `pending` as `processing` with a conditional database update;
- reconstructing the persisted command request;
- invoking the existing `CommandService.execute()` with only the decomposition
  handler registered;
- persisting success or a classified public failure; and
- reading status snapshots for HTTP and SSE.

The Session router receives this coordinator. When the action is
`convert_to_work_item`, it submits the job, adds `coordinator.run(job_id)` to
FastAPI `BackgroundTasks` only when a new pending job was created, and returns
202. The synchronous path is unchanged for other actions.

At application startup, any `pending` or `processing` command jobs left by a
previous process are changed to `failed` with public code `PROCESS_INTERRUPTED`.
The background task is process-local, so silently claiming that such work is
still running would be incorrect. Re-submitting the identical command ID may
reset that failed envelope to `pending`; the existing two-phase command records
then resume or replay safely without duplicating WorkItems.

## Frontend Flow

`executeCommand` returns a discriminated union of the existing completed result
and the new accepted-job response. `convert_to_work_item` no longer receives the
multi-Agent long-request timeout.

After the approval command succeeds, decomposition proceeds as follows:

1. Submit `convert_to_work_item` and receive the accepted job.
2. Persist its command ID for that Session in local storage and display the
   existing busy/progress state.
3. Open the job SSE URL and listen for `command.status` snapshots.
4. Independently start a recursive `setTimeout` status check with an exact
   5,000 ms delay after each completed request. This prevents overlapping polls.
5. Reconcile both channels through one status handler. Repeated or older
   `status_version` values are ignored.
6. On `succeeded`, close SSE, cancel the timer, delete the stored job ID, apply
   `result.state`, refresh Session resources, and select the first task.
7. On `failed`, close SSE, cancel the timer, keep the current Session state, and
   show the durable public error. A retry reuses the same command identity.
8. If SSE disconnects, polling continues. The browser may reconnect SSE, but a
   stream error alone is never shown as decomposition failure.
9. On component unmount or project switch, close SSE and cancel polling without
   cancelling the backend job.

On mount, a saved active decomposition command is queried once and observation
resumes if it is still pending or processing. This prevents a page reload from
losing access to a running job.

## Concurrency and Idempotency

- A conditional `pending -> processing` update ensures only one background runner
  owns a job in the single process.
- `command_id + input_hash` protects the submission boundary.
- `CommandService` continues to protect state version, legal action, external
  Agent evidence, and final materialization.
- Poll and SSE reads never mutate workflow state.
- A terminal job is immutable except that an interrupted/failed identical job may
  be explicitly resubmitted for recovery.
- A stale-state or illegal-action error becomes a terminal failed job and is never
  hidden as an HTTP transport timeout.

## Error Handling

Background exceptions never escape into the original POST response. They are
classified and stored on the job. Internal exception strings, Agent output, and
credentials are not exposed by either status channel.

If the status request temporarily fails, the frontend retains the active job and
tries again after five seconds. If both SSE and polling are unavailable, the UI
shows a connectivity message but does not claim the decomposition failed. Only a
durable `failed` status is presented as job failure.

## Testing

Backend tests cover:

- `convert_to_work_item` returns 202 before a blocked decomposition completes;
- the background runner transitions `pending -> processing -> succeeded` and
  exposes the original `CommandResult`;
- classified failures become durable `failed` snapshots;
- duplicate identical submissions do not run twice;
- conflicting command reuse returns 409;
- status and SSE are scoped to the requested Session;
- SSE emits increasing IDs, heartbeats while unchanged, resumes from
  `Last-Event-ID`, and closes on terminal state;
- startup marks abandoned jobs interrupted and identical resubmission recovers;
- synchronous commands preserve their 200 response contract.

Frontend tests use fake timers and cover:

- decomposition handles the 202 contract without waiting for completion;
- status is queried every 5,000 ms without overlapping requests;
- SSE and polling share one idempotent reconciliation path;
- SSE failure leaves polling active;
- success refreshes resources exactly once and stops both observers;
- durable failure displays the public error and stops both observers;
- unmount/project switch cleans up EventSource and timers; and
- a saved in-flight command resumes after reload.

## Documentation

Update the backend API documentation with the conditional 202 command response,
the status endpoint, the SSE endpoint, the five-second client reconciliation
rule, and the single-process interruption behavior.
