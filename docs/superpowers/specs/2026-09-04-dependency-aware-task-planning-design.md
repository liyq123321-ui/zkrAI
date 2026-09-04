# Dependency-Aware Task Planning Design

## Goal

Make child Agent Spec planning respect the dependency graph for every project,
not only for a particular task shape. The system may still derive the complete
base task graph in one call, but it must establish detailed implementation plans
in dependency order so consumers receive validated producer contracts.

The temperature-converter project remains split into an implementation task and
a dependent test task because it is a workflow-validation fixture, not because
the project intrinsically requires two delivery units.

## Current Problem

The base decomposition already provides an acyclic graph through
`dependency_keys`, but `_plan_tasks` currently launches every missing task plan
at once. `related_tasks` therefore contains sibling task descriptions whose
`implementation_plan` values may still be null. A producer and consumer can
independently invent incompatible interface names, DOM locators, state enums, or
data shapes.

Plan-only semantic repair has the same problem: all implicated plans are cleared
and replanned concurrently. Even when T2 depends on T1, T2 cannot reliably use
the corrected T1 contract.

## Selected Approach

Use topological planning waves over the existing task dependency DAG.

- Build deterministic topological layers from `AgentSpecProposal.dependency_keys`.
- Tasks in the same layer have no dependency relationship and may be planned
  concurrently, subject to the existing concurrency limit of two.
- A layer starts only after every preceding dependency layer has completed and
  its plans have passed structural validation.
- Keep base decomposition, final semantic review, atomic domain persistence, and
  existing task boundaries unchanged.

This preserves useful parallelism without introducing a separate global contract
registry. Fully serial planning would be simpler but unnecessarily slow for
independent branches. A separate contract-generation phase would be more complex
and add another model output contract before there is evidence it is needed.

## Dependency Contract Input

Every `plan_task` payload receives `dependency_contracts`, ordered by the task's
declared direct `dependency_keys`. Each entry contains:

- the upstream `work_item_key`, which is a base-breakdown-local identifier;
- the upstream immutable task specification;
- the upstream validated `implementation_plan`;
- a canonical hash of the upstream task specification and plan.

The prompt instructs the planner to treat these entries as the authoritative
producer contracts and adapt the consumer to them. `related_tasks` remains
available for non-authoritative project context, but a planner must not infer a
contract from a related task whose plan is absent or from another independent
task.

Persisted domain relationships continue to use server-generated WorkItem UUIDs.
The local keys are used only inside the frozen pre-persistence breakdown because
no WorkItem UUID exists yet. Durable Agent-call bindings include the approved
Spec UUID/hash, base checkpoint UUID, exact task snapshot, and dependency
contract hashes, preventing name-based or stale-contract reuse.

## Initial Planning Flow

1. Generate and validate the plan-free base breakdown, including the complete
   acyclic dependency graph.
2. Compute deterministic topological layers.
3. For each layer, invoke `plan_task` concurrently for missing plans, bounded by
   two calls.
4. After the layer settles, validate and attach every successful plan before
   constructing payloads for the next layer.
5. Stop on any failure. Preserve validated successful calls as durable
   checkpoints, but do not persist WorkItems, dependencies, or AgentSpecs.
6. After all layers complete, validate the full breakdown and run the semantic
   Reviewer before the existing atomic persistence step.

## Repair Flow

When semantic review identifies plan-only findings:

1. Resolve the directly implicated tasks from exact Agent Spec paths.
2. Expand the repair set to include every transitive dependent of an implicated
   task. An upstream contract change must invalidate all downstream consumers.
3. Clear plans only for that expanded set; retain unrelated plans.
4. Replan the expanded set in topological layers. Retained upstream plans may be
   supplied as dependency contracts immediately; repaired upstream plans become
   available only after their layer succeeds.
5. Bind each new or adopted plan checkpoint to the exact dependency-contract
   hashes. A changed upstream plan therefore prevents reuse of a stale downstream
   checkpoint.
6. Reassemble plan evidence in the original Agent Spec order and run full
   validation and semantic review.

A finding that targets only a leaf task does not invalidate its producers. A
finding that cannot be mapped to exact plan paths retains the existing safe
fallback of repairing all plans, still in dependency order.

## Existing Agent Spec Enrichment

The same topological-wave semantics apply when enriching already persisted
AgentSpecs that lack detailed plans. That service reconstructs the canonical DAG
from WorkItem UUID relationships, maps it to local keys, and then uses the shared
ordering and dependent-closure helpers. This prevents the initial decomposition
path and later enrichment path from drifting into different planning behavior.

Existing AgentSpec persistence remains atomic. No partial plan update is exposed
when a later layer or the Reviewer fails.

## Recovery and Evidence

- Successful plan calls remain `RESULT_READY` until the complete decomposition is
  adopted.
- Retrying an unchanged command or starting a new eligible command may adopt a
  plan only when all existing checkpoint bindings and dependency-contract hashes
  match.
- If an upstream plan changes, only its transitive downstream checkpoints become
  ineligible; unrelated branches remain reusable.
- Every started call must settle as `RESULT_READY`, `SUCCEEDED`, or `FAILED`,
  including cancellation paths.
- Review evidence lists exactly one current plan-call UUID per task in canonical
  Agent Spec order, regardless of topological execution order.

## Error Handling

- Unknown dependencies and cycles remain base-breakdown validation errors and no
  planning calls start.
- A missing validated plan for a declared dependency is an internal scheduling
  error; the downstream planner must not run.
- One failure in a planning wave waits for all already-started calls in that wave
  to settle, records their evidence, and stops later waves.
- Snapshot checks continue before calls and before atomic persistence. A changed
  approved PRD, input reference set, permissions, task graph, or AgentSpec
  snapshot stops reuse and persistence.

## Testing

Automated tests must demonstrate:

- a dependent planner starts only after its producer completes;
- independent tasks in one topological layer still run concurrently with a
  maximum of two active calls;
- the dependent payload contains the exact validated upstream contract and hash;
- a multi-level graph is planned layer by layer in deterministic order;
- an upstream repair includes and replans its transitive dependents, while a leaf
  repair leaves producers unchanged;
- changing an upstream plan invalidates the downstream checkpoint but preserves
  unrelated branch checkpoints;
- a wave failure leaves no domain rows and retains settled successful planning
  evidence for retry;
- both new decomposition and existing AgentSpec enrichment follow the same
  dependency rules;
- the existing full backend suite and frontend checks remain green.

## Non-Goals

- Do not merge implementation and test tasks automatically based on project size.
- Do not change task execution dependency semantics or persisted DAG direction.
- Do not introduce a new database table or standalone contract-generation Agent.
- Do not weaken final semantic review, full breakdown validation, or atomic
  persistence.
