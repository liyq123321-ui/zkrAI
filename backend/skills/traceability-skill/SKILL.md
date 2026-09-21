---
name: traceability-skill
description: Build and audit requirement traceability across PRD blocks, FR/NFR requirements, acceptance criteria, WorkItems, Agent Specs, and tests. Use when generating, revising, reviewing, or decomposing a firstFlight PRD.
---

# Requirement Traceability

Keep one evidence-backed chain from source intent to verification:

`PRD Block -> FR/NFR -> Acceptance Criterion -> WorkItem -> Agent Spec -> Test evidence`.

## Rules

- Treat the approved PRD and authenticated human decisions as authority. Never turn a planning suggestion into an approved requirement.
- Keep stable requirement identifiers when meaning is unchanged. When meaning changes, update every downstream reference in the same result.
- Every acceptance `requirement_id` must name a defined FR/NFR; every FR/NFR must have acceptance coverage.
- Every executable WorkItem must map its acceptance criteria to assigned requirements, and the complete breakdown must cover the approved registry.
- Record a missing link as a gap. Do not manufacture a source, approval, implementation result, or test result to close it.
- Detect orphan requirements, acceptance criteria, tasks, and tests in both forward and backward directions.
- When an authorized correction changes a requirement or mapping, synchronise directly linked Blocks needed to restore the chain. Evidence Blocks are the starting scope, not a reason to leave an adjacent mapping knowingly stale; unrelated Blocks remain out of scope.

Return the smallest correction that restores the full chain while preserving valid IDs and approved scope.
