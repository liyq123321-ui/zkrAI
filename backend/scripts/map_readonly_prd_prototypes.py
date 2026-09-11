"""Map existing ready HTML prototypes onto display-only PRD snapshots.

The HTML, title, and content hash are copied byte-for-byte.  No prototype is
generated, and projects without an existing ready prototype are reported as
``no-source`` instead of receiving fabricated content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import desc

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database.database import SessionLocal
from app.database.models import PrdPrototype, Project, SpecVersion
from app.services.prd_review import READ_ONLY_TEMPLATE_SOURCE


TARGETS = (
    ("length", "73ccaabe-7647-441d-9697-0228f45dfb87", 29),
    ("temperature", "350bf723-c85b-42b2-855a-37ba407b6925", 5),
    ("task-manager", "6b6eaef3-c58b-4aee-91a0-4bbb73c2988f", 10),
)


def map_prototypes(*, dry_run: bool) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    with SessionLocal() as db:
        for key, project_id, target_revision in TARGETS:
            project = db.get(Project, project_id)
            target = (
                db.query(SpecVersion)
                .filter_by(project_id=project_id, revision=target_revision)
                .one_or_none()
            )
            if (
                project is None
                or target is None
                or target.generation_source != READ_ONLY_TEMPLATE_SOURCE
            ):
                raise RuntimeError(f"{key}: read-only target v{target_revision} is missing")

            existing = (
                db.query(PrdPrototype)
                .filter_by(spec_version_id=target.id)
                .one_or_none()
            )
            source = (
                db.query(PrdPrototype)
                .join(SpecVersion, SpecVersion.id == PrdPrototype.spec_version_id)
                .filter(
                    PrdPrototype.project_id == project_id,
                    PrdPrototype.status == "ready",
                    PrdPrototype.html.is_not(None),
                    PrdPrototype.content_hash.is_not(None),
                    SpecVersion.revision < target_revision,
                    SpecVersion.id != target.id,
                )
                .order_by(desc(SpecVersion.revision), desc(PrdPrototype.created_at))
                .first()
            )
            if source is None:
                if existing is not None:
                    raise RuntimeError(f"{key}: target prototype exists without a source to verify")
                report.append(
                    {"project": key, "version": target_revision, "action": "no-source"}
                )
                continue

            source_spec = db.get(SpecVersion, source.spec_version_id)
            if (
                source_spec is None
                or not source.html
                or hashlib.sha256(source.html.encode("utf-8")).hexdigest()
                != source.content_hash
            ):
                raise RuntimeError(f"{key}: latest ready source prototype is invalid")

            if existing is not None:
                if (
                    existing.project_id != project_id
                    or existing.status != "ready"
                    or existing.html != source.html
                    or existing.content_hash != source.content_hash
                ):
                    raise RuntimeError(f"{key}: target prototype differs from the source")
                report.append(
                    {
                        "project": key,
                        "version": target_revision,
                        "source_version": source_spec.revision,
                        "action": "unchanged",
                    }
                )
                continue

            db.add(
                PrdPrototype(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    spec_version_id=target.id,
                    generator_agent_session_id="codex-readonly-prototype-map-20260911",
                    generator_call_id=f"readonly-prototype-map:{project_id}:v{target_revision}",
                    status="ready",
                    title=source.title,
                    html=source.html,
                    content_hash=source.content_hash,
                    generation_summary=(
                        f"映射自 PRD v{source_spec.revision} 的既有 HTML 原型；"
                        "未重新生成或修改内容。"
                    ),
                )
            )
            report.append(
                {
                    "project": key,
                    "version": target_revision,
                    "source_version": source_spec.revision,
                    "html_chars": len(source.html),
                    "content_hash": source.content_hash,
                    "action": "would-create" if dry_run else "created",
                }
            )
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.dry_run == args.apply:
        parser.error("choose exactly one of --dry-run or --apply")
    print(json.dumps(map_prototypes(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
