"""Safe handling for Agent-authored, uncompressed draw.io ER diagrams."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from xml.etree.ElementTree import Element, tostring

from defusedxml.ElementTree import fromstring

if TYPE_CHECKING:
    from app.domain.types import ErDiagram


MAX_XML_BYTES = 1_048_576
MAX_TOTAL_XML_BYTES = 2_097_152
MAX_PAGES = 4
MAX_ENTITIES = 150
MAX_RELATIONSHIPS = 300
MAX_ELEMENTS = 2_000
MAX_GEOMETRY_MAGNITUDE = 1_000_000
_FORBIDDEN_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_HTML_LABEL = re.compile(r"[<>]")
_ALLOWED_CHILDREN = {
    "mxfile": {"diagram"},
    "diagram": {"mxgraphmodel"},
    "mxgraphmodel": {"root"},
    "root": {"mxcell", "userobject"},
    "userobject": {"mxcell"},
    "mxcell": {"mxgeometry"},
    "mxgeometry": {"mxpoint", "mxrectangle", "array"},
    "array": {"mxpoint"},
    "mxpoint": set(),
    "mxrectangle": set(),
}
_ALLOWED_ATTRIBUTES = {
    "mxfile": {"host", "modified", "agent", "etag", "version", "type", "pages", "compressed"},
    "diagram": {"id", "name"},
    "mxgraphmodel": {
        "dx", "dy", "grid", "gridsize", "guides", "tooltips", "connect", "arrows",
        "fold", "page", "pagescale", "pagewidth", "pageheight", "math", "shadow",
        "background", "backgroundimage", "adaptivecolors", "style", "extfonts",
    },
    "root": set(),
    "userobject": {"id", "label", "tooltip", "tags"},
    "mxcell": {
        "id", "value", "style", "parent", "vertex", "edge", "source", "target",
        "connectable", "visible", "collapsed",
    },
    "mxgeometry": {"x", "y", "width", "height", "relative", "as"},
    "mxpoint": {"x", "y", "as"},
    "mxrectangle": {"x", "y", "width", "height", "as"},
    "array": {"as"},
}
_GEOMETRY_TAGS = {"mxgeometry", "mxpoint", "mxrectangle"}
_MODEL_NUMERIC_ATTRIBUTES = {
    "dx",
    "dy",
    "gridsize",
    "pagescale",
    "pagewidth",
    "pageheight",
}


class InvalidDrawioDiagram(ValueError):
    """Raised when a diagram cannot be safely rendered by the local viewer."""


@dataclass(frozen=True)
class DrawioDiagramSummary:
    page_count: int
    entity_count: int
    relationship_count: int


@dataclass(frozen=True)
class _EffectiveCell:
    cell_id: str
    label: str
    element: Any


def normalize_drawio_xml(drawio_xml: str) -> str:
    """Normalize text without rewriting meaningful mxGraph structure."""

    if not isinstance(drawio_xml, str):
        raise InvalidDrawioDiagram("draw.io XML must be text")
    normalized = unicodedata.normalize("NFC", drawio_xml).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise InvalidDrawioDiagram("draw.io XML must not be blank")
    if len(normalized.encode("utf-8")) > MAX_XML_BYTES:
        raise InvalidDrawioDiagram("draw.io XML exceeds the 1 MiB limit")
    return normalized


def validate_drawio_xml(drawio_xml: str) -> DrawioDiagramSummary:
    """Validate a bounded, uncompressed mxGraph ER diagram for local rendering."""

    normalized = normalize_drawio_xml(drawio_xml)
    declaration = _FORBIDDEN_DECLARATION.search(normalized)
    if declaration:
        raise InvalidDrawioDiagram(f"{declaration.group(0)[2:].strip()} declarations are not allowed")
    try:
        root = fromstring(normalized)
    except Exception as exc:
        raise InvalidDrawioDiagram("draw.io XML is malformed") from exc

    if root.tag == "mxGraphModel":
        graph_models = [root]
    elif root.tag == "mxfile":
        if root.get("compressed", "").strip().lower() not in {"", "false", "0"}:
            raise InvalidDrawioDiagram("draw.io XML must remain uncompressed")
        pages = list(root.findall("./diagram"))
        if not pages:
            raise InvalidDrawioDiagram("mxfile must contain at least one diagram page")
        if len(pages) > MAX_PAGES:
            raise InvalidDrawioDiagram(f"draw.io XML exceeds the {MAX_PAGES}-page limit")
        graph_models = []
        for page in pages:
            children = list(page)
            if len(children) != 1 or children[0].tag != "mxGraphModel":
                raise InvalidDrawioDiagram("diagram pages must use uncompressed mxGraphModel XML")
            graph_models.append(children[0])
    else:
        raise InvalidDrawioDiagram("draw.io XML root must be mxfile or mxGraphModel")

    _validate_drawio_structure(root)
    elements = list(root.iter())
    if len(elements) > MAX_ELEMENTS:
        raise InvalidDrawioDiagram(
            f"draw.io XML exceeds the {MAX_ELEMENTS}-element limit"
        )
    for element in elements:
        local_tag = element.tag.rsplit("}", 1)[-1].lower()
        if local_tag in {"script", "iframe", "object", "embed"}:
            raise InvalidDrawioDiagram(f"{local_tag} elements are not allowed")
        _validate_safe_attributes(local_tag, element.attrib)

    entity_count = 0
    edges = []
    for graph in graph_models:
        page_ids: set[str] = set()
        page_entity_ids: set[str] = set()
        page_edges: list[_EffectiveCell] = []
        _validate_reserved_root_cells(graph)
        page_cells = _effective_cells(graph)
        for effective in page_cells:
            cell = effective.element
            cell_id = effective.cell_id
            if cell_id in page_ids:
                raise InvalidDrawioDiagram(f"duplicate mxCell id: {cell_id}")
            page_ids.add(cell_id)
            if cell.get("vertex") == "1":
                is_entity = _is_entity_cell(cell)
                _require_geometry(
                    cell,
                    "entity" if is_entity else "vertex",
                    require_size=is_entity,
                )
                if is_entity:
                    page_entity_ids.add(cell_id)
            elif cell.get("edge") == "1":
                page_edges.append(effective)
                _require_geometry(cell, "relationship")
        _validate_cell_parents(page_cells, page_ids)
        for edge in page_edges:
            if (
                edge.element.get("source") not in page_entity_ids
                or edge.element.get("target") not in page_entity_ids
            ):
                raise InvalidDrawioDiagram("relationship endpoints must reference an existing entity")
        entity_count += len(page_entity_ids)
        edges.extend(page_edges)

    if entity_count < 2:
        raise InvalidDrawioDiagram("ER diagram must contain at least two entities")
    if entity_count > MAX_ENTITIES:
        raise InvalidDrawioDiagram(f"ER diagram exceeds the {MAX_ENTITIES}-entity limit")
    if not edges:
        raise InvalidDrawioDiagram("ER diagram must contain at least one relationship")
    if len(edges) > MAX_RELATIONSHIPS:
        raise InvalidDrawioDiagram(f"ER diagram exceeds the {MAX_RELATIONSHIPS}-relationship limit")
    return DrawioDiagramSummary(
        page_count=len(graph_models),
        entity_count=entity_count,
        relationship_count=len(edges),
    )


def normalize_agent_table_layout(drawio_xml: str) -> str:
    """Normalize Agent ER tables into draw.io's horizontally readable layout.

    Agent output occasionally uses a swimlane as an ER table or leaves spare
    rows in its geometry. Both forms render field labels in a narrow vertical
    column in the embedded viewer. This normalizer keeps the ER semantics while
    producing exactly one header row plus one visible row per field.
    """

    normalized = normalize_drawio_xml(drawio_xml)
    validate_drawio_xml(normalized)
    root = fromstring(normalized)
    graph_models = [root] if root.tag == "mxGraphModel" else [list(page)[0] for page in root.findall("./diagram")]
    changed = False
    for graph in graph_models:
        model_root = list(graph)[0]
        nodes = list(model_root)
        ids = {
            node.get("id")
            for node in nodes
            if node.tag.rsplit("}", 1)[-1].lower() == "mxcell" and node.get("id")
        }
        for row in nodes:
            if row.tag.rsplit("}", 1)[-1].lower() != "mxcell" or not _is_table_row(row):
                continue
            label = row.get("value", "").strip()
            if not label:
                continue
            row_id = row.get("id", "")
            if not row_id:
                continue
            row.set("value", "")
            cell_id = f"{row_id}-cell"
            suffix = 2
            while cell_id in ids:
                cell_id = f"{row_id}-cell-{suffix}"
                suffix += 1
            ids.add(cell_id)
            row_geometry = next(
                (child for child in list(row) if child.tag.rsplit("}", 1)[-1].lower() == "mxgeometry"),
                None,
            )
            cell = Element(
                "mxCell",
                {
                    "id": cell_id,
                    "value": label,
                    "style": "shape=partialRectangle;html=1;whiteSpace=wrap;connectable=0;strokeColor=inherit;overflow=hidden;fillColor=none;",
                    "vertex": "1",
                    "parent": row_id,
                },
            )
            geometry = Element(
                "mxGeometry",
                {
                    "width": row_geometry.get("width", "1") if row_geometry is not None else "1",
                    "height": row_geometry.get("height", "1") if row_geometry is not None else "1",
                    "as": "geometry",
                },
            )
            cell.append(geometry)
            model_root.append(cell)
            nodes.append(cell)
            changed = True
        entity_rows = {
            entity.get("id", ""): [
                row for row in nodes
                if row.get("parent") == entity.get("id", "") and _is_table_row(row)
            ]
            for entity in nodes
            if entity.get("vertex") == "1" and _is_entity_cell(entity)
        }
        for entity_id, rows in entity_rows.items():
            if not entity_id or not rows:
                continue
            entity = next((node for node in nodes if node.get("id") == entity_id), None)
            entity_geometry = _cell_geometry(entity) if entity is not None else None
            if entity is None or entity_geometry is None:
                continue
            field_rows: list[tuple[Element, Element]] = []
            for row in sorted(rows, key=_cell_y):
                row_id = row.get("id", "")
                if not row_id:
                    continue
                field_cells = [
                    cell for cell in nodes
                    if cell.get("parent") == row_id
                    and cell.get("vertex") == "1"
                    and cell.get("value", "").strip()
                ]
                if not field_cells:
                    for node in [row, *(cell for cell in nodes if cell.get("parent") == row_id)]:
                        if node in list(model_root):
                            model_root.remove(node)
                        if node in nodes:
                            nodes.remove(node)
                    changed = True
                    continue
                for index, cell in enumerate(field_cells):
                    target_row = row
                    if index:
                        target_row_id = _next_cell_id(f"{row_id}-field-{index + 1}-row", ids)
                        ids.add(target_row_id)
                        target_row = Element(
                            "mxCell",
                            {"id": target_row_id, "value": "", "vertex": "1", "parent": entity_id},
                        )
                        target_row.append(Element("mxGeometry", {"as": "geometry"}))
                        model_root.append(target_row)
                        nodes.append(target_row)
                        cell.set("parent", target_row_id)
                        changed = True
                    field_rows.append((target_row, cell))
            if not field_rows:
                continue
            table_width = entity_geometry.get("width", "180")
            y = 30.0
            for row, cell in field_rows:
                row_geometry = _cell_geometry(row)
                if row_geometry is None:
                    row_geometry = Element("mxGeometry", {"as": "geometry"})
                    row.append(row_geometry)
                height = _positive_geometry_value(row_geometry.get("height"), fallback=30.0)
                _update_geometry(row_geometry, y=y, width=table_width, height=height)
                y += height
                if _set_canonical_style(row, _ER_TABLE_ROW_STYLE):
                    changed = True
                if row.get("value"):
                    row.set("value", "")
                    changed = True
                cell_geometry = _cell_geometry(cell)
                if cell_geometry is None:
                    cell_geometry = Element("mxGeometry", {"as": "geometry"})
                    cell.append(cell_geometry)
                _update_geometry(cell_geometry, width=table_width, height=height, remove_relative=True)
                if _set_canonical_style(cell, _ER_TABLE_FIELD_STYLE):
                    changed = True
            _update_geometry(entity_geometry, height=y)
            if _set_canonical_style(entity, _ER_TABLE_STYLE, remove_keys={"horizontal"}):
                changed = True
    return tostring(root, encoding="unicode") if changed else normalized


_ER_TABLE_STYLE = {
    "shape": "table", "html": "1", "startSize": "30", "container": "1",
    "collapsible": "1", "childLayout": "tableLayout", "fixedRows": "1", "rowLines": "0",
}
_ER_TABLE_ROW_STYLE = {
    "shape": "tableRow", "html": "1", "horizontal": "0", "startSize": "0",
    "swimlaneHead": "0", "swimlaneBody": "0", "fillColor": "none", "collapsible": "0",
    "dropTarget": "0", "points": "[[0,0.5],[1,0.5]]", "portConstraint": "eastwest", "fontSize": "12",
}
_ER_TABLE_FIELD_STYLE = {
    "shape": "partialRectangle", "html": "1", "whiteSpace": "wrap", "connectable": "0",
    "overflow": "hidden", "fillColor": "none", "align": "left", "spacingLeft": "8",
}


def _cell_geometry(cell: Element | None) -> Element | None:
    if cell is None:
        return None
    return next(
        (child for child in list(cell) if child.tag.rsplit("}", 1)[-1].lower() == "mxgeometry"),
        None,
    )


def _cell_y(cell: Element) -> float:
    geometry = _cell_geometry(cell)
    return _positive_geometry_value(geometry.get("y") if geometry is not None else None, fallback=0.0)


def _positive_geometry_value(value: str | None, *, fallback: float) -> float:
    try:
        number = float(value) if value is not None else fallback
    except ValueError:
        return fallback
    return number if number > 0 else fallback


def _geometry_value(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _update_geometry(
    geometry: Element, *, y: float | None = None, width: str | None = None,
    height: float | None = None, remove_relative: bool = False,
) -> None:
    if y is not None:
        geometry.set("y", _geometry_value(y))
    if width is not None:
        geometry.set("width", width)
    if height is not None:
        geometry.set("height", _geometry_value(height))
    if remove_relative:
        geometry.attrib.pop("relative", None)


def _next_cell_id(base: str, ids: set[str | None]) -> str:
    candidate = base
    suffix = 2
    while candidate in ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _set_canonical_style(
    cell: Element, required: dict[str, str], *, remove_keys: set[str] | None = None,
) -> bool:
    remove_keys = remove_keys or set()
    parts = [part.strip() for part in cell.get("style", "").split(";") if part.strip()]
    updated: list[str] = []
    seen: set[str] = set()
    changed = False
    for part in parts:
        key = part.split("=", 1)[0]
        if key in remove_keys:
            changed = True
            continue
        if key in required:
            replacement = f"{key}={required[key]}"
            updated.append(replacement)
            seen.add(key)
            changed = changed or replacement != part
            continue
        updated.append(part)
    for key, value in required.items():
        if key not in seen:
            updated.append(f"{key}={value}")
            changed = True
    style = ";".join(updated) + ";"
    if style != cell.get("style", ""):
        cell.set("style", style)
        changed = True
    return changed


def diagram_anchor(diagram: ErDiagram) -> str:
    """Return the durable Markdown placeholder used by body and diff renderers."""

    normalized = normalize_drawio_xml(diagram.drawio_xml)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"firstflight-er-{diagram.diagram_id}-{digest}"


def diagram_entity_labels(drawio_xml: str) -> set[str]:
    """Return the plain-text labels of top-level table-shaped entity cells."""

    normalized = normalize_drawio_xml(drawio_xml)
    validate_drawio_xml(normalized)
    root = fromstring(normalized)
    labels: set[str] = set()
    graph_models = [root] if root.tag == "mxGraphModel" else [list(page)[0] for page in root.findall("./diagram")]
    for graph in graph_models:
        for effective in _effective_cells(graph):
            cell = effective.element
            if cell.get("vertex") == "1" and _is_entity_cell(cell) and effective.label:
                labels.add(effective.label)
    return labels


def diagram_review_projection(drawio_xml: str) -> dict[str, object]:
    """Return bounded semantics for review without sending raw XML to the model."""

    normalized = normalize_drawio_xml(drawio_xml)
    summary = validate_drawio_xml(normalized)
    root = fromstring(normalized)
    graph_models = [root] if root.tag == "mxGraphModel" else [list(page)[0] for page in root.findall("./diagram")]
    pages: list[dict[str, object]] = []
    for graph in graph_models:
        cells = _effective_cells(graph)
        entity_ids = {
            effective.cell_id
            for effective in cells
            if effective.element.get("vertex") == "1" and _is_entity_cell(effective.element)
        }
        fields_by_entity: dict[str, list[dict[str, str]]] = {cell_id: [] for cell_id in entity_ids}
        table_row_entities = {
            effective.cell_id: effective.element.get("parent", "")
            for effective in cells
            if (
                effective.element.get("vertex") == "1"
                and _is_table_row(effective.element)
                and effective.element.get("parent", "") in entity_ids
            )
        }
        for effective in cells:
            cell = effective.element
            parent_id = cell.get("parent", "")
            entity_id = parent_id if parent_id in fields_by_entity else table_row_entities.get(parent_id)
            if cell.get("vertex") == "1" and effective.cell_id not in entity_ids and entity_id:
                if _is_table_row(cell) and not effective.label:
                    continue
                fields_by_entity[entity_id].append(
                    {"id": effective.cell_id, "label": effective.label}
                )
        entities: list[dict[str, object]] = []
        relationships: list[dict[str, str]] = []
        for effective in cells:
            cell = effective.element
            if effective.cell_id in entity_ids:
                entities.append(
                    {
                        "id": effective.cell_id,
                        "label": effective.label,
                        "fields": fields_by_entity[effective.cell_id],
                    }
                )
            elif cell.get("edge") == "1":
                relationships.append(
                    {
                        "id": effective.cell_id,
                        "label": effective.label,
                        "source": cell.get("source", ""),
                        "target": cell.get("target", ""),
                    }
                )
        pages.append({"entities": entities, "relationships": relationships})
    return {
        "xml_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "page_count": summary.page_count,
        "entity_count": summary.entity_count,
        "relationship_count": summary.relationship_count,
        "pages": pages,
    }


def _is_entity_cell(cell) -> bool:
    style_parts = _style_parts(cell)
    return bool({"shape=table", "shape=swimlane", "swimlane"} & style_parts)


def _is_table_row(cell) -> bool:
    return "shape=tablerow" in _style_parts(cell)


def _is_table_cell(cell) -> bool:
    style_parts = _style_parts(cell)
    return {"shape=partialrectangle", "html=1", "whitespace=wrap"} <= style_parts


def _style_parts(cell) -> set[str]:
    return {
        part.strip().lower()
        for part in cell.get("style", "").split(";")
        if part.strip()
    }


def _validate_safe_attributes(element_tag: str, attributes: dict[str, str]) -> None:
    allowed_attributes = _ALLOWED_ATTRIBUTES[element_tag]
    for name, value in attributes.items():
        lowered_name = name.lower()
        lowered_value = value.lower()
        if lowered_name.startswith("on"):
            raise InvalidDrawioDiagram("event handler attributes are not allowed")
        if (
            lowered_name in {"link", "href", "xlink:href"}
            or lowered_name.endswith("}href")
        ) and value.strip():
            raise InvalidDrawioDiagram("link attributes are not allowed")
        if lowered_name in {"src", "image", "backgroundimage"} and value.strip():
            raise InvalidDrawioDiagram("image attributes are not allowed")
        if lowered_name == "extfonts" and value.strip():
            raise InvalidDrawioDiagram("external fonts are not allowed")
        if lowered_name == "math" and value.strip() not in {"", "0"}:
            raise InvalidDrawioDiagram("math rendering is not allowed in ER diagrams")
        if "javascript:" in lowered_value or "data:" in lowered_value:
            raise InvalidDrawioDiagram("script and data URLs are not allowed")
        if lowered_name == "style":
            if element_tag == "mxgraphmodel" and value.strip() not in {"", "default-style2"}:
                raise InvalidDrawioDiagram("external stylesheet selection is not allowed")
            if "\\" in value or "/*" in value or "*/" in value:
                raise InvalidDrawioDiagram("CSS escapes and comments are not allowed in styles")
            compact_style = re.sub(r"\s+", "", lowered_value)
            if "url(" in compact_style or "://" in compact_style:
                raise InvalidDrawioDiagram("external resource or image styles are not allowed")
            for style_part in value.split(";"):
                key, separator, raw_value = style_part.partition("=")
                if not separator or not raw_value.strip():
                    continue
                style_key = key.strip().lower()
                style_value = raw_value.strip().lower()
                if "image" in style_key:
                    raise InvalidDrawioDiagram("image styles are not allowed")
                if style_key == "fontsource":
                    raise InvalidDrawioDiagram("external font styles are not allowed")
                if style_value.startswith("mxgraph.") or style_value.startswith("stencil("):
                    raise InvalidDrawioDiagram("external stencil styles are not allowed")
        if lowered_name in {"value", "label", "tooltip"} and _HTML_LABEL.search(value):
            raise InvalidDrawioDiagram("diagram labels must be plain text")
        if lowered_name not in allowed_attributes:
            raise InvalidDrawioDiagram(
                f"{name} attributes are not allowed on {element_tag}"
            )
    _validate_numeric_attributes(element_tag, attributes)


def _validate_numeric_attributes(element_tag: str, attributes: dict[str, str]) -> None:
    numeric_attributes: set[str]
    if element_tag in _GEOMETRY_TAGS:
        numeric_attributes = {"x", "y", "width", "height"}
    elif element_tag == "mxgraphmodel":
        numeric_attributes = _MODEL_NUMERIC_ATTRIBUTES
    else:
        return

    for name in numeric_attributes:
        raw_value = attributes.get(name)
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise InvalidDrawioDiagram(
                f"{element_tag} {name} must be a numeric geometry value"
            ) from exc
        if not math.isfinite(value) or abs(value) > MAX_GEOMETRY_MAGNITUDE:
            raise InvalidDrawioDiagram(
                f"{element_tag} {name} numeric geometry value is out of bounds"
            )
        if name in {"width", "height", "gridsize", "pagescale", "pagewidth", "pageheight"} and value <= 0:
            raise InvalidDrawioDiagram(
                f"{element_tag} {name} dimension must be positive"
            )


def _validate_drawio_structure(element) -> None:
    element_tag = element.tag.rsplit("}", 1)[-1].lower()
    allowed_children = _ALLOWED_CHILDREN.get(element_tag)
    if allowed_children is None:
        raise InvalidDrawioDiagram(f"{element_tag} elements are not allowed")

    children = list(element)
    for child in children:
        child_tag = child.tag.rsplit("}", 1)[-1].lower()
        if child_tag not in allowed_children:
            raise InvalidDrawioDiagram(
                f"{child_tag} elements are not allowed inside {element_tag}"
            )
        _validate_drawio_structure(child)

    if element_tag == "mxgraphmodel" and (
        len(children) != 1
        or children[0].tag.rsplit("}", 1)[-1].lower() != "root"
    ):
        raise InvalidDrawioDiagram("mxGraphModel must contain exactly one root element")
    if element_tag == "userobject" and (
        len(children) != 1
        or children[0].tag.rsplit("}", 1)[-1].lower() != "mxcell"
    ):
        raise InvalidDrawioDiagram("UserObject must contain exactly one mxCell")


def _effective_cells(graph) -> list[_EffectiveCell]:
    graph_children = list(graph)
    if len(graph_children) != 1:
        raise InvalidDrawioDiagram("mxGraphModel must contain exactly one root element")
    effective_cells: list[_EffectiveCell] = []
    for node in list(graph_children[0]):
        node_tag = node.tag.rsplit("}", 1)[-1].lower()
        if node_tag == "mxcell":
            cell_id = node.get("id")
            if not cell_id:
                raise InvalidDrawioDiagram("every top-level mxCell must have an id")
            effective_cells.append(
                _EffectiveCell(cell_id=cell_id, label=node.get("value", "").strip(), element=node)
            )
            continue

        wrapper_id = node.get("id")
        if not wrapper_id:
            raise InvalidDrawioDiagram("every UserObject wrapper must have an id")
        cell = list(node)[0]
        inner_id = cell.get("id")
        if inner_id and inner_id != wrapper_id:
            raise InvalidDrawioDiagram("UserObject inner mxCell id must match its wrapper id")
        effective_cells.append(
            _EffectiveCell(
                cell_id=wrapper_id,
                label=node.get("label", cell.get("value", "")).strip(),
                element=cell,
            )
        )
    return effective_cells


def _validate_reserved_root_cells(graph) -> None:
    graph_children = list(graph)
    if len(graph_children) != 1:
        raise InvalidDrawioDiagram("mxGraphModel must contain exactly one root element")

    reserved: dict[str, Any] = {}
    for node in list(graph_children[0]):
        node_id = node.get("id")
        if node_id not in {"0", "1"}:
            continue
        if node.tag.rsplit("}", 1)[-1].lower() != "mxcell":
            raise InvalidDrawioDiagram("reserved root and layer ids must use plain mxCell elements")
        if node_id in reserved:
            raise InvalidDrawioDiagram(f"duplicate mxCell id: {node_id}")
        reserved[node_id] = node

    if set(reserved) != {"0", "1"}:
        raise InvalidDrawioDiagram("reserved root mxCell 0 and default layer mxCell 1 are required")

    model_root = reserved["0"]
    default_layer = reserved["1"]
    if (
        model_root.get("parent") is not None
        or model_root.get("vertex") is not None
        or model_root.get("edge") is not None
        or list(model_root)
    ):
        raise InvalidDrawioDiagram("reserved root mxCell 0 must be an unparented plain cell")
    if (
        default_layer.get("parent") != "0"
        or default_layer.get("vertex") is not None
        or default_layer.get("edge") is not None
        or list(default_layer)
    ):
        raise InvalidDrawioDiagram("reserved default layer mxCell 1 must be a plain child of root 0")


def _validate_cell_parents(cells: list[_EffectiveCell], cell_ids: set[str]) -> None:
    parent_by_id: dict[str, str] = {}
    for effective in cells:
        parent_id = effective.element.get("parent", "").strip()
        if not parent_id:
            continue
        if parent_id not in cell_ids:
            raise InvalidDrawioDiagram(
                f"mxCell parent must reference an existing cell: {parent_id}"
            )
        parent_by_id[effective.cell_id] = parent_id

    # Iterative three-colour traversal keeps adversarial-but-bounded parent
    # chains from escaping as Python RecursionError.
    state: dict[str, int] = {}
    for start_id in cell_ids:
        if state.get(start_id) == 2:
            continue
        path: list[str] = []
        cell_id: str | None = start_id
        while cell_id is not None and state.get(cell_id, 0) == 0:
            state[cell_id] = 1
            path.append(cell_id)
            cell_id = parent_by_id.get(cell_id)
        if cell_id is not None and state.get(cell_id) == 1:
            raise InvalidDrawioDiagram("mxCell parent cycle is not allowed")
        for path_id in path:
            state[path_id] = 2


def _require_geometry(cell, cell_kind: str, *, require_size: bool = False) -> None:
    geometries = [
        child
        for child in list(cell)
        if child.tag.rsplit("}", 1)[-1].lower() == "mxgeometry"
    ]
    if len(geometries) != 1 or geometries[0].get("as") != "geometry":
        raise InvalidDrawioDiagram(f"every {cell_kind} must have one mxGeometry")
    if require_size and (
        geometries[0].get("width") is None or geometries[0].get("height") is None
    ):
        raise InvalidDrawioDiagram("every entity geometry must include width and height")
