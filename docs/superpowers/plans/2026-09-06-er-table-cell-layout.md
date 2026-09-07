# ER Table Cell Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep newly generated PRD ER diagram fields horizontally readable without rejecting an otherwise valid PRD for non-canonical table-row layout.

**Architecture:** At the Agent-output boundary, normalize field labels and recognized field-cell geometry into draw.io's renderable table layout. Layout variants that cannot cause unsafe XML remain acceptable; only existing XML safety and ER semantic-minimum validators may block persistence. Existing stored diagrams remain readable and are deliberately neither migrated nor repaired.

**Tech Stack:** Python 3.12, pytest, draw.io mxGraph XML, bundled drawio-skill Markdown instructions.

## Global Constraints

- Change only the generation contract for new ER diagrams; do not rewrite historical XML.
- Every ER field label must be owned by `shape=partialRectangle;html=1;whiteSpace=wrap;` child cell.
- A direct field label on a `tableRow` is moved to a renderable child cell before persistence.
- Every recognized field cell uses the row's absolute width and height; a `1×1` relative cell is visually clipped even though its text remains accessible.
- A row may be empty, may contain more than one recognized field cell, or may contain unrelated layout children without blocking the PRD.
- Preserve existing XML safety limits and relationship validation.

---

### Task 1: Normalize renderable field cells without treating layout variance as an error

**Files:**
- Modify: `backend/tests/unit/test_drawio_diagrams.py`
- Modify: `backend/tests/unit/test_output_repair.py`
- Modify: `backend/app/agents/output_validation.py`
- Modify: `backend/app/services/drawio_diagrams.py`

**Interfaces:**
- Consumes: `validate_agent_table_layout(drawio_xml: str) -> None` from the Agent-output validation flow.
- Produces: normalized Agent ER XML whose recognizable fields render horizontally; no layout-only `OutputConsistencyError`.

- [ ] **Step 1: Write the failing tests**

Keep the existing legacy row fixtures accepted by general XML validation. Add an Agent-output test containing one row with two child field cells and one unrelated layout child. It must validate successfully and every recognized child field cell must have the row's absolute width and height and no `relative` attribute.

```python
def test_agent_output_accepts_multiple_field_cells_and_normalizes_each_geometry(valid_spec):
    generated = valid_spec.model_copy(update={"er_diagrams": [diagram_with_two_field_cells()]})

    validate_node_output(generated, {"input_refs": generated.source_refs})

    xml = generated.er_diagrams[0].drawio_xml
    assert 'id="field-a"' in xml
    assert 'id="field-b"' in xml
    assert 'width="180" height="30"' in xml
    assert 'relative="1"' not in xml
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_drawio_diagrams.py -q`

Expected: FAIL with `ER_TABLE_FIELD_LAYOUT`, because the old Agent-output gate rejects a row that has more than one child.

- [ ] **Step 3: Implement the minimal Agent-output validator**

Keep `normalize_agent_table_layout` as the Agent-output boundary. It moves direct labels to `partialRectangle` children and fixes each recognized field cell's absolute geometry. Change `validate_agent_table_layout` so it checks only the renderability hazards that normalization cannot repair; it must not reject multiple field cells or unrelated layout children. Call it only from `validate_node_output` for PM generation/rewrite nodes after normal XML safety validation. Keep existing general XML, geometry, entity, edge, and parent-cycle checks unchanged so saved historical diagrams remain readable.

```python
if _is_table_row(cell):
    # The normalizer repairs labels and field geometry. Layout-only children
    # are intentionally tolerated so a usable PRD is not rejected.
    continue
```

- [ ] **Step 4: Run focused tests to verify green**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_drawio_diagrams.py tests/unit/test_output_repair.py -q`

Expected: PASS.

### Task 2: Keep generation instructions canonical while runtime stays tolerant

**Files:**
- Modify: `backend/skills/drawio-skill/references/diagram-types.md`
- Modify: `backend/prompts/nodes/pm_generate_spec.txt`
- Modify: `backend/prompts/nodes/pm_rewrite_prd.txt`
- Test: `backend/tests/unit/test_agent_gateway.py`

**Interfaces:**
- Consumes: the bundled `drawio-skill` copied only for `pm_generate_spec` and `pm_rewrite_prd` nodes.
- Produces: canonical ER XML when the Agent follows the preferred shape; runtime normalization remains the fallback.

- [ ] **Step 1: Write the failing prompt/skill assertion**

Extend the existing gateway/skill integrity test so it asserts both PM prompts name the mandatory row/cell structure and the ERD reference describes the same parent-child XML pattern.

```python
assert "shape=partialRectangle" in pm_generate_prompt
assert "tableRow" in pm_rewrite_prompt
assert "Each Row has exactly one Cell child" in diagram_types
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_agent_gateway.py -q`

Expected: FAIL because the current source only describes the row and allows its label to be placed there.

- [ ] **Step 3: Update the source-of-truth instructions**

Amend the ERD preset with an XML example: a blank `tableRow` child of the entity and exactly one `partialRectangle` cell child carrying the field's plain-text value. State that labels on rows are invalid. Add a short matching invariant to both PM prompts before their return-contract instructions.

```xml
<mxCell id="customer-id-row" value="" style="shape=tableRow;horizontal=0;..." vertex="1" parent="customer">
  <mxGeometry y="30" width="180" height="30" as="geometry" />
</mxCell>
<mxCell id="customer-id-cell" value="客户编号" style="shape=partialRectangle;html=1;whiteSpace=wrap;..." vertex="1" parent="customer-id-row">
  <mxGeometry width="180" height="30" as="geometry" />
</mxCell>
```

- [ ] **Step 4: Run the focused test to verify green**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_agent_gateway.py -q`

Expected: PASS.

### Task 3: Regenerate the current demo and verify output end-to-end

**Files:**
- Verify only: `backend/tests/unit/test_drawio_diagrams.py`
- Verify only: `backend/tests/unit/test_agent_gateway.py`
- Verify only: `frontend/aios-main/src/api/DrawioErDiagram.tsx`

- [ ] **Step 1: Run the draw.io and agent contract suites**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_drawio_diagrams.py tests/unit/test_output_repair.py tests/unit/test_agent_gateway.py -q`

Expected: PASS with no regressions in XML safety, Skill staging, or agent output validation.

- [ ] **Step 2: Regenerate the current customer/order/payment demo through normal PRD review**

After the validator and bundled skill are deployed to the local backend, use the existing formal PRD review flow to create a new version of the current customer/order/payment demo. Do not alter its historical versions or write to the database directly. Confirm the newest version's fields are horizontal after zoom, pan, and opening the XML in draw.io.

- [ ] **Step 3: Inspect working tree before handoff**

Run: `git diff --check` and `git diff -- backend/app/services/drawio_diagrams.py backend/skills/drawio-skill/references/diagram-types.md backend/prompts/nodes/pm_generate_spec.txt backend/prompts/nodes/pm_rewrite_prd.txt backend/tests/unit/test_drawio_diagrams.py backend/tests/unit/test_agent_gateway.py`.

Expected: no whitespace errors and no historical diagram payload changes.
