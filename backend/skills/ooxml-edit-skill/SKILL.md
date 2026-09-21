---
name: ooxml-edit-skill
description: Perform deterministic low-level DOCX OOXML edits only when ordinary document APIs cannot preserve or express the required structure. Use for exact tables, relationships, content types, or package repairs; not for normal PRD prose editing.
---

# Deterministic DOCX OOXML Editing

Use the installed `documents` skill and high-level document APIs first. Drop to OOXML only for a concrete package-level requirement that those APIs cannot safely express.

## Boundaries

- Work on a copy in a dedicated temporary directory.
- Treat DOCX as an OPC ZIP package; preserve unknown parts and relationships.
- Parse XML with namespace-aware tools. Never edit XML with global string replacement.
- When adding or removing parts, update the owning `.rels` file and `[Content_Types].xml` together.
- Preserve relationship IDs, bookmarks, numbering, styles, section properties, and table grid data unless the requested change requires them to change.
- Reject path traversal and external relationship targets that were not explicitly requested.

## Verification

Repackage deterministically, reopen with a DOCX parser, render every page using the `documents` skill workflow, and inspect layout before delivery. Report any feature that could not be preserved instead of silently flattening it.
