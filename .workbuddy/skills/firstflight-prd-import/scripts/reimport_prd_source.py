#!/usr/bin/env python3
"""Re-issue an imported PRD from its original file, in place, with a backup.

Step 2 of the ``change -> re-import -> verify`` loop in ``SKILL.md``. It drives
the **real service path** (``prd_source_import`` -> ``BlockService.import_source``
-> ``DocumentAssetService``), so it exercises every rule instead of hand-editing
rows, and it takes a backup first.

    cd "<repo>/backend"
    ./.venv/Scripts/python.exe \
      ../.workbuddy/skills/firstflight-prd-import/scripts/reimport_prd_source.py \
      --project 313f1c46 --source "C:/Users/zkr/Downloads/xxx.docx"

    # prove it changed nothing
    ... reimport_prd_source.py --project 313f1c46 --source ... --dry-run

Then confirm with ``check_prd_import.py`` and require exit 0.

Run it from ``backend/`` so ``.env`` and ``data/`` resolve; the script also
chdirs there itself when it can find the repo root.
"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND = REPO_ROOT / "backend"


def load_env(path: Path) -> int:
    """Feed ``backend/.env`` into ``os.environ`` the way uvicorn's --env-file does.

    This **must** happen before any ``app.*`` import: ``app/database/database.py``
    calls ``Settings.from_env()`` and builds the engine at module import time, so
    a late load silently points the whole run at the wrong database. An already
    exported shell variable wins, so an operator can override one value.
    """

    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def port_is_served(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def backup(db_path: Path, label: str) -> Path:
    """SQLite's online backup API — never copy a live database file."""

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db_path.with_name(f"{db_path.name}.bak-before-{label}-{stamp}")
    source, destination = sqlite3.connect(str(db_path)), sqlite3.connect(str(target))
    with destination:
        source.backup(destination)
    source.close()
    destination.close()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="project id or id prefix")
    parser.add_argument("--source", required=True, help="the original .docx/.pdf/.md")
    parser.add_argument("--actor", default="owner-1")
    parser.add_argument("--label", default="reimport", help="backup filename label")
    parser.add_argument("--no-store-source", action="store_true",
                        help="do not keep the original file (leaves the image rule unanchored)")
    parser.add_argument("--dry-run", action="store_true",
                        help="extract and report only; never touch the database")
    args = parser.parse_args()

    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    loaded = load_env(BACKEND / ".env")

    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"source file not found: {source}")

    # Imported only now — see load_env() for why the order matters.
    from sqlalchemy import text as sql

    from app.config import Settings
    from app.database.database import SessionLocal
    from app.database.models import Project
    from app.models import Document, DocumentAsset
    from app.services.block_service import BlockService
    from app.services.document_asset_service import DocumentAssetService
    from app.services.imported_prd_layout import assess_imported_hierarchy
    from app.services.prd_source_import import import_prd_source

    print(f"env      : {loaded} values loaded from backend/.env")

    fresh = import_prd_source(
        source.name, base64.b64encode(source.read_bytes()).decode(),
        keep_file=not args.no_store_source,
    )
    # ``import_source`` recomputes and re-stores brief.source_prd.hierarchy itself,
    # so this is only for the operator's benefit.
    assessment = assess_imported_hierarchy(fresh["content"])
    print(f"extracted: {source.name}")
    print(f"           {len(fresh['content'])} characters | "
          f"{assessment['headings']} headings | clear {assessment['clear']} | "
          f"import_id {fresh['import_id'] or '(none)'}")
    if not fresh["import_id"]:
        print("           WARNING: re-run without --no-store-source — the image rule")
        print("                    needs the stored .docx to be verifiable")

    settings = Settings.from_env()
    service = BlockService(SessionLocal)
    assets = DocumentAssetService(
        service, settings.prd_asset_root or (settings.codex_cwd / ".firstflight-prd-assets")
    )

    with SessionLocal() as db:
        matches = [row[0] for row in db.execute(sql("select id from projects"))
                   if row[0].startswith(args.project)]
        if len(matches) != 1:
            raise SystemExit(f"--project {args.project!r} matched {len(matches)} projects")
        project_id = matches[0]
        document = db.query(Document).filter_by(project_id=project_id).one()
        document_id, revision = document.id, document.revision
        project = db.get(Project, project_id)
        before = dict(project.brief or {}).get("source_prd") or {}

    print(f"project  : {project_id}")
    print(f"document : {document_id} | revision {revision}")
    print(f"previous : {before.get('filename')} | import_id {before.get('import_id') or '(none)'}")
    # import_id is sha256(content)[:32], so an unchanged id means the extractor
    # produced byte-identical output — a cheap "did my change do anything?".
    unchanged = bool(before.get("import_id")) and before["import_id"] == fresh["import_id"]
    print(f"extract  : {'unchanged — extractor output is byte-identical' if unchanged else 'CHANGED'}")

    if args.dry_run:
        print("\n--dry-run: nothing written. Re-run without it to apply.")
        return 0

    db_path = Path(settings.database_url.split("sqlite:///")[-1])
    if port_is_served(8088) and not args.dry_run:
        print("\nWARNING  : something is listening on 8088 — the running backend may hold a\n"
              "           stale session. Restart it after this finishes if you also changed\n"
              "           code under backend/app/.")
    print(f"backup   : {backup(db_path, f'{args.label}-{project_id[:8]}').name}")

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        brief = dict(project.brief or {})
        previous = brief.get("source_prd") or {}
        brief["source_prd"] = {
            # Keep anything we do not own; drop a stale computed hierarchy.
            **{key: value for key, value in previous.items() if key != "hierarchy"},
            "filename": fresh["filename"],
            "media_type": fresh["media_type"],
            "title": fresh["title"],
            "content": fresh["content"],
            "import_id": fresh["import_id"],
        }
        project.brief = brief
        db.add(project)
        db.commit()

    snapshot = service.import_source(
        document_id, args.actor, revision, replace=True, assets=assets
    )
    blocks = snapshot["blocks"]
    print(f"revision : {snapshot['document']['revision']} | blocks {len(blocks)}")

    # import_source discards assets as it deletes Blocks, but sweeps from older
    # revisions can still be lying around — collapse them onto one call.
    with SessionLocal() as db:
        live = {block["id"] for block in blocks}
        dead = {asset.block_id for asset in db.query(DocumentAsset).filter_by(
            document_id=document_id) if asset.block_id and asset.block_id not in live}
        freed = assets.discard_blocks(db, document_id, dead)
        db.commit()

    with SessionLocal() as db:
        rows = db.query(DocumentAsset).filter_by(document_id=document_id).all()
        live = {block["id"] for block in blocks}
        print(f"assets   : {len(rows)} rows | {sum(a.size_bytes for a in rows)} bytes | "
              f"orphaned {sum(1 for a in rows if a.block_id and a.block_id not in live)}")
        print(f"swept    : {freed} unreachable assets")
    print(f"images   : {sum(1 for b in blocks if 'asset://' in (b['content'] or ''))} blocks carry one | "
          f"leftover tokens {sum(1 for b in blocks if '[[FF_IMPORTED_IMAGE_' in (b['content'] or ''))}")
    print(f"tops     : {sum(1 for b in blocks if not b.get('parent_id'))} top-level blocks")

    print("\nnow run: check_prd_import.py   (exit 0 means every rule holds)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
