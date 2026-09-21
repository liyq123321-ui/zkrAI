---
name: prd-consistency-skill
description: Audit PRD and Spec content for cross-block contradictions, duplicate requirements, terminology drift, scope conflicts, state conflicts, and mismatched acceptance behavior. Use during generation, rewrite, and semantic review.
---

# PRD Consistency

Compare the whole document, not isolated sections.

Check these shared dimensions across background, goals, scope, requirements, objects, flows, permissions, deliverables, risks, acceptance criteria, and diagrams:

- the same term names the same concept;
- actors and permissions agree;
- object fields and state transitions agree;
- numerical limits, units, timing, and defaults agree;
- included scope, exclusions, and future extensions do not conflict;
- requirements are neither duplicated nor silently weakened elsewhere;
- acceptance criteria verify the exact behavior and failure handling stated by their requirements;
- diagrams do not introduce or omit material entities or relationships.

For each inconsistency, identify both locations, classify it as duplicate, contradiction, definition drift, scope conflict, state conflict, or missing link, and propose the smallest evidence-backed correction. Do not resolve a genuine product decision by guessing.

When the host authorizes correction, treat the Blocks containing the evidence as the primary scope rather than an isolated write fence. Update a directly related parent, child, or dependency Block when that is necessary to keep one rule consistent, but do not rewrite unrelated chapters. Combine compatible corrections aimed at the same Block instead of emitting duplicate actions.
