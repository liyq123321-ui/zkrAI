---
name: deliver-acceptance-criteria
description: Produce independently testable Given/When/Then acceptance criteria for PRD feature slices, including recovery and relevant non-functional behavior.
---

# Acceptance Criteria

Bind every criterion to a specific FR or NFR. Cover the primary success flow first,
then material boundary cases, user-visible failures and recovery behavior. Add
performance, accessibility, security, reliability or audit expectations only when
they are relevant and supported.

Use observable Given/When/Then semantics. Each criterion must have one pass/fail
outcome and must not depend on an unstated implementation choice. Merge duplicate
criteria and keep requirement identifiers stable.
