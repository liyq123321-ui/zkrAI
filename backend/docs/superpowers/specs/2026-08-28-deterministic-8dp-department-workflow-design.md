# Deterministic 8-DP Department Workflow Design

**Date:** 2026-08-28

**Status:** Approved design

**Scope:** Replace the prompt-controlled specification flow in firstFlight with a deterministic Python workflow engine that drives the complete Spec Superflow lifecycle, department delegation, isolated Codex execution, review, integration, and closure.

## 1. Context

The current FastAPI gateway already provides session identifiers, SQLite-backed conversations, conversation history, and Codex CLI streaming. Its workflow behavior is not deterministic:

- `app/api/chat.py` constructs a prompt and lets a Codex response implicitly determine what happens next.
- `app/services/prompt_builder.py` accepts workflow state but does not use it to control transitions.
- `app/services/workflow_service.py` contains phase helpers, but the request path does not invoke them as a state machine.
- Workflow state does not model approval requirements, the next legal action, optimistic concurrency, checkpoints, or durable agent jobs.
- Work item extraction depends on parsing model-authored text instead of validating a node-specific output contract.

This design makes Python the sole authority for phases, transitions, permissions, checkpoints, task closure, and integration. Codex remains the reasoning and implementation engine inside narrowly scoped graph nodes, but model text cannot directly change workflow state.

## 2. Goals and Non-Goals

### Goals

1. Implement the complete eight-state Spec Superflow lifecycle and DP-0 through DP-7 as fixed Python transitions.
2. Represent the user's top-level request as an Epic and delegate selected parts to fixed departments as Department WorkItems.
3. Let each Department Lead split its WorkItem into Employee SubWorkItems with explicit interfaces, dependencies, skills, paths, tests, and supervisor.
4. Run every Worker in a separate Git worktree, process, working directory, and Codex workspace-write sandbox with network disabled by default.
5. Require evidence-based, hierarchical acceptance before any WorkItem can close.
6. Persist state, approvals, audit events, checkpoints, jobs, and artifact hashes in SQLite while materializing readable specs in the repository.
7. Resume safely after process failure without duplicating work, decisions, merges, or side effects.
8. Preserve the current chat API as a compatibility facade while new clients use explicit commands and event streams.
9. Never automatically merge or push the main branch. Always remind the user to save Git when a candidate version is produced.

### Non-Goals for the First Version

- Quick, Hotfix, and Tweak paths are not implemented. Every new workflow uses the Full path.
- Departments are not created dynamically by a model.
- Skills are not downloaded dynamically during a Worker run.
- The system does not provision remote CI, cloud sandboxes, or remote Git hosting.
- The system does not automatically resolve conflicts by overwriting either side of an Interface Contract.

## 3. Core Invariants

The implementation must preserve these invariants across every code path:

1. Python owns the graph. A Codex result may propose structured data but cannot set `current_phase`, `status`, `waiting_for_user`, `approval_required`, or `next_action`.
2. A `project_path` is supplied when a session starts and is immutable for that `session_id`.
3. A workflow command is accepted only when its action is legal for the current phase and its `expected_state_version` matches.
4. A Worker may submit evidence but may not accept, integrate, close, merge, or push its own work.
5. A child WorkItem closes only after its declared supervisor verifies its Spec, diff, tests, and completion claim.
6. A Department WorkItem closes only after all children are closed and department-level tests pass.
7. DP-7 is unreachable until all selected departments are closed and integration tests satisfy the frozen Interface Contract.
8. Main-branch merge and push are always user-owned actions.
9. Model output is accepted only through a node-specific JSON Schema. Substring-based JSON extraction is forbidden.
10. Every DP decision is checkpointed and auditable.

## 4. Domain Model

### 4.1 WorkflowRun

`WorkflowRun` is the durable aggregate for one Epic execution.

Required fields:

- `id`, `session_id`, `epic_id`
- immutable `project_path` and `base_commit`
- `current_phase`
- `current_decision_point`
- `clarification_round`
- `status`
- `waiting_for_user`
- `approval_required`
- `next_action`
- monotonic `state_version`
- `created_at`, `updated_at`, `completed_at`

`status` is independent from phase. A successfully archived run retains `current_phase=closing` and ends with `status=completed`. An abandoned run uses `current_phase=abandoned` and `status=cancelled`.

### 4.2 Work Hierarchy

The fixed hierarchy is:

```text
Epic
└── Department WorkItem
    └── Employee SubWorkItem
```

Every WorkItem has:

- `id`, `epic_id`, optional `parent_id`, and `department_id`
- `kind`: `department` or `employee`
- `title`, concrete objective, scope, and exclusions
- inputs, outputs, and interface references
- dependency WorkItem IDs
- `required_skills[]`
- `allowed_paths[]`
- test obligations and acceptance criteria
- supervisor Agent identity
- priority and dependency order
- lifecycle status
- assigned AgentRun, submitted commit, diff hash, and evidence bundle

The lifecycle is:

```text
DRAFT -> READY -> ASSIGNED -> RUNNING -> SUBMITTED
SUBMITTED -> ACCEPTED -> INTEGRATED -> CLOSED
SUBMITTED -> REJECTED -> ASSIGNED
RUNNING -> FAILED | BLOCKED | ORPHANED
```

Only the supervisor may move `SUBMITTED` to `ACCEPTED`. Only the Merge Coordinator may mark an accepted employee item `INTEGRATED` and `CLOSED`. Only the Planner may close a Department WorkItem. The Epic closes only through DP-7.

### 4.3 Supporting Records

- `WorkflowCheckpoint`: immutable state snapshot taken at each DP boundary.
- `DecisionRecord`: user approval, revision request, execution-mode selection, Planner arbitration, or abandonment.
- `Department`: one of the fixed capability-table entries.
- `SkillPackage`: allowlisted repository, pinned commit, checksum, license, entry path, and capabilities.
- `InterfaceContract`: versioned provider/consumer contract and acceptance rules.
- `AgentJob`: durable schedulable unit for one graph node or Worker run.
- `AgentRun`: process, sandbox, worktree, input/output, timing, and exit status.
- `ReviewReceipt`: supervisor decision linked to a Spec version, diff hash, and evidence hash.
- `ArtifactVersion`: repository path, content hash, state version, and writer event.
- `OutboxEvent`: idempotent DB-to-repository materialization request.

## 5. Lightweight Graph Engine

The project will use a zero-dependency `WorkflowGraphEngine` rather than LangGraph or another orchestration framework. The engine is expected to remain a small application module with these parts:

- enums for phases, decision points, commands, statuses, and node outcomes;
- an explicit transition table;
- node handlers with typed inputs and outputs;
- guard functions for approvals, artifacts, dependencies, and tests;
- transactional persistence and optimistic locking;
- checkpoint and audit hooks;
- durable AgentJob dispatch;
- a deterministic calculation of legal actions and `next_action`.

The transition table is data, not prompt text. A transition succeeds in one SQLite transaction that verifies the command idempotency key, checks `state_version`, evaluates guards, writes the new state, records decisions, creates jobs, and appends outbox events.

### 5.1 States and Decision Points

The graph uses the official eight phases:

1. `exploring`
2. `specifying`
3. `bridging`
4. `approved-for-build`
5. `executing`
6. `debugging`
7. `closing`
8. `abandoned`

Decision behavior is fixed as follows:

| DP | Phase | Gate | Successful result |
|---|---|---|---|
| DP-0 | exploring | Confirm Full design path and initial problem framing | Enter `specifying` |
| DP-1 | specifying | Confirm clarified requirements | Generate bridge artifacts and enter `bridging` |
| DP-2 | bridging | Review proposal, specs, architecture, department allocation, and task graph | Freeze reviewed artifact versions; remain in `bridging` |
| DP-3 | bridging | Approve execution contract and frozen interfaces | Enter `approved-for-build` |
| DP-4 | approved-for-build | Select execution mode | Create execution jobs and enter `executing` |
| DP-5 | executing | Approve escalation for unresolved execution failure | Enter `debugging` |
| DP-6 | executing/debugging | Evaluate validation failure or successful recovery | Rework in `debugging`, resume `executing`, or enter `closing` after full validation |
| DP-7 | closing | Confirm archive and candidate release | Archive artifacts; set `status=completed` |

At any nonterminal gate, `revise` returns to the owning phase without discarding audit history. `abandon` moves to `abandoned`. Illegal transitions return a conflict and do not invoke Codex.

Requirement clarification is also bounded. The Planner may conduct at most three clarification rounds during `exploring` and `specifying`. After the third round it cannot open another question cycle: it records unresolved items, selects explicit assumptions through a parent-level Planner DecisionRecord, and presents those assumptions for the user's DP-1 approval or revision. This keeps model uncertainty from creating an unbounded loop.

### 5.2 Execution Modes at DP-4

All modes use the same isolation rules:

- `inline`: one isolated Worker executes the next eligible SubWorkItem;
- `batch-inline`: eligible SubWorkItems execute sequentially;
- `sdd`: independent WorkItems execute in dependency waves with bounded parallelism.

The mode changes scheduling only. It does not weaken sandboxing, evidence, review, integration, or closure gates.

## 6. Architecture and Components

```text
FastAPI API
  -> Command Service
     -> WorkflowGraphEngine
        -> Domain Services
        -> SQLite State / Audit / Checkpoints / Jobs / Outbox
        -> Artifact Materializer
        -> AgentJob Worker
           -> Codex CLI Adapter
           -> Worktree + Sandbox Manager
           -> Skill Resolver
           -> Evidence Collector
        -> Review and Merge Coordinators
```

### 6.1 API Layer

The API validates transport schemas, authenticates the session, submits commands, and streams events. It does not decide transitions.

New endpoints:

- `POST /sessions`: create a session with immutable `project_path`.
- `POST /sessions/{session_id}/commands`: submit an explicit workflow command.
- `GET /sessions/{session_id}/state`: return phase, status, version, legal actions, and next action.
- `GET /sessions/{session_id}/work-items`: return the Epic hierarchy and evidence state.
- `GET /sessions/{session_id}/events`: stream durable events with SSE `Last-Event-ID` recovery.

A command contains:

```json
{
  "command_id": "client-generated-idempotency-key",
  "action": "message|approve|revise|abandon|select_execution_mode",
  "expected_state_version": 12,
  "message": "optional user text",
  "payload": {}
}
```

The existing `/chat` route remains a facade. It converts ordinary chat input to a `message` command and streams graph events in the existing response format. It never bypasses decision gates.

### 6.2 AgentJob Worker

Long-running model calls, tests, and merge operations are represented as durable `AgentJob` records. The worker claims jobs transactionally, records a lease, invokes one node, validates its output, and writes an outcome event. A job idempotency key is derived from:

```text
run_id : node_name : state_version : workitem_id : attempt
```

A unique database constraint prevents duplicate execution after retries or service restarts.

### 6.3 Codex CLI Adapter

The existing CLI integration is retained but made explicit and portable:

- invoke `codex exec --json`;
- supply a node-specific `--output-schema`;
- use `--sandbox workspace-write` for implementation Workers;
- use read-only sandboxing for Planner, Lead review, validation, and artifact review;
- set the working directory to the assigned project or Worker worktree;
- disable network access unless a future approved node explicitly requires it;
- persist structured JSONL events, stderr, exit code, and the final validated object.

A schema-invalid response is repaired in the same Codex thread at most twice. A third invalid response marks the job failed and leaves the workflow at its latest checkpoint for operator action.

## 7. Node Protocol

Each model call is a small, single-purpose node. Initial node types are:

- clarification planner;
- architecture analyst;
- department allocator;
- Department Council participant;
- Department Lead decomposer;
- artifact writer;
- execution-contract builder;
- implementation Worker;
- Lead reviewer;
- cross-interface validator;
- debugging analyst;
- release/archive reviewer.

Python supplies each node with only:

- node name and immutable node objective;
- read-only workflow snapshot;
- relevant artifact and WorkItem versions;
- frozen interfaces and dependency slice;
- exact allowed Skill packages;
- allowed repository paths;
- output JSON Schema.

The schema never includes workflow phase or transition fields. Node output is stored append-only and becomes actionable only after Python validation and guard evaluation.

## 8. Department Allocation and Council Protocol

### 8.1 Fixed Departments

The capability table contains exactly eight conventional software departments. The Planner selects only departments required by the Epic, but cannot add another department.

| Department | Primary responsibility | Default Skill capabilities |
|---|---|---|
| Product Management | requirements, user stories, business rules, acceptance | requirements elicitation, PRD writing, acceptance criteria |
| Solution Architecture | boundaries, ADRs, shared models, interfaces | system design, API design, ADR writing, dependency analysis |
| Backend Engineering | services, APIs, business logic, integration | TDD, backend development, API implementation, code review |
| Frontend Engineering | UI, client state, interaction, accessibility | frontend design, frontend development, accessibility, UI testing |
| Data Engineering | schemas, migrations, lifecycle, data quality | data modeling, migrations, validation, rollback design |
| Security | authentication, authorization, threats, supply chain | threat modeling, secure coding, dependency audit, auth review |
| QA Engineering | test strategy, automation, regression, gates | test strategy, test automation, regression, quality gates |
| DevOps | CI/CD, environments, observability, release | CI/CD, containerization, observability, release engineering |

### 8.2 Three-Round Council

Department collaboration follows a deterministic maximum of three rounds:

1. **Independent decomposition:** Each selected Department Lead independently proposes responsibilities, deliverables, risks, and dependencies for its Department WorkItem.
2. **Interface negotiation:** Leads negotiate provider/consumer ownership, shared models, request and response schemas, compatibility, and dependency order.
3. **Conflict convergence:** Leads address only unresolved conflicts and open decisions.

After round three, the Planner arbitrates every remaining issue and freezes the Interface Contract. There is no fourth round.

An Interface Contract records:

- contract ID and immutable version;
- provider department and consumer departments;
- request, response, event, or shared-model schema references;
- compatibility and error behavior;
- dependency ordering;
- integration fixtures and acceptance tests;
- status `DRAFT`, `NEGOTIATING`, or `FROZEN`;
- the DecisionRecord that froze or revised it.

A frozen contract can change only through a new Planner DecisionRecord that versions the contract and invalidates affected downstream approvals.

### 8.3 Department Decomposition

After contracts freeze, each Department Lead converts its Department WorkItem into Employee SubWorkItems. Every SubWorkItem must contain:

- a concrete objective, scope, and exclusions;
- inputs, outputs, and interface references;
- dependency WorkItem IDs;
- `required_skills[]` with pinned SkillPackage IDs;
- `allowed_paths[]`;
- test obligations and objective acceptance criteria;
- its supervisor Agent;
- expected evidence and completion declaration format.

Python rejects incomplete SubWorkItems before scheduling them.

## 9. Skill Registry

Skills may originate from public GitHub repositories, but execution uses a curated immutable registry rather than arbitrary URLs.

Before a Skill is eligible, an administrative installation step records:

- allowlisted repository URL;
- pinned Git commit SHA;
- content checksum;
- license and provenance;
- Skill entry path and metadata;
- declared capabilities and compatible departments;
- security review status.

The Skill Resolver maps `required_skills[]` to exact versions before a Worker starts. A Worker receives read-only mounts of only its assigned Skills; the full Skill repository is never exposed. Network access remains disabled, so execution cannot fetch or replace Skills dynamically.

## 10. Worker Isolation and Git Topology

The Epic captures one immutable `base_commit`. Branch and worktree roles are:

```text
base_commit
├── workflow/<run>/worker/<subworkitem>
├── workflow/<run>/dept/<department>
└── workflow/<run>/integration
```

For every Employee SubWorkItem, the sandbox manager creates:

- a dedicated Git worktree rooted at `base_commit` or the explicitly approved dependency commit;
- an independent branch;
- an independent OS process and working directory;
- a Codex workspace-write sandbox;
- network disabled by default;
- read-only mounts for the selected Skill subset;
- an input bundle containing only the assigned Spec, dependencies, frozen interfaces, and allowed paths.

Before execution, the manager records the worktree status and validates path policy. After execution, it enumerates all changed, untracked, renamed, deleted, symlinked, and submodule paths. Any path outside `allowed_paths[]`, any `.git` manipulation, or any symlink escape rejects the submission.

A Worker may produce only:

- one independent commit;
- its diff and content hash;
- test commands and captured results;
- relevant logs and artifacts;
- a structured completion claim.

It cannot merge, push, close its WorkItem, alter the Interface Contract, or approve its own evidence.

## 11. Hierarchical Review, Integration, and Closure

1. A Department Lead runs in a read-only sandbox and compares each submitted commit, diff, and test bundle against the exact SubWorkItem Spec and frozen interface version.
2. On rejection, the Lead records failed criteria and returns the item to `ASSIGNED`; it cannot silently edit Worker output.
3. On acceptance, a Merge Coordinator integrates Worker commits into the department branch in dependency order. Only after successful integration may it close the Employee SubWorkItem.
4. When all department children close, department-level tests run. The Planner reviews the Department WorkItem, aggregate diff, interface obligations, and test evidence before closing it.
5. Department branches integrate into the workflow integration branch in contract dependency order.
6. Cross-interface validation and the full integration test suite must pass before DP-6 can enter `closing`.
7. DP-7 archives the approved artifact set and produces a candidate version notification.
8. The system does not merge or push the main branch. The notification explicitly asks the user to save Git, review the candidate branch, and perform any desired merge and push.

## 12. Artifacts and Final Consistency

SQLite is the workflow state authority. Human-readable artifacts are materialized under:

```text
changes/<change-id>/proposal.md
changes/<change-id>/specs/
changes/<change-id>/design.md
changes/<change-id>/tasks.md
changes/<change-id>/execution-contract.md
changes/<change-id>/workflow-manifest.json
```

State changes and `OutboxEvent` records commit in the same database transaction. The artifact materializer consumes events idempotently, writes complete files atomically, calculates hashes, and records `ArtifactVersion`. The manifest identifies the originating `state_version`, artifact hashes, frozen Interface Contract versions, and WorkItem graph version.

This is deliberate final consistency: an API response may temporarily expose a newer database version than the repository artifacts, but `next_action` does not allow an artifact-dependent approval until materialization and hash verification finish.

## 13. Checkpoints, Recovery, and Concurrency

Every DP creates an immutable checkpoint containing:

- workflow snapshot and `state_version`;
- Epic and WorkItem DAG version;
- approved artifact hashes;
- frozen Interface Contract versions;
- `base_commit`, department heads, and integration head;
- AgentJob and AgentRun references;
- outbox cursor and review receipts.

On restart, recovery selects the latest valid checkpoint and verifies:

1. `project_path` is unchanged and accessible;
2. `base_commit` and recorded branch heads are reachable;
3. materialized artifact hashes match the database;
4. frozen interface hashes match reviewed WorkItems;
5. no completed idempotency key is being scheduled again;
6. leased jobs are either active or expired.

Expired Worker leases become `ORPHANED`. Their worktree, commit, diff, logs, and evidence remain intact. The supervisor decides whether to retry from the same dependency base or assign a replacement Agent. Recovery never overwrites divergent Git or artifact state; it sets `waiting_for_user` or `approval_required` and emits a reconciliation event.

Optimistic locking uses `expected_state_version`. A stale command returns HTTP 409 with the current version and legal actions. Repeating the same `command_id` returns the original command result without executing side effects again.

## 14. Error Semantics

- **409 Conflict:** stale state version, illegal action, changed artifact, changed contract, or changed Git base.
- **422 Unprocessable Entity:** invalid command payload or agent output that cannot pass schema repair.
- **423 Locked:** workflow is waiting for a user decision, supervisor review, checkpoint reconciliation, or required dependency.
- **503 Service Unavailable:** Codex CLI or an execution dependency failed transiently; the durable job remains resumable.

Failures never advance a decision point implicitly. Error events include the affected run, phase, node, WorkItem, attempt, checkpoint, and safe next action.

## 15. Observability and Audit

Structured events and logs include, where applicable:

- `run_id`, `session_id`, `epic_id`;
- `workitem_id`, `department_id`;
- `agent_job_id`, `agent_run_id`;
- `checkpoint_id`, `state_version`;
- `base_commit`, submitted commit, and artifact hashes;
- node name, attempt, duration, exit status, and error category.

Approvals, revisions, abandonment, execution-mode selection, Planner arbitration, Interface Contract freezing, reviews, integrations, closures, and release notifications are immutable audit events. SSE clients resume by durable event ID rather than replaying in-memory output.

## 16. Testing Strategy

### State and Contract Tests

- table-driven tests cover every legal and illegal DP transition;
- command idempotency and optimistic-lock conflicts are tested directly;
- every Codex node has valid, invalid, repairable, and unrepairable schema fixtures;
- WorkItem validation rejects missing goals, interfaces, dependencies, skills, paths, tests, criteria, or supervisor.

### Department and Isolation Tests

- all eight capability-table entries are fixed and addressable;
- the Planner cannot create a ninth department;
- the Council stops after three rounds and unresolved issues reach Planner arbitration;
- frozen contracts cannot be modified without a versioned decision;
- fixture Workers test path traversal, symlink escape, `.git` changes, network attempts, and unauthorized Skill access;
- a Worker cannot accept, integrate, or close its own item.

### Integration and Recovery Tests

- dependency waves schedule only ready WorkItems;
- rejected submissions return to assignment without losing evidence;
- Merge Coordinator ordering follows the WorkItem DAG;
- department closure requires child closure and department tests;
- DP-7 remains blocked until cross-interface and full integration tests pass;
- the API, job worker, outbox writer, and Worker process are terminated at transactional boundaries to verify checkpoint recovery and absence of duplicate effects.

### End-to-End Acceptance Test

A fixture Epic runs through DP-0 to DP-7, selects multiple departments, completes all three Council rounds, freezes an Interface Contract, runs isolated Worker commits, performs hierarchical reviews and integration, survives one forced restart, and produces a candidate integration branch. The assertion confirms that the main branch and remote refs are unchanged and that the user receives the Git-save reminder.

## 17. Migration Plan

1. Add new workflow, job, checkpoint, audit, contract, and artifact tables without modifying old conversation records.
2. Add the graph engine and workers behind a `full_flow_v2` workflow type. Existing sessions retain their legacy behavior.
3. Default new sessions to `full_flow_v2` and translate `/chat` requests into explicit commands.
4. Replace the monolithic workflow prompt with node-specific prompts and JSON Schemas.
5. Run compatibility and recovery tests against migrated SQLite databases.
6. After old sessions are archived and the new path is stable, remove the unused legacy Workflow Service and substring-based WorkItem parsing.

Database migrations are forward-only and preserve conversation history. No migration changes an existing session's `project_path` or workflow type.

## 18. Acceptance Criteria

The first version is complete only when all of the following are demonstrated:

1. DP-0 through DP-7 transitions are controlled exclusively by Python and covered by tests.
2. Duplicate commands and retried jobs produce exactly one durable effect.
3. A service restart resumes from the latest valid checkpoint without duplicate WorkItems, Worker runs, decisions, or merges.
4. The Planner uses only the fixed eight-department capability table.
5. Department Council negotiation stops after three rounds and Planner arbitration freezes remaining interfaces.
6. Every Employee SubWorkItem contains all mandatory specification fields.
7. Each Worker is isolated by worktree, process, working directory, sandbox, network policy, Skill mounts, and allowed paths.
8. Unauthorized path changes are rejected before integration.
9. Workers cannot approve or close their own tasks.
10. Lead, Merge Coordinator, Planner, and DP-7 closure permissions are enforced separately.
11. Integration and Interface Contract tests must pass before `closing`.
12. SQLite and repository artifacts reconcile by version and hash after failures.
13. Legacy `/chat` sessions remain usable during migration.
14. The system never merges or pushes the main branch automatically and always emits a Git-save reminder for a candidate version.

