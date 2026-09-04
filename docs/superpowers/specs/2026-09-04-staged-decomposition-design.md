# Staged Decomposition Design

## Problem

The decomposition workflow currently asks one Agent call to produce milestones,
tasks, every Agent Spec, and every detailed implementation plan. The resulting
JSON is large enough that a repair attempt can correct one contract violation
while introducing another. The temperature-converter project exhausted all
three structured-output attempts this way: two responses omitted an Agent Spec,
and the final response assigned implementation requirements to the wrong task.

The existing strict validation and atomic database transaction are correct and
must remain. The generation boundary, not the validation rule, is the problem.

## Goals

- Generate and validate the structural work breakdown separately from detailed
  per-task implementation plans.
- Bind every reusable result to the project UUID, approved Spec UUID, Spec
  content hash, approved input references, and canonical task content.
- Reuse valid staged results after a failed attempt instead of regenerating
  successful work.
- Keep work items and Agent Specs invisible until every stage and review passes.
- Preserve the current command API, legal actions, final project phase, and
  all-or-nothing materialization behavior.
- Continue converting generation-time local keys to work-item UUIDs before
  persistence.

## Non-goals

- Do not weaken requirement coverage, dependency, source-reference, or
  implementation-plan validation.
- Do not assign UUIDs to proposed tasks before persistence or replace the
  human-readable requirement IDs inside an approved Spec.
- Do not expose partially planned tasks on the Kanban board.
- Do not add a new frontend workflow or require a database migration.

## Chosen Architecture

The existing `agent_calls` table is the durable checkpoint store. Checkpoints
remain Agent evidence rather than domain work items. A staged coordinator in
`DecompositionService` runs three phases:

1. **Base breakdown** — `decompose_spec` produces milestones, tasks, and one
   Agent Spec per task with `implementation_plan: null`. Structural validation
   runs with `require_implementation_plan=False` while every other rule remains
   active.
2. **Per-task planning** — `plan_task` is called for each Agent Spec with bounded
   concurrency of two. Each returned `ImplementationPlan` is validated against
   that task's acceptance requirements before it becomes a reusable checkpoint.
3. **Full review and publication** — the validated plans are attached to the
   canonical base breakdown, full validation runs with
   `require_implementation_plan=True`, the Reviewer checks the complete result,
   and the existing command transaction creates all work items and Agent Specs.

This keeps each generation contract small without weakening the final contract.

## Checkpoint Identity and Recovery

Every staged request carries a `stage` marker and immutable binding fields:

- `project_id`
- `source_spec_version_id`
- `source_spec_content_hash`
- canonical `input_refs`
- for a plan, the adopted base-decomposition call UUID
- for a plan, `work_item_key` plus a canonical task-spec hash

A retry may reuse a checkpoint only when all binding fields match the current
approved snapshot and the stored response passes current model and service
validation again. Lookup never uses project titles, task titles, or bare FR/NFR
names. A changed PRD, changed task boundary, mismatched project, invalid response,
or non-`RESULT_READY`/non-`SUCCEEDED` call makes the checkpoint ineligible.

The base checkpoint is one valid `decompose_spec` call. Each task plan is one
valid `plan_task` call. Successful plan calls remain `RESULT_READY` when another
task fails, allowing the next command to reuse them. At final materialization,
every adopted base, plan, and Reviewer call is changed to `SUCCEEDED` in the same
transaction as the work-item creation.

## Base Breakdown Generation

The `pm_decompose` and `pm_revise_breakdown` prompts explicitly require
`implementation_plan: null` during the base stage and forbid detailed plan
generation. `validate_node_output` reads a trusted control flag supplied by the
gateway, not Agent text, and validates a base `WorkBreakdown` without requiring
plans.

The base result still must provide:

- globally unique local keys within the proposed breakdown;
- valid milestone/task parents and an acyclic dependency graph;
- exactly one Agent Spec per task;
- complete approved FR/NFR acceptance coverage;
- exact approved source references;
- at least one required output per task; and
- consistent task and Agent-Spec dependencies.

This stage retains the existing bounded structured-output repair loop.

## Per-task Planning

The planner receives only one canonical Agent Spec, the approved Spec snapshot,
UUID bindings, and summaries of related task contracts needed for handoffs. It
does not receive authority to alter task scope, acceptance criteria, ownership,
outputs, or dependencies.

At most two planner calls run concurrently. A task plan is accepted only after
`validate_implementation_plan` proves that:

- every referenced requirement exists in the approved Spec;
- every step references only requirements assigned to that task;
- every task requirement has implementation coverage; and
- steps, interfaces, structures, and fields satisfy uniqueness rules.

If one task fails, completed sibling plans remain reusable. The user-facing
project stays in `REVIEW` and the retry action continues only the missing or
invalid planning stages.

## Semantic Review and Repair

The Reviewer receives the fully assembled and strictly validated breakdown.
When findings implicate only implementation plans, the coordinator regenerates
only the named task plans with the prior plan and review findings as evidence.
When findings cannot be mapped safely to specific task plans, all task plans are
eligible for one bounded repair round; the base scope is never silently changed.
Human-decision findings stop automatic repair as they do today.

The existing three-round semantic-review limit remains. A review repair creates
new plan checkpoints; prior successful checkpoints stay as audit evidence but
are not adopted unless they are part of the final reviewed breakdown.

## Atomicity and Failure Handling

No milestone, task, dependency, or Agent Spec is created during generation,
planning, or review. The command materialization transaction rechecks the
approved Spec snapshot, project version, local-key collisions, full breakdown,
and all adopted Agent-call evidence before writing domain rows.

Failure records expose the stage and affected task key in durable audit data
while retaining the existing public error code compatibility. Invalid raw model
outputs remain rejected and are not treated as checkpoints. Pending calls are
always settled as `FAILED` or `RESULT_READY` before control returns.

## Compatibility

- The frontend continues sending `convert_to_work_item` and displays the same
  retry action after failure.
- Existing already-persisted Agent Specs and the standalone enrichment command
  keep their current behavior.
- The final `PreparedDecomposition` includes all adopted call UUIDs so command
  evidence validation and audit trails cover the base, every plan, and Reviewer.
- Persisted work-item dependencies and Agent-Spec dependency references continue
  to use generated UUIDs. Local keys exist only inside the canonical staged
  proposal before the database rows exist.

## Test Strategy

Unit tests will prove that base-stage output accepts null plans while ordinary
decomposition output still requires plans, and that UUID/snapshot binding rejects
cross-project or stale checkpoints.

Service integration tests will prove that:

- decomposition calls the base generator before per-task planners;
- no more than two planners run concurrently;
- plans are attached to the correct task and fully validated;
- a failed sibling plan leaves no domain rows but preserves valid checkpoints;
- a new retry reuses the matching base and completed plans and calls only missing
  planners;
- changed Spec content, task content, project UUID, or source references prevents
  reuse;
- final materialization marks exactly the adopted calls `SUCCEEDED` and creates
  all work items atomically; and
- an unassigned requirement such as the observed `T4-S2` failure is isolated to
  that task's planner repair instead of forcing regeneration of the full
  breakdown.

The full backend and frontend suites, static checking, production build, and a
live retry of the temperature-converter decomposition are required before the
change is considered complete.
