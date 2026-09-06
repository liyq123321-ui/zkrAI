import json

import pytest
from pydantic import ValidationError

from app.domain.types import ErDiagram, ProjectSpecPayload
from app.agents.output_validation import OutputConsistencyError, validate_node_output
from app.services.drawio_diagrams import (
    InvalidDrawioDiagram,
    diagram_anchor,
    diagram_entity_labels,
    diagram_review_projection,
    normalize_agent_table_layout,
    validate_drawio_xml,
)
from app.services.spec_service import SpecService, _render_markdown
from tests.helpers.factories import make_valid_spec


VALID_ER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="drawio" version="31.4.2">
  <diagram name="Data model" id="page-1">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="customer" value="Customer" style="shape=table;html=1;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="180" height="100" as="geometry" />
        </mxCell>
        <mxCell id="order" value="Order" style="shape=table;html=1;" vertex="1" parent="1">
          <mxGeometry x="260" y="0" width="180" height="100" as="geometry" />
        </mxCell>
        <mxCell id="customer-order" value="places" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="customer" target="order">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def _diagram(**overrides: str) -> dict[str, str]:
    return {
        "diagram_id": "data-model",
        "title": "Core data model",
        "after_section": "core_objects",
        "drawio_xml": VALID_ER_XML,
        **overrides,
    }


def test_valid_uncompressed_er_xml_allows_drawio_html_style_flag():
    summary = validate_drawio_xml(VALID_ER_XML)

    assert summary.page_count == 1
    assert summary.entity_count == 2
    assert summary.relationship_count == 1


def test_standard_userobject_uses_wrapper_id_and_label_for_relationships():
    wrapped = VALID_ER_XML.replace(
        '<mxCell id="customer" value="Customer"',
        '<UserObject id="customer" label="Customer"><mxCell',
    ).replace(
        '</mxCell>\n        <mxCell id="order"',
        '</mxCell></UserObject>\n        <mxCell id="order"',
        1,
    )

    summary = validate_drawio_xml(wrapped)

    assert summary.entity_count == 2
    assert diagram_entity_labels(wrapped) == {"Customer", "Order"}


def test_userobject_inner_cell_id_must_match_wrapper_when_present():
    conflicting = VALID_ER_XML.replace(
        '<mxCell id="customer" value="Customer"',
        '<UserObject id="customer" label="Customer"><mxCell id="inner-customer"',
    ).replace(
        '</mxCell>\n        <mxCell id="order"',
        '</mxCell></UserObject>\n        <mxCell id="order"',
        1,
    )

    with pytest.raises(InvalidDrawioDiagram, match="wrapper"):
        validate_drawio_xml(conflicting)


def test_table_rows_are_fields_not_entities_in_validation_and_review_projection():
    rows = (
        '<mxCell id="customer-id" value="PK customer_id" style="shape=tableRow;html=1;" vertex="1" parent="customer">'
        '<mxGeometry y="30" width="180" height="30" as="geometry" /></mxCell>'
        '<mxCell id="order-id" value="PK order_id" style="shape=tableRow;html=1;" vertex="1" parent="order">'
        '<mxGeometry y="30" width="180" height="30" as="geometry" /></mxCell>'
    )
    with_fields = VALID_ER_XML.replace("</root>", f"{rows}</root>")

    summary = validate_drawio_xml(with_fields)
    projection = diagram_review_projection(with_fields)

    assert summary.entity_count == 2
    assert projection["entity_count"] == 2
    assert projection["pages"][0]["entities"] == [
        {"id": "customer", "label": "Customer", "fields": [{"id": "customer-id", "label": "PK customer_id"}]},
        {"id": "order", "label": "Order", "fields": [{"id": "order-id", "label": "PK order_id"}]},
    ]


def test_review_projection_reads_fields_from_horizontal_table_cells():
    rows = (
        '<mxCell id="customer-id-row" value="" style="shape=tableRow;html=1;" vertex="1" parent="customer">'
        '<mxGeometry y="30" width="180" height="30" as="geometry" /></mxCell>'
        '<mxCell id="customer-id" value="PK customer_id" '
        'style="shape=partialRectangle;html=1;whiteSpace=wrap;" vertex="1" parent="customer-id-row">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry" /></mxCell>'
        '<mxCell id="order-id-row" value="" style="shape=tableRow;html=1;" vertex="1" parent="order">'
        '<mxGeometry y="30" width="180" height="30" as="geometry" /></mxCell>'
        '<mxCell id="order-id" value="PK order_id" '
        'style="shape=partialRectangle;html=1;whiteSpace=wrap;" vertex="1" parent="order-id-row">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry" /></mxCell>'
    )

    projection = diagram_review_projection(VALID_ER_XML.replace("</root>", f"{rows}</root>"))

    assert projection["pages"][0]["entities"] == [
        {"id": "customer", "label": "Customer", "fields": [{"id": "customer-id", "label": "PK customer_id"}]},
        {"id": "order", "label": "Order", "fields": [{"id": "order-id", "label": "PK order_id"}]},
    ]


def test_normalization_converts_swimlane_er_entities_to_exact_header_and_field_rows():
    fields = "".join(
        f'<mxCell id="customer-row-{index}" value="" style="shape=tableRow;html=1;" vertex="1" parent="customer">'
        f'<mxGeometry y="{index * 30}" width="180" height="30" as="geometry" /></mxCell>'
        f'<mxCell id="customer-field-{index}" value="{label}" style="shape=partialRectangle;html=1;" '
        f'vertex="1" parent="customer-row-{index}">'
        '<mxGeometry width="1" height="1" relative="1" as="geometry" /></mxCell>'
        for index, label in enumerate(("Customer ID", "Customer name", "Customer status"), start=1)
    )
    malformed_layout = VALID_ER_XML.replace(
        'style="shape=table;html=1;" vertex="1" parent="1">\n          <mxGeometry x="0" y="0" width="180" height="100"',
        'style="shape=swimlane;html=1;horizontal=0;startSize=30;" vertex="1" parent="1">\n          <mxGeometry x="0" y="0" width="180" height="150"',
        1,
    ).replace("</root>", f"{fields}</root>")

    normalized = normalize_agent_table_layout(malformed_layout)

    assert 'id="customer" value="Customer" style="shape=table;' in normalized
    assert 'id="customer" value="Customer" style="shape=table;html=1;startSize=30;' in normalized
    assert '<mxGeometry x="0" y="0" width="180" height="120" as="geometry"' in normalized
    assert normalized.count('parent="customer"') == 3
    for index in range(1, 4):
        row = normalized.split(f'id="customer-row-{index}"', 1)[1].split("</mxCell>", 1)[0]
        field = normalized.split(f'id="customer-field-{index}"', 1)[1].split("</mxCell>", 1)[0]
        assert f'y="{index * 30}" width="180" height="30"' in row
        assert 'whiteSpace=wrap' in field
        assert 'width="180" height="30"' in field
        assert 'relative="1"' not in field


def test_many_table_rows_do_not_consume_the_entity_limit():
    rows = "".join(
        f'<mxCell id="field-{index}" value="field {index}" style="shape=tableRow;html=1;" vertex="1" parent="customer">'
        f'<mxGeometry y="{index * 20}" width="180" height="20" as="geometry" /></mxCell>'
        for index in range(149)
    )

    summary = validate_drawio_xml(VALID_ER_XML.replace("</root>", f"{rows}</root>"))

    assert summary.entity_count == 2


def test_relationship_endpoints_must_be_entity_containers_not_table_rows():
    row = (
        '<mxCell id="customer-id" value="PK customer_id" style="shape=tableRow;html=1;" vertex="1" parent="customer">'
        '<mxGeometry y="30" width="180" height="30" as="geometry" /></mxCell>'
    )
    targets_field = VALID_ER_XML.replace("</root>", f"{row}</root>").replace(
        'target="order"', 'target="customer-id"'
    )

    with pytest.raises(InvalidDrawioDiagram, match="existing entity"):
        validate_drawio_xml(targets_field)


@pytest.mark.parametrize(
    ("unsafe_xml", "message"),
    [
        (
            VALID_ER_XML.replace(
                '<mxfile host="drawio" version="31.4.2">',
                '<!DOCTYPE mxfile [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><mxfile host="drawio" version="31.4.2">',
            ),
            "DOCTYPE",
        ),
        (VALID_ER_XML.replace('value="Customer"', 'value="&lt;b&gt;Customer&lt;/b&gt;"'), "plain text"),
        (
            VALID_ER_XML.replace("shape=table;html=1;", "shape=table;html=1;image=https://example.com/a.png;", 1),
            "image",
        ),
        (
            VALID_ER_XML.replace(
                "shape=table;html=1;",
                "shape=table;html=1;fontSource=https%3A%2F%2Fevil.example%2Ffont.woff;",
                1,
            ),
            "external font",
        ),
        (
            VALID_ER_XML.replace("shape=table;html=1;", "shape=mxgraph.aws4.lambda_function;html=1;", 1),
            "external stencil",
        ),
        (
            VALID_ER_XML.replace(
                "shape=table;html=1;",
                "shape=table;html=1;indicatorShape=mxgraph.aws4.lambda_function;",
                1,
            ),
            "external stencil",
        ),
        (
            VALID_ER_XML.replace(
                "shape=table;html=1;",
                "shape=table;html=1;fillColor=url(https://evil.example/fill.svg);",
                1,
            ),
            "external resource",
        ),
        (
            VALID_ER_XML.replace(
                "shape=table;html=1;",
                r"shape=table;html=1;fillColor=\75\72\6c(\68\74\74\70\73\3a\2f\2f evil.example/fill.svg#x);",
                1,
            ),
            "CSS escapes",
        ),
        (
            VALID_ER_XML.replace(
                "shape=table;html=1;",
                "shape=table;html=1;indicatorImage=https://evil.example/pixel.png;",
                1,
            ),
            "image",
        ),
        (VALID_ER_XML.replace("<mxGraphModel>", '<mxGraphModel math="1">'), "math"),
        (
            VALID_ER_XML.replace(
                "<mxGraphModel>",
                '<mxGraphModel extFonts="Evil^https://evil.example/font.woff2">',
            ),
            "external fonts",
        ),
        (
            VALID_ER_XML.replace("<mxGraphModel>", '<mxGraphModel style="evil-theme">'),
            "stylesheet",
        ),
        (
            VALID_ER_XML.replace(
                "</root>",
                '</root><include name="https://evil.example/payload.xml" />',
            ),
            "include",
        ),
        (
            VALID_ER_XML.replace(
                '<mxfile host="drawio" version="31.4.2">',
                '<mxfile host="drawio" version="31.4.2" vars="{&quot;evil&quot;:&quot;&lt;img src=https://evil.example/pixel.png&gt;&quot;}">',
            ),
            "vars",
        ),
        (
            VALID_ER_XML.replace(
                '<mxCell id="customer" value="Customer"',
                '<UserObject label="%evil%" placeholders="1">'
                '<mxCell id="customer" value="Customer"',
            ).replace(
                '</mxCell>\n        <mxCell id="order"',
                '</mxCell></UserObject>\n        <mxCell id="order"',
                1,
            ),
            "placeholders",
        ),
        (
            VALID_ER_XML.replace(
                '<mxCell id="customer" value="Customer"',
                '<UserObject note="&lt;img src=https://evil.example/pixel.png&gt;">'
                '<mxCell id="customer" value="Customer"',
            ).replace(
                '</mxCell>\n        <mxCell id="order"',
                '</mxCell></UserObject>\n        <mxCell id="order"',
                1,
            ),
            "note",
        ),
        (
            VALID_ER_XML.replace(
                "<root>",
                '<root><mxImage src="https://example.com/tracker.png" />',
            ),
            "image",
        ),
        (
            VALID_ER_XML.replace(
                '<mxCell id="customer" value="Customer"',
                '<UserObject label="&lt;img src=https://example.com/tracker.png&gt;">'
                '<mxCell id="customer" value="Customer"',
            ).replace(
                '</mxCell>\n        <mxCell id="order"',
                '</mxCell></UserObject>\n        <mxCell id="order"',
                1,
            ),
            "plain text",
        ),
        (VALID_ER_XML.replace('vertex="1" parent="1"', 'vertex="1" parent="1" onclick="alert(1)"', 1), "event"),
        (VALID_ER_XML.replace('id="customer" value=', 'id="customer" link="javascript:alert(1)" value='), "link"),
        (VALID_ER_XML.replace("<root>", "<root><script>alert(1)</script>"), "script"),
    ],
)
def test_unsafe_drawio_xml_is_rejected(unsafe_xml: str, message: str):
    with pytest.raises(InvalidDrawioDiagram, match=message):
        validate_drawio_xml(unsafe_xml)


def test_compressed_drawio_page_is_rejected():
    compressed = '<mxfile><diagram name="Data model">7V1bc+I4FP41PCbD...</diagram></mxfile>'

    with pytest.raises(InvalidDrawioDiagram, match="uncompressed"):
        validate_drawio_xml(compressed)


def test_truthy_compressed_flag_is_rejected_before_external_editing():
    compressed_on_save = VALID_ER_XML.replace(
        '<mxfile host="drawio" version="31.4.2">',
        '<mxfile host="drawio" version="31.4.2" compressed="true">',
    )

    with pytest.raises(InvalidDrawioDiagram, match="uncompressed"):
        validate_drawio_xml(compressed_on_save)


def test_edges_must_reference_existing_entities():
    dangling = VALID_ER_XML.replace('target="order"', 'target="missing"')

    with pytest.raises(InvalidDrawioDiagram, match="existing entity"):
        validate_drawio_xml(dangling)


def test_cell_ids_must_be_unique():
    duplicate = VALID_ER_XML.replace('id="order"', 'id="customer"')

    with pytest.raises(InvalidDrawioDiagram, match="duplicate"):
        validate_drawio_xml(duplicate)


@pytest.mark.parametrize(
    "reserved_xml",
    [
        VALID_ER_XML.replace('<mxCell id="0" />', '<mxCell id="0" vertex="1"><mxGeometry width="10" height="10" as="geometry" /></mxCell>'),
        VALID_ER_XML.replace('<mxCell id="1" parent="0" />', '<mxCell id="1" parent="0" edge="1"><mxGeometry relative="1" as="geometry" /></mxCell>'),
        VALID_ER_XML.replace('<mxCell id="0" />', '<mxCell id="0" parent="1" />'),
        VALID_ER_XML.replace('<mxCell id="1" parent="0" />', '<mxCell id="1" />'),
    ],
)
def test_reserved_root_cells_keep_drawio_model_semantics(reserved_xml: str):
    with pytest.raises(InvalidDrawioDiagram, match="reserved|root|layer"):
        validate_drawio_xml(reserved_xml)


def test_cell_parent_must_reference_an_existing_cell():
    missing_parent = VALID_ER_XML.replace('vertex="1" parent="1"', 'vertex="1" parent="missing"', 1)

    with pytest.raises(InvalidDrawioDiagram, match="parent"):
        validate_drawio_xml(missing_parent)


def test_cell_cannot_parent_itself():
    self_parent = VALID_ER_XML.replace('vertex="1" parent="1"', 'vertex="1" parent="customer"', 1)

    with pytest.raises(InvalidDrawioDiagram, match="parent"):
        validate_drawio_xml(self_parent)


def test_cell_parent_chain_cannot_form_a_cycle():
    cyclic = VALID_ER_XML.replace('id="customer" value="Customer"', 'id="customer" value="Customer"')
    cyclic = cyclic.replace('vertex="1" parent="1"', 'vertex="1" parent="order"', 1)
    cyclic = cyclic.replace('vertex="1" parent="1"', 'vertex="1" parent="customer"', 1)

    with pytest.raises(InvalidDrawioDiagram, match="parent cycle"):
        validate_drawio_xml(cyclic)


def test_long_cell_parent_chain_is_validated_without_python_recursion():
    chain = "".join(
        f'<mxCell id="group-{index}" parent="{("1" if index == 0 else f"group-{index - 1}")}" />'
        for index in range(1_100)
    )

    summary = validate_drawio_xml(VALID_ER_XML.replace("</root>", f"{chain}</root>"))

    assert summary.entity_count == 2


def test_userobject_inner_cell_parent_is_validated():
    wrapped = VALID_ER_XML.replace(
        '<mxCell id="customer" value="Customer"',
        '<UserObject id="customer" label="Customer"><mxCell',
    ).replace(
        '</mxCell>\n        <mxCell id="order"',
        '</mxCell></UserObject>\n        <mxCell id="order"',
        1,
    ).replace('vertex="1" parent="1"', 'vertex="1" parent="missing"', 1)

    with pytest.raises(InvalidDrawioDiagram, match="parent"):
        validate_drawio_xml(wrapped)


@pytest.mark.parametrize(
    "invalid_xml",
    [
        VALID_ER_XML.replace('width="180"', 'width="NaN"', 1),
        VALID_ER_XML.replace('width="180"', 'width="Infinity"', 1),
        VALID_ER_XML.replace('width="180"', 'width="-10"', 1),
        VALID_ER_XML.replace('x="0"', 'x="1e309"', 1),
        VALID_ER_XML.replace(
            '<mxGeometry relative="1" as="geometry" />',
            '<mxGeometry relative="1" as="geometry"><mxPoint x="Infinity" y="0" as="sourcePoint" /></mxGeometry>',
        ),
        VALID_ER_XML.replace(
            '<mxGeometry x="0" y="0" width="180" height="100" as="geometry" />',
            '<mxGeometry x="0" y="0" width="180" height="100" as="geometry">'
            '<mxRectangle x="0" y="0" width="-1" height="100" as="alternateBounds" /></mxGeometry>',
        ),
    ],
)
def test_geometry_values_must_be_finite_bounded_and_positive_sized(invalid_xml: str):
    with pytest.raises(InvalidDrawioDiagram, match="numeric|dimension|geometry"):
        validate_drawio_xml(invalid_xml)


def test_entity_geometry_requires_width_and_height():
    missing_size = VALID_ER_XML.replace(' width="180" height="100"', '', 1)

    with pytest.raises(InvalidDrawioDiagram, match="width and height"):
        validate_drawio_xml(missing_size)


def test_project_spec_accepts_legacy_payload_without_er_diagrams():
    spec = make_valid_spec()

    assert spec.er_diagrams == []


def test_project_spec_rejects_duplicate_diagram_ids():
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [_diagram(), _diagram(title="Operational data model")]

    with pytest.raises(ValidationError, match="diagram_id"):
        ProjectSpecPayload.model_validate(payload)


def test_project_spec_rejects_duplicate_diagram_titles_case_insensitively():
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [
        _diagram(),
        _diagram(diagram_id="operational-model", title=" core DATA model "),
    ]

    with pytest.raises(ValidationError, match="titles"):
        ProjectSpecPayload.model_validate(payload)


def test_er_diagram_rejects_unknown_placement():
    with pytest.raises(ValidationError, match="after_section"):
        ErDiagram.model_validate(_diagram(after_section="risks"))


def test_markdown_inserts_stable_diagram_anchor_after_target_section():
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [_diagram()]

    markdown = _render_markdown(payload)
    anchor = diagram_anchor(ErDiagram.model_validate(_diagram()))

    link = f"[ER 图：Core data model](#{anchor})"
    assert markdown.index("## Core Objects") < markdown.index(link) < markdown.index("## Main Flows")
    assert VALID_ER_XML not in markdown


def test_markdown_escapes_diagram_title_without_splitting_the_anchor():
    title = "Orders](https://evil.example) [model"
    diagram = ErDiagram.model_validate(_diagram(title=title))
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [diagram.model_dump(mode="json")]

    markdown = _render_markdown(payload)

    assert (
        rf"[ER 图：Orders\](https://evil.example) \[model](#{diagram_anchor(diagram)})"
        in markdown
    )
    assert "Orders](https://evil.example)" not in markdown


def test_er_diagram_title_rejects_line_breaks():
    with pytest.raises(ValidationError, match="line breaks"):
        ErDiagram.model_validate(_diagram(title="Core\nmodel"))


def test_diagram_anchor_hash_changes_when_xml_changes():
    original = ErDiagram.model_validate(_diagram())
    revised = ErDiagram.model_validate(
        _diagram(drawio_xml=VALID_ER_XML.replace('value="Order"', 'value="PurchaseOrder"'))
    )

    assert diagram_anchor(original) != diagram_anchor(revised)


def test_er_diagrams_are_limited_to_eight_per_spec():
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [
        _diagram(diagram_id=f"model-{index}", title=f"Model {index}") for index in range(9)
    ]

    with pytest.raises(ValidationError, match="8 items"):
        ProjectSpecPayload.model_validate(payload)


def test_combined_diagram_xml_has_a_project_level_limit():
    padded_xml = VALID_ER_XML.replace(
        '<diagram name="Data model"',
        " " * 710_000 + '<diagram name="Data model"',
        1,
    )
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [
        _diagram(diagram_id=f"model-{index}", title=f"Model {index}", drawio_xml=padded_xml)
        for index in range(3)
    ]

    with pytest.raises(ValidationError, match="combined"):
        ProjectSpecPayload.model_validate(payload)


def test_reviewer_payload_uses_compact_diagram_semantics_in_current_and_parent_spec():
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [_diagram()]
    artifact_collision = {
        **_diagram(),
        "description": "This is source evidence, not a Project Spec diagram object.",
    }

    review_payload = SpecService._spec_reviewer_payload(
        payload,
        "spec-hash",
        ["artifact:brief-1"],
        {"parent_spec": payload, "artifacts": [{"content": artifact_collision}]},
    )
    projection = review_payload["spec"]["er_diagrams"][0]

    assert "drawio_xml" not in json.dumps(review_payload["spec"], ensure_ascii=False)
    assert "mxGraphModel" not in json.dumps(review_payload["spec"], ensure_ascii=False)
    assert "drawio_xml" not in json.dumps(
        review_payload["generation_source_snapshot"]["parent_spec"], ensure_ascii=False
    )
    assert projection["diagram_id"] == "data-model"
    assert projection["semantic_projection"]["pages"][0]["entities"] == [
        {"id": "customer", "label": "Customer", "fields": []},
        {"id": "order", "label": "Order", "fields": []},
    ]
    assert projection["semantic_projection"]["pages"][0]["relationships"] == [
        {"id": "customer-order", "label": "places", "source": "customer", "target": "order"}
    ]
    assert review_payload["generation_source_snapshot"]["artifacts"][0]["content"] == artifact_collision


def test_drawio_xml_is_limited_to_four_pages():
    graph = VALID_ER_XML.split('<diagram name="Data model" id="page-1">', 1)[1].split("</diagram>", 1)[0]
    pages = "".join(f'<diagram name="Page {index}" id="page-{index}">{graph}</diagram>' for index in range(5))
    oversized = f'<mxfile host="drawio">{pages}</mxfile>'

    with pytest.raises(InvalidDrawioDiagram, match="4-page"):
        validate_drawio_xml(oversized)


def test_four_drawio_pages_may_reuse_page_local_cell_ids():
    graph = VALID_ER_XML.split('<diagram name="Data model" id="page-1">', 1)[1].split("</diagram>", 1)[0]
    pages = "".join(f'<diagram name="Page {index}" id="page-{index}">{graph}</diagram>' for index in range(4))

    summary = validate_drawio_xml(f'<mxfile host="drawio">{pages}</mxfile>')

    assert summary.page_count == 4
    assert summary.entity_count == 8
    assert summary.relationship_count == 4


def test_drawio_xml_is_limited_to_one_mebibyte():
    oversized = VALID_ER_XML.replace('value="Customer"', f'value="Customer{"x" * 1_048_576}"')

    with pytest.raises(InvalidDrawioDiagram, match="1 MiB"):
        validate_drawio_xml(oversized)


def test_drawio_xml_is_limited_to_150_entities():
    extra_entities = "".join(
        f'<mxCell id="extra-{index}" value="Extra {index}" style="shape=table;html=1;" vertex="1" parent="1">'
        f'<mxGeometry x="0" y="{index * 20}" width="180" height="20" as="geometry" />'
        "</mxCell>"
        for index in range(149)
    )
    oversized = VALID_ER_XML.replace("</root>", f"{extra_entities}</root>")

    with pytest.raises(InvalidDrawioDiagram, match="150-entity"):
        validate_drawio_xml(oversized)


def test_drawio_xml_is_limited_to_300_relationships():
    extra_edges = "".join(
        f'<mxCell id="edge-{index}" value="relates" edge="1" parent="1" source="customer" target="order">'
        '<mxGeometry relative="1" as="geometry" />'
        "</mxCell>"
        for index in range(300)
    )
    oversized = VALID_ER_XML.replace("</root>", f"{extra_edges}</root>")

    with pytest.raises(InvalidDrawioDiagram, match="300-relationship"):
        validate_drawio_xml(oversized)


def test_drawio_xml_is_limited_to_a_bounded_total_element_count():
    inert_cells = ''.join(f'<mxCell id="inert-{index}" />' for index in range(2_000))
    oversized = VALID_ER_XML.replace("</root>", f"{inert_cells}</root>")

    with pytest.raises(InvalidDrawioDiagram, match="element"):
        validate_drawio_xml(oversized)


def test_agent_output_rejects_entity_names_absent_from_core_objects():
    payload = make_valid_spec().model_dump(mode="json")
    payload["er_diagrams"] = [_diagram()]
    spec = ProjectSpecPayload.model_validate(payload)

    with pytest.raises(OutputConsistencyError, match="ER_ENTITY_NOT_IN_CORE_OBJECTS"):
        validate_node_output(spec, {"input_refs": spec.source_refs})


def test_agent_output_accepts_entity_names_declared_as_core_objects():
    payload = make_valid_spec().model_dump(mode="json")
    payload["core_objects"] = [*payload["core_objects"], "Customer", "Order"]
    payload["er_diagrams"] = [_diagram()]
    spec = ProjectSpecPayload.model_validate(payload)

    validate_node_output(spec, {"input_refs": spec.source_refs})
