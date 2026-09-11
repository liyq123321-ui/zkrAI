"""Import three template-shaped PRDs as display-only database snapshots.

The importer deliberately does not update ``Project.current_spec_version_id``.
That keeps task decomposition, Agent Specs, review state, and phase transitions
bound to their existing immutable Spec versions.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import unicodedata
import uuid
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database.database import SessionLocal
from app.database.models import PrdVersion, Project, SpecVersion, WorkItem
from app.domain.types import ErDiagram, ProjectSpecPayload, SpecStatus, WorkItemKind
from app.services.drawio_diagrams import diagram_anchor, validate_drawio_xml
from app.services.prd_review import READ_ONLY_TEMPLATE_SOURCE


REPO_ROOT = BACKEND_ROOT.parent


def _normalized_markdown(markdown: str) -> str:
    return unicodedata.normalize("NFC", markdown).replace("\r\n", "\n").replace("\r", "\n")


def _markdown_hash(markdown: str) -> str:
    return hashlib.sha256(_normalized_markdown(markdown).encode("utf-8")).hexdigest()


def _structured_hash(content: object) -> str:
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _er_xml(
    page_id: str,
    entities: list[tuple[str, str, int, int, int, int]],
    relationships: list[tuple[str, str, str, str]],
) -> str:
    mxfile = Element(
        "mxfile",
        {"host": "firstFlight", "version": "24.7.17", "type": "device", "compressed": "false"},
    )
    diagram = SubElement(mxfile, "diagram", {"id": page_id, "name": "ER 关系图"})
    model = SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "1200", "dy": "800", "grid": "1", "gridSize": "10",
            "page": "1", "pageScale": "1", "pageWidth": "1169", "pageHeight": "827",
        },
    )
    root = SubElement(model, "root")
    SubElement(root, "mxCell", {"id": "0"})
    SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    for entity_id, label, x, y, width, height in entities:
        cell = SubElement(
            root,
            "mxCell",
            {
                "id": entity_id,
                "value": label,
                "style": (
                    "shape=table;html=1;whiteSpace=wrap;container=1;collapsible=0;"
                    "rounded=1;fillColor=#f8fafc;strokeColor=#2563eb;fontColor=#0f172a;"
                    "align=left;verticalAlign=top;spacing=10;fontSize=13;"
                ),
                "vertex": "1",
                "parent": "1",
            },
        )
        SubElement(
            cell,
            "mxGeometry",
            {"x": str(x), "y": str(y), "width": str(width), "height": str(height), "as": "geometry"},
        )
    for edge_id, label, source, target in relationships:
        cell = SubElement(
            root,
            "mxCell",
            {
                "id": edge_id,
                "value": label,
                "style": (
                    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
                    "html=1;startArrow=ERone;startFill=0;endArrow=ERmany;endFill=0;"
                    "strokeColor=#475569;fontColor=#334155;"
                ),
                "edge": "1",
                "parent": "1",
                "source": source,
                "target": target,
            },
        )
        SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return tostring(mxfile, encoding="unicode")


def _diagram_specs() -> dict[str, ErDiagram]:
    diagrams = {
        "length": ErDiagram(
            diagram_id="length-converter-model",
            title="长度换算器页面状态模型",
            after_section="core_objects",
            drawio_xml=_er_xml(
                "length-converter-model",
                [
                    ("length-input", "ConversionInput\nrawValue: string\nnumericValue: decimal", 60, 90, 240, 110),
                    ("length-result", "ConversionResult\ncentimetres: decimal\ndisplayValue: string", 430, 35, 250, 110),
                    ("length-error", "ValidationMessage\nerrorType: enum\nmessage: string", 430, 235, 250, 110),
                ],
                [
                    ("length-valid", "校验通过后生成 0..1", "length-input", "length-result"),
                    ("length-invalid", "校验失败后生成 0..1", "length-input", "length-error"),
                ],
            ),
        ),
        "temperature": ErDiagram(
            diagram_id="temperature-converter-model",
            title="温度换算器页面状态模型",
            after_section="core_objects",
            drawio_xml=_er_xml(
                "temperature-converter-model",
                [
                    ("temperature-input", "TemperatureInput\nrawValue: string\nnumericValue: decimal", 60, 90, 250, 110),
                    ("temperature-result", "ConversionResult\nfahrenheitValue: decimal\ndisplayValue: string", 440, 35, 270, 110),
                    ("temperature-error", "ValidationMessage\nerrorType: enum\nmessage: string", 440, 235, 270, 110),
                ],
                [
                    ("temperature-valid", "校验通过后生成 0..1", "temperature-input", "temperature-result"),
                    ("temperature-invalid", "校验失败后生成 0..1", "temperature-input", "temperature-error"),
                ],
            ),
        ),
        "task-manager": ErDiagram(
            diagram_id="task-manager-domain-model",
            title="用户、项目与任务核心关系",
            after_section="core_objects",
            drawio_xml=_er_xml(
                "task-manager-domain-model",
                [
                    ("user", "User\nid: UUID PK\nlogin: string\npasswordHash: string\ncreatedAt: datetime", 40, 70, 245, 135),
                    ("project-access", "ProjectAccess\nuserId: UUID FK\nprojectId: UUID FK\naccessLevel: enum", 390, 45, 245, 120),
                    ("project", "Project\nid: UUID PK\ncreatorId: UUID FK\nname: string\ndescription: string\ncreatedAt / updatedAt", 740, 70, 250, 150),
                    ("task", "Task\nid: UUID PK\nprojectId: UUID FK\nassigneeId: UUID FK\ntitle / description\npriority / status\ndueDate / tags\ncreatedAt / updatedAt", 740, 330, 270, 205),
                ],
                [
                    ("user-access", "拥有访问关系 1..N", "user", "project-access"),
                    ("access-project", "授权到项目 N..1", "project-access", "project"),
                    ("project-task", "包含 0..N", "project", "task"),
                    ("user-task", "可负责 0..N", "user", "task"),
                ],
            ),
        ),
    }
    for diagram in diagrams.values():
        validate_drawio_xml(diagram.drawio_xml)
    return diagrams


TARGETS = (
    {
        "key": "length",
        "project_id": "73ccaabe-7647-441d-9697-0228f45dfb87",
        "root_wi": "ab3755ea-60d9-42b3-bb17-15da034cefbd",
        "baseline_revision": 28,
        "target_revision": 29,
        "document": REPO_ROOT / "output/prd/length-unit-converter-prd-v29.md",
        "placeholder": "firstflight-er-length-converter-model-a1373bee",
    },
    {
        "key": "temperature",
        "project_id": "350bf723-c85b-42b2-855a-37ba407b6925",
        "root_wi": "c4a0a782-7c9b-4c1e-8af2-5684c236183c",
        "baseline_revision": 4,
        "target_revision": 5,
        "document": REPO_ROOT / "output/prd/temperature-converter-prd-v5.md",
        "placeholder": "firstflight-er-temperature-converter-model-6dc4ab01",
    },
    {
        "key": "task-manager",
        "project_id": "6b6eaef3-c58b-4aee-91a0-4bbb73c2988f",
        "root_wi": "6d4ff6bb-b2a8-459f-90d0-39338ed7669c",
        "baseline_revision": 9,
        "target_revision": 10,
        "document": REPO_ROOT / "output/prd/team-task-management-prd-v10.md",
        "placeholder": "firstflight-er-task-manager-domain-model-273edf5e",
    },
)


def anchors() -> dict[str, str]:
    return {key: diagram_anchor(diagram) for key, diagram in _diagram_specs().items()}


def import_versions(*, dry_run: bool) -> list[dict[str, object]]:
    diagrams = _diagram_specs()
    report: list[dict[str, object]] = []
    with SessionLocal() as db:
        for target in TARGETS:
            project = db.get(Project, target["project_id"])
            root = db.get(WorkItem, target["root_wi"])
            current = db.get(SpecVersion, project.current_spec_version_id) if project else None
            if (
                project is None
                or root is None
                or root.kind != WorkItemKind.ROOT.value
                or root.parent_id is not None
                or root.project_id != project.id
                or current is None
                or current.project_id != project.id
                or current.revision != target["baseline_revision"]
            ):
                raise RuntimeError(f"{target['key']}: database baseline does not match the import contract")
            current_binding = (
                db.query(PrdVersion).filter_by(spec_version_id=current.id).one_or_none()
            )
            if current_binding is None:
                raise RuntimeError(f"{target['key']}: workflow PRD binding is missing")

            diagram = diagrams[target["key"]]
            anchor = diagram_anchor(diagram)
            markdown = _normalized_markdown(Path(target["document"]).read_text(encoding="utf-8"))
            markdown = markdown.replace(str(target["placeholder"]), anchor)
            if "{{ER_" in markdown or f"](#{anchor})" not in markdown:
                raise RuntimeError(f"{target['key']}: ER diagram anchor is missing from Markdown")

            content = copy.deepcopy(current.content)
            existing_diagrams = [
                item for item in content.get("er_diagrams", [])
                if isinstance(item, dict) and item.get("diagram_id") != diagram.diagram_id
            ]
            content["er_diagrams"] = [*existing_diagrams, diagram.model_dump(mode="json")]
            ProjectSpecPayload.model_validate(content)
            content_hash = _structured_hash(content)
            markdown_hash = _markdown_hash(markdown)

            existing = (
                db.query(SpecVersion)
                .filter_by(project_id=project.id, revision=target["target_revision"])
                .one_or_none()
            )
            if existing is not None:
                binding = db.get(PrdVersion, (root.id, target["target_revision"]))
                if (
                    existing.generation_source != READ_ONLY_TEMPLATE_SOURCE
                    or existing.parent_version_id != current.id
                    or existing.content_hash != content_hash
                    or _markdown_hash(existing.markdown) != markdown_hash
                    or binding is None
                    or binding.spec_version_id != existing.id
                    or binding.commit_sha is not None
                ):
                    raise RuntimeError(f"{target['key']}: target revision already exists with different content")
                report.append({"project": target["key"], "version": target["target_revision"], "action": "unchanged", "anchor": anchor})
                continue

            spec = SpecVersion(
                id=str(uuid.uuid4()),
                project_id=project.id,
                revision=target["target_revision"],
                content=content,
                markdown=markdown,
                generation_source=READ_ONLY_TEMPLATE_SOURCE,
                input_refs=[
                    f"spec_version:{current.id}",
                    "template:01_阿里产品经理_PRD需求文档",
                    "template:02_鹅厂PRD需求文档_含模板",
                ],
                generator_agent_session_id="codex-readonly-prd-import-20260911",
                generator_call_id=f"readonly-prd:{project.id}:v{target['target_revision']}",
                parent_version_id=current.id,
                change_summary="按阿里与鹅厂 PRD 模板重组为正式只读展示版本，并补充 ER 关系图；不改变任务基线。",
                content_hash=content_hash,
                status=SpecStatus.DRAFT.value,
            )
            db.add(spec)
            db.add(
                PrdVersion(
                    wi=root.id,
                    version=target["target_revision"],
                    spec_version_id=spec.id,
                    filename=f"docs/prd/{root.id}/v{target['target_revision']}.md",
                    pr_number=current_binding.pr_number,
                    commit_sha=None,
                    content_hash=markdown_hash,
                    spec_content_hash=content_hash,
                    change_summary=spec.change_summary,
                )
            )
            report.append({"project": target["key"], "version": target["target_revision"], "action": "would-create" if dry_run else "created", "anchor": anchor})
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-anchors", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.print_anchors:
        print(json.dumps(anchors(), ensure_ascii=False, indent=2))
        return
    if args.dry_run == args.apply:
        parser.error("choose exactly one of --dry-run or --apply")
    print(json.dumps(import_versions(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
