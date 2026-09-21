---
name: requirement-registry-skill
description: Normalize natural-language PRD content into a unique FR/NFR requirement registry and detect semantic duplicates even when identifiers differ. Use before PRD acceptance, rewrite, decomposition, or traceability review.
---

# Requirement Registry

Use `functional_requirements` and `non_functional_requirements` as the canonical registry.

## Identity and deduplication

Compare requirements by meaning, not by identifier or wording. Build a semantic key from:

- accountable actor or system;
- trigger and precondition;
- action or obligation;
- governed object;
- observable outcome;
- numerical, timing, permission, and failure constraints.

If two entries express the same obligation, keep one canonical requirement and merge supported detail and source references. Do not keep FR-037, FR-091, and FR-199 as separate entries merely because their wording differs. If entries overlap but impose different constraints, preserve both only when each has a distinct testable outcome; otherwise expose the conflict for human resolution.

## Registry rules

- Use stable `FR-NNN` and `NFR-NNN` identifiers.
- Keep one obligation per requirement.
- Preserve explicit scope, exclusions, priority, actors, thresholds, and failure behavior.
- Label unsupported detail as an assumption or open question; never promote it to fact.
- Update acceptance and downstream mappings whenever entries are merged, split, or replaced.
