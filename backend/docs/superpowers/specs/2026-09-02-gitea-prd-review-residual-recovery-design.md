# Gitea PRD Review Residual Recovery Design

**Date:** 2026-09-02  
**Status:** Approved by the user's request to fix the two residual review findings  
**Parent design:** `docs/superpowers/specs/2026-09-01-gitea-prd-review-mvp-design.md`

## Goal

Close the two load-bearing findings left by the scoped re-review without changing the public API or product scope:

1. a crash after a semantic reviewer result is durable must not call the reviewer again;
2. a Gitea comment ID must be unique across the complete pull-request review snapshot, not merely within one review.

## Decision 1: recover the complete prepare pipeline

`SpecService.prepare_external_revision` already builds a deterministic reviewer payload containing the command ID, input hash, normalized Spec hash, provenance, and generation request. Before creating a `review_spec` call, it must inspect prior calls for the same project and exact payload.

- An exact `RESULT_READY` call is validated as `SemanticReview`, then reused with its original call ID and reviewer session ID.
- An exact `PENDING` or `AMBIGUOUS` call fails closed as in-doubt. It must not create or execute another reviewer call.
- A malformed or binding-mismatched durable result fails closed; it is never silently ignored.
- Only when no reusable or unresolved exact call exists may a new reviewer call be persisted and executed.
- The returned prepared command contains both the generation call ID and the reused or new reviewer call ID so existing command evidence validation remains complete.

The recovery test must reproduce the real crash boundary: persist the reviewer `RESULT_READY`, interrupt before the command becomes `PREPARED`, retry the same command, and prove `review_spec` was invoked exactly once.

## Decision 2: PR-wide comment identity

`GiteaClient.list_comment_threads` must maintain one `seen_comment_ids` set for the entire pull-request traversal. Review IDs remain unique as today. Every root or reply comment ID is inserted into the PR-wide set before thread grouping.

If any comment ID appears again in the same or another review, the adapter returns `GITEA_INCOMPATIBLE` and no partial thread list. This preserves fail-closed behavior for ambiguous authoritative evidence.

## Testing and scope

- Use the existing SQLAlchemy fixtures, `ScriptedAgentGateway`, and `httpx.MockTransport`; no real Gitea, LLM, or network calls.
- Add focused RED tests before production edits, verify the expected failures, then make the minimum implementation changes.
- Run the affected focused suites, the complete 357-test regression suite, compile checks, and Git diff checks.
- Do not modify public routes, schemas, database schema, or unrelated workflow behavior.
- Keep `.DS_Store`, `.superpowers/`, and `tests/helpers/__init__.py` untracked and out of commits.

