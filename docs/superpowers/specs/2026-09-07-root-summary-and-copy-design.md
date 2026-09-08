# Root Summary, UUID Copy, and Project Detail Design

## Goal

Make project-level work items easier to scan and identify without discarding the original request. Root work items gain an AI-generated summary of at most 20 characters, every Kanban card exposes a copyable UUID, the root filter uses the summary plus UUID, and opening a root card shows a project-detail header before the existing PRD review UI.

## Scope

- Persist a `summary` on `WorkItem`; this iteration populates and consumes it for `ROOT` items.
- Generate summaries through the existing PM brief-analysis call, not through an extra AI request during normal project creation.
- Backfill existing root work items whose summary is missing without overwriting summaries already stored.
- Update Kanban cards, the root filter, and the root detail modal.
- Preserve milestone monitoring and task detail behavior.

## Summary Contract

- `summary` is a nullable database column so the schema can migrate existing rows safely.
- A valid generated root summary is trimmed, single-line, non-empty, and at most 20 Unicode characters.
- The PM analysis prompt must return the summary in `brief_updates.summary`. Summary validation is isolated from the rest of the PM result: the backend persists only a valid value and ignores an invalid value without failing project intake.
- New project intake initially creates the root row, then the existing PM analysis persists the generated summary on that root. No second AI call is introduced.
- Later PM clarification analyses may update the root summary when the confirmed project objective changes.
- Read APIs expose `summary` on each work item. Until generation succeeds, clients fall back to the original title so a project never becomes unnamed.

## Existing-Project Backfill

Provide an idempotent backend command that:

1. Selects only `ROOT` work items with a null or blank summary.
2. Runs the current project brief through the same PM analysis contract.
3. Persists only the returned summary; it must not apply clarification questions or other brief updates.
4. Commits one project at a time, continues after an individual failure, and reports updated, skipped, and failed project IDs.
5. Never replaces a non-empty summary.

Run this command once against the current local database as part of delivery. A partial failure is visible in the command result and leaves the original title fallback intact.

## Database and API Changes

- Add `WorkItem.summary` and include it in fresh database creation.
- Extend the existing SQLite forward migration for `work_items` with the nullable `summary` column; running initialization repeatedly remains safe.
- Add `summary` to `WorkItemRead` and the frontend `WorkItemDto`.
- Extend `ProjectBriefUpdates` with the validated optional summary field.
- When project analysis is materialized, copy a returned summary to the project's root work item in the same transaction as the other accepted analysis results.
- Session catalog compatibility is retained. Frontend filter labels are derived from the loaded root work item, with the catalog title as a fallback.

## Kanban Card Interaction

- Every Kanban work-item card renders its top-left UUID through one reusable `CopyableWorkItemId` control.
- Activating the UUID copies the raw UUID without the visual `#` prefix.
- Pointer and keyboard activation stop propagation so copying never opens the card.
- The control has an explicit accessible name, visible hover/focus styling, and workspace-level `role="status"` feedback for success or failure.
- The root card heading is `summary` when available. Its body continues to show the original full title or objective, preserving the user's request.
- Milestone and task headings retain their existing titles.

## Root Filter

- Each root option displays `summary（UUID）`.
- If a summary is temporarily unavailable, the option displays `original title（UUID）`.
- Selection continues to use the UUID, so label changes do not alter filter state.
- The filter's checkbox accessible name matches the visible summary-and-UUID label.

## Project Requirement Detail

Opening a root card that has PRD content changes the modal title from `PRD 审核` to `项目需求详情`. Content appears in this order:

1. Copyable root UUID.
2. Root summary as the primary heading.
3. A `详情` section containing the original, complete root title; if title is absent, use objective, then description.
4. The existing PRD review panel and all of its current tabs, actions, review findings, and empty/error states.

This wrapper is a focused `RootWorkItemDetail` component. It receives the root work item and renders the existing `PrdReviewPanel` as its lower content rather than duplicating PRD behavior.

When a root has no PRD content, its card body remains disabled and does not open a modal. The UUID copy control remains an enabled sibling control with normal visual contrast, so copying is unaffected by the disabled card body.

## Error Handling and Fallbacks

- Invalid AI summaries are ignored and do not fail the surrounding PM analysis or replace a stored valid summary.
- A missing summary never blocks project creation, listing, filtering, or PRD review; the original title is the display fallback.
- Clipboard rejection displays `复制失败` and leaves navigation untouched.
- Backfill failures are isolated per project and reported without rolling back successful projects.

## Testing

- Summary-validation tests cover valid, blank, multiline, and over-20-character values without invalidating the surrounding PM analysis.
- Database migration tests confirm the new column is added idempotently and legacy work-item data is preserved.
- Project-service tests prove PM analysis persists a new summary and updates it after clarification.
- Backfill tests prove missing-only updates, non-overwrite behavior, per-project isolation, and result reporting.
- Query/API tests prove `summary` is returned.
- Frontend tests prove root cards use summary, retain the full original detail, root filter labels use `summary（UUID）`, UUID activation copies without opening a card, clipboard errors report failure, roots without PRD keep only their body disabled, and the project detail ordering precedes PRD review when PRD exists.
- Full backend and frontend suites, TypeScript checking, and the production frontend build must pass.

## Non-goals

- Do not summarize milestone or task titles in this iteration.
- Do not shorten or overwrite the original root `title` or objective.
- Do not change root-filter selection semantics.
- Do not redesign the PRD review panel itself.

## Acceptance Criteria

- Newly analyzed projects persist an AI-generated root summary no longer than 20 characters.
- Existing roots can be backfilled once, without overwriting existing summaries.
- Root Kanban headings show summaries while full requests remain visible in card detail text and the root detail modal.
- Every Kanban card UUID can be copied independently of card navigation.
- Root filter entries read `summary（UUID）`.
- Root cards with PRD open `项目需求详情` with UUID, summary, original detail, then PRD review in that order; roots without PRD keep the card body disabled while UUID copy remains available.
