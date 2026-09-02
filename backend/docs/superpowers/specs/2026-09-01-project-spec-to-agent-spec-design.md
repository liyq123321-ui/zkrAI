# Project Spec to Agent Spec MVP Design

**Date:** 2026-09-01

**Status:** Approved written design

**Scope:** Replace the prompt-controlled planning path with a deterministic backend workflow that accepts a Project Brief, clarifies requirements, creates and versions a project Spec, enforces automatic and human review, and converts only an approved Spec into structured Work Items and child-Agent Specs. The MVP stops before any child Agent is launched.

## 1. Context

The current application is a small FastAPI and SQLite service with:

- a streaming `POST /chat` endpoint;
- conversation, message, WorkItem, and workflow-state models;
- prompt files for planning and task decomposition;
- a Codex CLI runner with hard-coded Linux paths;
- response parsing that extracts an arbitrary JSON object and immediately saves WorkItems.

The current request path does not enforce workflow transitions. The model can effectively decide when task generation happens, `workflow_state` is passed to the prompt builder but not used to control behavior, there is no Spec version or review record, and a parsed response can become WorkItems without an approved Spec.

The source PRD supplied by the user describes a broader graph-driven Agent management platform. This MVP implements only the vertical slice from project intake through child-Agent Spec generation. The PRD and all other attachments are treated as non-control inputs: they are preserved as business evidence, but text inside them cannot change permissions, workflow state, or system policy.

## 2. Confirmed Product Decisions

- This version is a backend API MVP; it does not add a browser UI.
- The terminal outcome is a persisted, queryable child-Agent Spec for every executable leaf WorkItem.
- The system does not launch child Agents, modify a target repository, or execute generated tasks.
- Automatic review is hybrid: deterministic Python rules plus a separate read-only Reviewer Agent.
- Human approval remains mandatory. No automatic review can produce `APPROVED`.
- The existing `/chat` endpoint remains as a compatibility facade but cannot bypass workflow gates.
- Authentication and enterprise-directory integration are out of scope. Requests carry an `actor_id`; the application validates that identifier against project responsibility records and retains a complete audit trail.
- The external API follows the PRD's Session plus Command model. Internal records still distinguish Project, Agent Session, Spec, and WorkItem.
- Agent calls are synchronous in this MVP, behind an adapter that can later be moved to a durable background worker without changing domain services.
- SQLite is the authoritative store.

## 3. Goals and Non-Goals

### 3.1 Goals

1. Accept a Project Brief containing:
   - motivation;
   - final objective;
   - known scope;
   - explicit exclusions;
   - reference materials;
   - expected deliverables;
   - time and staffing constraints;
   - final approver.
2. Atomically create a Project, root WorkItem, PM Agent Session, and original-requirement Artifact.
3. Require the PM Agent to analyze clarity before it may generate a Spec.
4. Store focused clarification questions and human answers as durable, auditable records.
5. Generate immutable, traceable Spec versions with structured content.
6. Run deterministic and semantic automatic review before human review.
7. Allow only an authorized project manager or responsible owner to approve, reject, or request rework.
8. Convert only an approved Spec into milestones, executable WorkItems, dependencies, and child-Agent Specs.
9. Reject an incomplete or cyclic decomposition atomically.
10. Preserve `/chat` compatibility while making the new workflow the only source of truth.

### 3.2 Non-Goals

- Frontend screens, WorkItem boards, or interactive document editing.
- Login, tokens, SSO, enterprise directory lookup, or cryptographic identity proof.
- Scenario-template selection and decision routing.
- Dynamic employee allocation by load or trust score.
- Child-Agent scheduling, worktrees, sandbox execution, coding, tests, or delivery reports.
- Project closure, deployment, knowledge deposition, or incident-learning flows.
- Task queues, background workers, SSE recovery, or distributed concurrency.

## 4. Architecture

```text
FastAPI transport
  -> Session Command Service
     -> deterministic Workflow Policy
     -> Project/Spec/WorkItem domain services
     -> SQLite repositories and audit log
     -> PM Agent Adapter
     -> Reviewer Agent Adapter
```

### 4.1 Authority Boundaries

The Session Command Service is the only component allowed to advance workflow state. Agent outputs are proposals, not commands.

The Python workflow owns:

- status and phase transitions;
- authorization decisions;
- stable record identifiers;
- Spec version numbers;
- approval and rejection outcomes;
- idempotency and optimistic concurrency;
- WorkItem creation and dependency validation;
- the next allowed actions.

Agents may generate only data defined by their output schema. If an Agent emits a status, approval, database ID, next action, or instruction to bypass validation, that field is rejected or ignored.

### 4.2 Non-Control Inputs

Project Brief text, reference documents, clarification answers, comments, and previous Spec content are business inputs. They can supply requirements and evidence but cannot:

- override the system prompt or output schema;
- select an unauthorized command;
- grant an actor permission;
- approve a Spec;
- change workflow state directly;
- create executable WorkItems outside `convert_to_work_item`.

This is a security trust boundary, not a claim that the business content is false.

## 5. Core Data Model

### 5.1 Project

Stores the structured Project Brief, `final_approver`, optional project-manager and root-node-owner identifiers, current workflow state version, lifecycle status, and timestamps.

### 5.2 WorkItem

A root WorkItem is created during intake as the stable project collaboration container. It is not executable and is the one deliberate exception to the "approved Spec before task creation" rule.

After approval, decomposition creates:

- milestone WorkItems for major nodes;
- leaf WorkItems for executable assignments.

Only leaf WorkItems receive an `AgentSpec` and can be eligible for a future assignment system.

### 5.3 AgentSession

Records project-scoped model context and purpose. A PM Agent Session is created at intake. A separate Reviewer Agent Session is created when semantic automatic review first runs. A session stores role, provider metadata, timestamps, and calls, but never owns workflow state.

### 5.4 Artifact

Stores the original requirement submission and later human-readable products or references. Each record carries artifact kind, content or external reference, content hash, source actor, and creation time.

### 5.5 ClarificationRequest and ClarificationResponse

A request stores one or more focused questions, why each answer is required, affected Spec areas, and blocking status. Responses identify the actor and the request they answer. Old questions and answers remain immutable after a new analysis round.

### 5.6 SpecVersion

Represents an immutable project-level Spec revision. It stores:

- project and revision number;
- structured JSON content;
- optional rendered Markdown content;
- generation source;
- exact input Artifact and clarification identifiers;
- generating Agent Session and call identifier;
- parent Spec version;
- change summary;
- content hash;
- current review status;
- creation time.

A revision never changes its requirements content. Review status may advance through the legal state machine; content changes always produce a new revision.

### 5.7 SpecReview

Stores one review run with kind `RULE`, `AGENT`, or `HUMAN`, reviewer identity, input Spec hash, verdict, structured findings, comments, timestamps, and the command that caused it.

### 5.8 WorkItemDependency

Stores directed prerequisite relationships between generated WorkItems. The complete dependency set is validated as a directed acyclic graph before it is committed.

### 5.9 AgentSpec

Stores the immutable task envelope for one leaf WorkItem. It references the exact approved project Spec version and contains the context needed by a future child Agent without passing the entire project conversation.

### 5.10 AuditEvent and ProcessedCommand

`AuditEvent` records all important intake, clarification, generation, review, approval, rejection, rework, decomposition, validation-failure, and Agent-call events.

`ProcessedCommand` stores `command_id`, input hash, state version, result, and side-effect references. Reusing the same identifier with the same input returns the recorded result. Reusing it with different input returns a conflict.

## 6. Project and Spec State

Project workflow phases are:

```text
INTAKE
  -> NEED_CLARIFICATION
  -> SPECIFICATION
  -> REVIEW
  -> DECOMPOSITION
  -> AGENT_SPECS_READY
```

`AGENT_SPECS_READY` is the terminal state for this MVP.

Spec review states are:

```text
DRAFT
  -> AUTO_REVIEW
  -> HUMAN_REVIEW
  -> APPROVED
```

Exceptional states are:

```text
REWORK
NEED_CLARIFICATION
REJECTED
```

### 6.1 Intake and Clarification

`POST /sessions` validates the Project Brief structure and a client-supplied idempotency key, then atomically creates the Project, root WorkItem, PM Agent Session, original Artifact, and initial workflow state. After that transaction commits, the request synchronously asks the PM Agent to analyze:

- goal clarity;
- scope conflicts;
- missing critical material;
- required human decisions;
- assumptions;
- whether acceptance conditions can be verified.

If analysis succeeds, the response contains either focused questions with `NEED_CLARIFICATION`, or a readiness result with `SPECIFICATION`. If the Agent call fails, intake records remain durable, the phase remains `INTAKE`, and the response exposes a retryable failure without inventing clarification results.

A `message` command in `NEED_CLARIFICATION` saves a ClarificationResponse and reruns analysis. PM analysis cannot create a Spec or WorkItem.

### 6.2 Spec Generation and Review

`create_spec` is legal only after readiness analysis. It creates a `DRAFT` SpecVersion and immediately begins automatic review:

1. move the revision to `AUTO_REVIEW`;
2. run deterministic validation;
3. run the separate Reviewer Agent if deterministic structure is valid;
4. merge the two reports without hiding a blocking finding;
5. select `NEED_CLARIFICATION`, `REWORK`, or `HUMAN_REVIEW` from deterministic policy.

The Reviewer Agent cannot approve a Spec. Only an authorized `approve` command in `HUMAN_REVIEW` can produce `APPROVED`.

`rework` records human comments and moves the current version to `REWORK`. `revise` generates a new immutable version from the approved inputs plus review comments, then repeats automatic review. `reject` moves the current version to `REJECTED` and prevents decomposition.

### 6.3 Decomposition

`convert_to_work_item` is legal only for the current `APPROVED` Spec version. It asks the PM Agent for a structured WorkBreakdown, then validates and writes the entire result in one transaction.

The Agent's WorkBreakdown uses unique `local_key` values and dependency keys because the Agent is not allowed to choose database identifiers. The service generates WorkItem IDs, resolves every local key to its server ID, and then writes the final AgentSpecs with those server IDs.

The transaction creates milestones, leaf WorkItems, dependency edges, and one AgentSpec per leaf. If any required field is missing, any dependency key is unknown, any dependency is cyclic, or any task lacks an executable acceptance criterion, the entire write rolls back. Success moves the project to `AGENT_SPECS_READY`.

## 7. Session and Command API

### 7.1 Endpoints

```text
POST /sessions
POST /sessions/{session_id}/commands
GET  /sessions/{session_id}/state
GET  /sessions/{session_id}/specs
GET  /sessions/{session_id}/specs/{version}
GET  /sessions/{session_id}/work-items
GET  /sessions/{session_id}/work-items/{work_item_id}
GET  /sessions/{session_id}/agent-specs
GET  /sessions/{session_id}/agent-specs/{agent_spec_id}
GET  /sessions/{session_id}/events
POST /chat
```

`GET /sessions/{id}/state` returns current phase, current Spec version and review state, state version, legal actions, next action, and outstanding clarification or review findings.

### 7.2 Command Envelope

Session creation accepts a structured Project Brief plus `request_id` and `actor_id`. Repeating the same `request_id` with the same body returns the original Session; reusing it with different content returns `409`.

```json
{
  "command_id": "client-generated-idempotency-key",
  "action": "message|create_spec|revise|approve|reject|rework|convert_to_work_item",
  "expected_state_version": 12,
  "actor_id": "employee-or-manager-id",
  "message": "optional human input",
  "payload": {}
}
```

Legal actions depend only on stored state. The model never receives the ability to select an action.

### 7.3 `/chat` Compatibility

The existing request shape and streaming behavior remain available. A new natural-language chat creates a compatible Session whose incomplete Project Brief enters clarification. A follow-up message becomes a `message` command. The facade returns the Session ID, state, focused questions or next action, and Agent text for display.

The compatibility route no longer parses arbitrary model output into WorkItems. It cannot call `create_spec`, `approve`, or `convert_to_work_item` implicitly.

## 8. Agent Contracts

The application defines four typed adapter operations:

```text
analyze_brief(...)  -> ClarificationAnalysis
generate_spec(...)  -> ProjectSpecPayload
review_spec(...)    -> SemanticReview
decompose_spec(...) -> WorkBreakdown
```

Production uses a configurable Codex CLI adapter. Tests use a deterministic fake implementing the same protocol. The binary, home, working directory, timeout, and model settings are configuration; no Linux-specific path is embedded in application code.

Every call uses a node-specific JSON Schema. Invalid output gets at most two schema-repair attempts in the same logical call. A third invalid result records a failed call and leaves workflow state at the last safe point.

## 9. Project Spec Contract

Every `ProjectSpecPayload` contains:

1. background and goals;
2. users and usage scenarios;
3. functional requirements;
4. non-functional requirements;
5. system boundary and exclusions;
6. fixed parts;
7. configurable parts and extension points;
8. core objects;
9. main flows;
10. exceptional flows;
11. permissions and responsibilities;
12. deliverable and format requirements;
13. verifiable acceptance criteria;
14. risks;
15. assumptions;
16. open questions;
17. source references to the Brief, clarification responses, and supplied materials.

Functional and non-functional requirements carry stable identifiers. Acceptance criteria reference those identifiers so coverage can be checked without searching prose.

## 10. Hybrid Automatic Review

### 10.1 Deterministic Rules

Python checks:

- required sections and field types;
- unique stable requirement identifiers;
- at least one acceptance criterion per required behavior;
- acceptance criteria containing an observable verification method and expected result;
- references to existing input Artifacts;
- explicit inclusions and exclusions;
- separation of fixed, configurable, and extensible behavior;
- no unresolved blocking question before human review;
- valid responsibility identifiers where a responsibility is required.

### 10.2 Semantic Reviewer Agent

The Reviewer receives the immutable Project Spec plus the exact persisted generation-request snapshot: Project Brief, Artifact bodies and references, clarification questions and answers, and any parent Spec/revision comments. Review retries rebuild this payload from the generator `AgentCall`, never from mutable current project rows.

The read-only Reviewer Agent checks:

- contradictory requirements or boundaries;
- ambiguous or non-testable language;
- unsupported assumptions presented as confirmed facts;
- scope added without a Project Brief or clarification source;
- missing main, exceptional, permission, or responsibility flows;
- acceptance criteria that are structurally present but do not prove the requirement;
- risks that require human decisions.

### 10.3 Finding Schema

```json
{
  "code": "UNTESTABLE_REQUIREMENT",
  "severity": "BLOCKER|MAJOR|MINOR|INFO",
  "spec_path": "functional_requirements.FR-003",
  "message": "The response-time requirement has no measurable threshold.",
  "suggested_resolution": "Specify workload, data size, percentile, and maximum latency.",
  "blocks_progress": true
}
```

A rule finding and an Agent finding are retained separately even if they describe the same problem. The merged policy may reference both but cannot discard either source record.

## 11. WorkBreakdown and Child-Agent Spec Contract

A WorkBreakdown contains:

- major milestone nodes;
- milestone and WorkItem dependencies;
- executable leaf WorkItems;
- one complete AgentSpec proposal per leaf.

Each milestone and leaf proposal has a unique `local_key`. AgentSpec proposals reference their task and prerequisites by local key. `work_item_id` and `dependency_work_item_ids` in the persisted contract below are server-generated values resolved from those keys; they are never accepted from model output.

Each AgentSpec contains:

```json
{
  "work_item_id": "stable-server-generated-id",
  "source_spec_version_id": "approved-spec-version-id",
  "objective": "concrete outcome",
  "scope": ["included work"],
  "exclusions": ["explicitly excluded work"],
  "context_refs": ["artifact-or-spec-reference"],
  "inputs": ["required input"],
  "outputs": [
    {"name": "deliverable", "format": "json", "required": true}
  ],
  "fixed_constraints": ["constraint that cannot be changed"],
  "configurable_parts": ["allowed configuration"],
  "extension_points": ["explicit extension boundary"],
  "acceptance_criteria": [
    {
      "criterion": "observable condition",
      "verification_method": "exact check",
      "expected_result": "required outcome"
    }
  ],
  "required_skills": ["skill capability"],
  "allowed_tools": ["approved tool"],
  "allowed_paths": ["future workspace boundary"],
  "responsible_role": "accountable role",
  "suggested_assignee": "known employee or Agent reference",
  "dependency_work_item_ids": ["prerequisite-id"],
  "test_obligations": ["required verification"],
  "risks": ["task-specific risk"],
  "open_questions": []
}
```

An AgentSpec is valid only when:

- it has at least one required output;
- it has at least one executable acceptance criterion;
- all dependencies reference WorkItems in the proposed breakdown;
- `required_skills`, `responsible_role`, and `suggested_assignee` remain distinct concepts;
- all blocking questions are resolved;
- every non-blocking open question names a risk owner and accepted consequence;
- fixed constraints, configurable parts, and extension points do not contradict the approved project Spec.

## 12. Authorization

This MVP does not authenticate a caller. It enforces stored responsibility relationships using the supplied `actor_id`:

- clarification responses may come from a recorded project participant;
- human review comments may come from the final approver, project manager, or root WorkItem's responsible owner;
- the final approver, project manager, or root WorkItem's responsible owner may issue `approve` or `reject`; every such decision records which authority was used, while `final_approver` remains the person accountable for final confirmation;
- service-generated Agent identities cannot issue human-review commands;
- every decision stores actor, command, prior state version, new state version, and timestamp.

The lack of identity proof is exposed as an explicit deployment limitation. A production deployment must authenticate the caller before trusting `actor_id`.

## 13. Error Handling and Consistency

HTTP errors are stable:

- `400` for a command illegal in the current state;
- `403` for a known actor without required responsibility;
- `404` for a missing Session, Spec, WorkItem, or AgentSpec;
- `409` for stale `expected_state_version` or conflicting command reuse;
- `422` for an invalid Brief, command payload, or Agent result;
- `503` for an unavailable, failed, or timed-out Agent call.

No failure advances state implicitly. Each failure records its reason, attempt count, and command or Agent call. The current durable state remains queryable and the same safe operation can be retried.

## 14. Testing Strategy

### 14.1 Unit Tests

- all legal and illegal state transitions;
- human-review authorization;
- idempotent command handling;
- stale state-version rejection;
- Spec deterministic rule findings;
- semantic-review merge policy;
- dependency existence and cycle detection;
- AgentSpec structural and cross-Spec validation.
- concurrent identical intake requests sharing one durable PM-analysis claim;
- public event history redacting Agent diagnostics and local paths.

### 14.2 Service Tests

Use the real workflow services and isolated SQLite database with a controlled fake Agent to cover:

- intake readiness without clarification;
- one or more clarification rounds;
- automatic review to `REWORK`;
- automatic review to `NEED_CLARIFICATION`;
- human approval, rework, and rejection;
- revision history and unchanged old content;
- atomic decomposition rollback.

### 14.3 API Integration Tests

- Session creation and returned initial state;
- command idempotency and conflict behavior;
- optimistic concurrency;
- stable HTTP errors;
- Spec, WorkItem, AgentSpec, state, and event queries;
- `/chat` compatibility without gate bypass.

### 14.4 End-to-End Acceptance Flow

One test runs:

```text
Project Brief
  -> Project/root WorkItem/PM Session/original Artifact
  -> NEED_CLARIFICATION
  -> human response
  -> DRAFT
  -> AUTO_REVIEW with rule and Agent receipts
  -> HUMAN_REVIEW
  -> APPROVED
  -> milestone and leaf WorkItems
  -> complete child-Agent Specs
  -> AGENT_SPECS_READY
```

The acceptance suite also proves:

- reference-material prompt injection cannot change state or skip review;
- an unapproved Spec cannot be converted;
- one invalid task causes the full decomposition to roll back;
- an AgentSpec references the exact approved Spec version;
- no child Agent process is started.

## 15. Acceptance Criteria

The MVP is accepted when:

1. A valid Project Brief atomically creates all four required intake records.
2. An incomplete or contradictory Brief produces durable focused questions and `NEED_CLARIFICATION`.
3. PM analysis cannot create a Spec or WorkItem.
4. Every Spec revision preserves source inputs, generator, parent revision, change summary, hash, and review history.
5. Rule and Reviewer Agent reports are separately queryable whenever semantic review is reached; a structural rule failure records why semantic review was skipped.
6. Only an authorized human command can produce `APPROVED`.
7. `convert_to_work_item` rejects every non-approved revision.
8. A valid approved Spec produces milestones, a dependency DAG, leaf WorkItems, and one valid AgentSpec per leaf.
9. Invalid decomposition produces no partial WorkItems or AgentSpecs.
10. `/chat` remains usable but cannot bypass the workflow.
11. The complete automated suite passes without starting a real child Agent.
12. The system exposes `AGENT_SPECS_READY` and the generated task packages as the terminal MVP result.

## 16. Future Extension Boundaries

The following components may be added without changing the approved contracts:

- a background AgentJob worker can implement the existing Agent adapter asynchronously;
- authentication can resolve trusted `actor_id` values before command submission;
- a browser client can render state, questions, review findings, and AgentSpecs from existing queries;
- decision routing and scenario templates can run before Spec generation;
- an assignment service can consume valid AgentSpecs after `AGENT_SPECS_READY`;
- delivery, review, execution, and closure graphs can extend WorkItem state after assignment.

