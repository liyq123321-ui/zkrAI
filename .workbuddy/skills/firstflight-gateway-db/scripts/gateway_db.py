#!/usr/bin/env python
"""Inspect and clean the firstFlight SQLite gateway database.

Subcommands
-----------
scope    Resolve target rows and print the full reference map (read-only).
delete   Snapshot, then delete the selected scope in one transaction, then verify.
orphans  Find (and optionally remove) child rows whose parent row is gone.
restore  Replace gateway.db with a previous gateway-before-*.db snapshot.

Scopes (``--scope``)
--------------------
work-item-domain  plan-tree rows only: work_items + the tables that hang off a
                  work item (tasks / versions / comments_index / dependencies /
                  agent_specs / work_item_runs). Keeps the project row, PRDs,
                  spec versions, sessions and audit history.
project-domain    everything the project owns, including the ``projects`` row,
                  sessions, specs, PRDs, conversations, the audit trail, and the
                  second-hop ``prd_*`` rows keyed by ``prd_documents.id``.
                  This is what removes an entry from the UI's 当前任务 dropdown.

Run with the repo interpreter:
    backend/.venv/Scripts/python.exe scripts/gateway_db.py scope --project-id <uuid>
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Real ids in this database are UUIDs (or ``prd_<32 hex>`` for prd_documents).
# Some columns instead carry a *synthetic marker* written by the readonly import
# paths, e.g. ``codex-readonly-prd-import-20260911`` or
# ``readonly-prototype-map:<project>:v5``. Those look like dangling references but
# are intentional, and the rows belong to live projects — never auto-delete them.
UUID_RE = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|prd_[0-9a-f]{32})$",
    re.IGNORECASE,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DB = REPO_ROOT / "backend" / "data" / "gateway.db"

# Tables that carry a work-item foreign key. Verified against the live schema;
# see references/schema.md. (table, column)
WORK_ITEM_DOMAIN: list[tuple[str, str]] = [
    ("work_items", "id"),
    ("work_item_dependencies", "from_work_item_id"),
    ("work_item_dependencies", "to_work_item_id"),
    ("agent_specs", "work_item_id"),
    ("versions", "wi"),
    ("comments_index", "wi"),
    ("work_item_runs", "work_item_id"),
    ("tasks", "wi"),
]

# Project domain: (table, column, seed-set). The seed-set names come from
# resolve_project_graph(). Deleting by ``project_id``/``session_id`` is preferred
# because it is self-describing; indirect columns (work item ids, spec version
# ids, prd document ids) are included so a project sweep also removes anything
# left behind by an earlier work-item-domain cleanup.
PROJECT_DOMAIN: list[tuple[str, str, str]] = [
    ("work_items", "project_id", "project"),
    ("work_item_dependencies", "project_id", "project"),
    ("tasks", "wi", "work_item"),
    ("versions", "wi", "work_item"),
    ("comments_index", "wi", "work_item"),
    ("agent_specs", "work_item_id", "work_item"),
    ("work_item_runs", "work_item_id", "work_item"),
    # second hop: prd_* rows hang off prd_documents.id, not off project_id.
    ("prd_blocks", "document_id", "prd_document"),
    ("prd_block_versions", "document_id", "prd_document"),
    ("prd_block_comments", "document_id", "prd_document"),
    ("prd_agent_runs", "document_id", "prd_document"),
    ("prd_document_artifacts", "document_id", "prd_document"),
    ("prd_document_assets", "document_id", "prd_document"),
    ("agent_calls", "project_id", "project"),
    ("agent_runtime_events", "project_id", "project"),
    ("agent_sessions", "project_id", "project"),
    ("artifacts", "project_id", "project"),
    ("audit_events", "project_id", "project"),
    ("clarification_requests", "project_id", "project"),
    ("clarification_responses", "project_id", "project"),
    ("command_attempts", "project_id", "project"),
    ("intake_analysis_claims", "project_id", "project"),
    ("prd_documents", "project_id", "project"),
    ("prd_prototypes", "project_id", "project"),
    ("sdlc_route_decisions", "project_id", "project"),
    ("spec_reviews", "project_id", "project"),
    ("spec_versions", "project_id", "project"),
    ("processed_commands", "session_id", "session"),
    ("conversations", "id", "session"),
    ("projects", "id", "project"),
]

# Parent -> child links that a plain "scan for this id value" sweep cannot see,
# because the child stores the *parent row's own id* rather than a project /
# session / work-item id. (parent_table, parent_col, child_table, child_col)
PARENT_LINKS: list[tuple[str, str, str, str]] = [
    ("prd_documents", "id", "prd_blocks", "document_id"),
    ("prd_documents", "id", "prd_block_versions", "document_id"),
    ("prd_documents", "id", "prd_block_comments", "document_id"),
    ("prd_documents", "id", "prd_agent_runs", "document_id"),
    ("prd_documents", "id", "prd_document_artifacts", "document_id"),
    ("prd_documents", "id", "prd_document_assets", "document_id"),
    ("prd_blocks", "id", "prd_blocks", "parent_id"),
    ("prd_blocks", "id", "prd_block_versions", "block_id"),
    ("prd_blocks", "id", "prd_block_comments", "block_id"),
    ("prd_blocks", "id", "prd_agent_runs", "block_id"),
    ("prd_blocks", "id", "prd_document_assets", "block_id"),
    ("prd_agent_runs", "id", "prd_block_versions", "run_id"),
    ("spec_versions", "id", "spec_reviews", "spec_version_id"),
    ("spec_versions", "id", "prd_prototypes", "spec_version_id"),
    ("spec_versions", "id", "prd_documents", "source_spec_version_id"),
    ("spec_versions", "id", "prd_documents", "submitted_spec_id"),
    ("spec_versions", "id", "spec_versions", "parent_version_id"),
    ("work_items", "id", "tasks", "wi"),
    ("work_items", "id", "versions", "wi"),
    ("work_items", "id", "comments_index", "wi"),
    ("work_items", "id", "agent_specs", "work_item_id"),
    ("work_items", "id", "work_item_runs", "work_item_id"),
    ("projects", "id", "agent_sessions", "project_id"),
    ("projects", "id", "spec_versions", "project_id"),
    ("projects", "id", "prd_documents", "project_id"),
    ("agent_sessions", "id", "agent_calls", "agent_session_id"),
    ("agent_sessions", "id", "agent_runtime_events", "agent_session_id"),
    ("agent_sessions", "id", "spec_versions", "generator_agent_session_id"),
    ("agent_sessions", "id", "prd_prototypes", "generator_agent_session_id"),
    ("agent_calls", "id", "prd_prototypes", "generator_call_id"),
]

SCOPES = ("work-item-domain", "project-domain")


def qmarks(n: int) -> str:
    return ",".join("?" * n)


def connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        sys.exit(f"error: database not found: {db}")
    return sqlite3.connect(db)


def tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "select name from sqlite_master where type='table' order by name"
        )
    ]


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def scan_references(conn: sqlite3.Connection, values: list[str]) -> dict[str, list[tuple[str, int]]]:
    """Every (table, column) whose value matches any of ``values``."""
    if not values:
        return {}
    out: dict[str, list[tuple[str, int]]] = {}
    for t in tables(conn):
        hits: list[tuple[str, int]] = []
        for col in columns(conn, t):
            try:
                n = conn.execute(
                    f'select count(*) from "{t}" where "{col}" in ({qmarks(len(values))})',
                    values,
                ).fetchone()[0]
            except sqlite3.Error:
                continue
            if n:
                hits.append((col, n))
        if hits:
            out[t] = hits
    return out


def resolve_work_items(
    conn: sqlite3.Connection, ids: list[str] | None, project_ids: list[str] | None
) -> list[str]:
    if ids:
        found = [
            r[0]
            for r in conn.execute(
                f"select id from work_items where id in ({qmarks(len(ids))})", ids
            )
        ]
        missing = set(ids) - set(found)
        if missing:
            print(f"warning: {len(missing)} id(s) not found in work_items: {sorted(missing)}")
        return found
    return [
        r[0]
        for r in conn.execute(
            f"select id from work_items where project_id in ({qmarks(len(project_ids or []))})",
            project_ids or [],
        )
    ]


def resolve_project_graph(
    conn: sqlite3.Connection, project_ids: list[str]
) -> dict[str, list[str]]:
    """Expand project ids into every id set that rows may reference."""

    def query(sql: str, params: list[str]) -> list[str]:
        if not params:
            return []
        return [r[0] for r in conn.execute(sql.replace("{ph}", qmarks(len(params))), params)]

    projects = query("select id from projects where id in ({ph})", project_ids)
    missing = set(project_ids) - set(projects)
    if missing:
        print(f"warning: {len(missing)} project id(s) not found in projects: {sorted(missing)}")
    return {
        "project": projects,
        "session": query("select session_id from projects where id in ({ph})", projects),
        "agent_session": query(
            "select id from agent_sessions where project_id in ({ph})", projects
        ),
        "spec_version": query(
            "select id from spec_versions where project_id in ({ph})", projects
        ),
        "work_item": query(
            "select id from work_items where project_id in ({ph})", projects
        ),
        "prd_document": query(
            "select id from prd_documents where project_id in ({ph})", projects
        ),
    }


def orphan_predicate(parent_table: str, parent_col: str, child_table: str, child_col: str) -> str:
    return (
        f'"{child_col}" is not null and "{child_col}" != \'\' and '
        f'not exists (select 1 from "{parent_table}" p where p."{parent_col}" = "{child_table}"."{child_col}")'
    )


def find_orphans(conn: sqlite3.Connection) -> list[tuple[str, str, str, str, int, str]]:
    """(parent_table, parent_col, child_table, child_col, count, delete_sql)."""

    present = set(tables(conn))
    found: list[tuple[str, str, str, str, int, str]] = []
    for ptable, pcol, ctable, ccol in PARENT_LINKS:
        if ptable not in present or ctable not in present:
            continue
        if ccol not in columns(conn, ctable) or pcol not in columns(conn, ptable):
            continue
        where = orphan_predicate(ptable, pcol, ctable, ccol)
        n = conn.execute(f'select count(*) from "{ctable}" where {where}').fetchone()[0]
        if n:
            found.append(
                (ptable, pcol, ctable, ccol, n, f'delete from "{ctable}" where {where}')
            )
    return found


def orphan_samples(
    conn: sqlite3.Connection, ptable: str, pcol: str, ctable: str, ccol: str, limit: int = 4
) -> list[str]:
    where = orphan_predicate(ptable, pcol, ctable, ccol)
    rows = conn.execute(
        f'select distinct "{ccol}" from "{ctable}" where {where} limit {int(limit)}'
    ).fetchall()
    return [str(r[0]) for r in rows]


def looks_synthetic(values: list[str]) -> bool:
    """True when none of the offending values is a real id."""

    return bool(values) and not any(UUID_RE.match(v) for v in values)


def backup(conn: sqlite3.Connection, db: Path, reason: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = db.with_name(f"gateway-before-{reason}-{stamp}.db")
    out = sqlite3.connect(dest)
    try:
        with out:
            conn.backup(out)
    finally:
        out.close()
    return dest


def print_reference_map(refs: dict[str, list[tuple[str, int]]]) -> int:
    total = 0
    for t in sorted(refs):
        parts = ", ".join(f"{c}={n}" for c, n in refs[t])
        print(f"  {t:<28} {parts}")
        total += sum(n for _, n in refs[t])
    return total


def plan_work_item_domain(
    conn: sqlite3.Connection, ids: list[str]
) -> list[tuple[str, int, str | None, list[str] | None]]:
    """Rows as (label, count, delete-sql, params)."""

    plan: list[tuple[str, int, str | None, list[str] | None]] = []
    for table, col in WORK_ITEM_DOMAIN:
        if table == "work_item_dependencies":
            continue
        sql = f'delete from "{table}" where "{col}" in ({qmarks(len(ids))})'
        n = conn.execute(
            f'select count(*) from "{table}" where "{col}" in ({qmarks(len(ids))})', ids
        ).fetchone()[0]
        if n:
            plan.append((f"{table}.{col}", n, sql, list(ids)))
    dep_sql = (
        "delete from work_item_dependencies "
        f"where from_work_item_id in ({qmarks(len(ids))}) "
        f"or to_work_item_id in ({qmarks(len(ids))})"
    )
    dep_n = conn.execute(
        "select count(*) from work_item_dependencies "
        f"where from_work_item_id in ({qmarks(len(ids))}) "
        f"or to_work_item_id in ({qmarks(len(ids))})",
        ids + ids,
    ).fetchone()[0]
    if dep_n:
        plan.append(("work_item_dependencies (distinct rows)", dep_n, dep_sql, list(ids) + list(ids)))
    return plan


def plan_project_domain(
    conn: sqlite3.Connection, graph: dict[str, list[str]]
) -> list[tuple[str, int, str | None, list[str] | None]]:
    plan: list[tuple[str, int, str | None, list[str] | None]] = []
    for table, col, seed in PROJECT_DOMAIN:
        vals = graph.get(seed) or []
        if not vals:
            continue
        sql = f'delete from "{table}" where "{col}" in ({qmarks(len(vals))})'
        n = conn.execute(
            f'select count(*) from "{table}" where "{col}" in ({qmarks(len(vals))})', vals
        ).fetchone()[0]
        if n:
            plan.append((f"{table}.{col}", n, sql, list(vals)))
    return plan


def build_plan(
    conn: sqlite3.Connection, args: argparse.Namespace
) -> tuple[list[tuple[str, int, str | None, list[str] | None]], list[str], dict[str, list[str]]]:
    if args.scope == "work-item-domain":
        ids = resolve_work_items(conn, args.id, args.project_id)
        return plan_work_item_domain(conn, ids), ids, {"work_item": ids}
    graph = resolve_project_graph(conn, args.project_id or [])
    seeds = [v for vals in graph.values() for v in vals]
    return plan_project_domain(conn, graph), seeds, graph


def print_plan(plan: list[tuple[str, int, str | None, list[str] | None]]) -> int:
    for label, n, _, _ in plan:
        print(f"  {label:<44} {n}")
    return sum(n for _, n, _, _ in plan)


def print_orphans(conn: sqlite3.Connection, found: list[tuple[str, str, str, str, int, str]]) -> int:
    for ptable, pcol, ctable, ccol, n, _ in found:
        samples = orphan_samples(conn, ptable, pcol, ctable, ccol)
        flag = "  [synthetic marker — do NOT delete]" if looks_synthetic(samples) else ""
        print(f"  {ctable}.{ccol:<24} -> {ptable}.{pcol:<22} {n}{flag}")
        if flag:
            print(f"      sample: {', '.join(samples)}")
    return sum(n for *_, n, _ in found)


def run_orphan_check(conn: sqlite3.Connection) -> int:
    """Print any child rows whose parent is missing. Returns rows to clean."""

    found = find_orphans(conn)
    print("\n--- orphan check (parent -> child links) ---")
    if not found:
        print("  none")
        return 0
    total = print_orphans(conn, found)
    real = [f for f in found if not looks_synthetic(orphan_samples(conn, *f[:4]))]
    real_total = sum(f[4] for f in real)
    print(f"  {'TOTAL dangling rows':<44} {total}")
    if real_total:
        links = " ".join(f"--link {f[2]}.{f[3]}" for f in real)
        print(f"  {'cleanable (non-synthetic)':<44} {real_total}")
        print(f"  run: gateway_db.py orphans --delete --yes {links}")
    return real_total


def cmd_scope(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    plan, seeds, graph = build_plan(conn, args)

    if args.scope == "project-domain":
        print("resolved project graph:")
        for name, vals in graph.items():
            preview = ", ".join(v[:8] for v in vals[:6])
            print(f"  {name:<14} {len(vals):<4} {preview}{' …' if len(vals) > 6 else ''}")
    else:
        print(f"resolved work items: {len(seeds)}")

    print("\n--- reference map (all tables x all columns) ---")
    refs = scan_references(conn, seeds)
    total = print_reference_map(refs)
    if not refs:
        print("  (none)")
    print(f"  {'TOTAL matching rows':<28} {total}")

    print(f"\n--- proposed {args.scope} deletion plan ---")
    n = print_plan(plan)
    print(f"  {'TOTAL':<44} {n}")
    run_orphan_check(conn)
    conn.close()
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    db = Path(args.db)
    conn = connect(db)
    plan, seeds, _ = build_plan(conn, args)
    if not plan:
        sys.exit("error: nothing to delete (empty plan)")

    total = sum(n for _, n, _, _ in plan)
    print(f"scope: {args.scope} · {total} rows")
    for label, n, _, _ in plan:
        print(f"  {label:<44} {n}")

    if not args.yes:
        print("\ndry run — re-run with --yes to execute")
        conn.close()
        return 0

    snap = backup(conn, db, args.reason)
    print(f"\nsnapshot: {snap.name} ({snap.stat().st_size:,} bytes)")
    chk = sqlite3.connect(snap)
    for table in ("projects", "work_items"):
        n_now = conn.execute(f"select count(*) from {table}").fetchone()[0]
        n_bak = chk.execute(f"select count(*) from {table}").fetchone()[0]
        if n_now != n_bak:
            chk.close()
            sys.exit(f"error: snapshot verification failed on {table} ({n_now} vs {n_bak}); aborted")
    chk.close()
    print("snapshot verified")

    with conn:
        for label, _, sql, params in plan:
            cur = conn.execute(sql, params)
            if cur.rowcount:
                print(f"  deleted {label}: {cur.rowcount}")
    print("committed")

    leftovers = [
        f"{t}.{c}={n}"
        for t, hits in scan_references(conn, seeds).items()
        for c, n in hits
    ]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print("\n--- post-check ---")
    print("  leftover references:", ", ".join(leftovers) if leftovers else "none")
    print("  integrity_check:", integrity)
    orphan_total = run_orphan_check(conn)
    conn.close()
    ok = not leftovers and integrity == "ok" and orphan_total == 0
    print("\nRESULT:", "OK" if ok else "INCOMPLETE")
    return 0 if ok else 1


def cmd_orphans(args: argparse.Namespace) -> int:
    db = Path(args.db)
    conn = connect(db)
    found = find_orphans(conn)

    print("--- orphan check (parent -> child links) ---")
    if not found:
        print("  none")
        conn.close()
        return 0
    total = print_orphans(conn, found)
    print(f"  {'TOTAL dangling rows':<44} {total}")

    selected = found
    if args.link:
        wanted = set(args.link)
        selected = [f for f in found if f"{f[2]}.{f[3]}" in wanted]
        unknown = wanted - {f"{f[2]}.{f[3]}" for f in found}
        if unknown:
            sys.exit(f"error: link(s) not currently dangling: {sorted(unknown)}")
    elif looks_synthetic(orphan_samples(conn, *found[0][:4])) and len(found) == 1:
        print("\nnothing to do — the only dangling link looks like a synthetic marker")
        conn.close()
        return 0

    if not args.delete:
        print("\ndry run — re-run with --delete --yes --link <table.column> to remove rows")
        conn.close()
        return 0
    if not args.yes:
        print("\nreport only — pass --yes together with --delete to remove them")
        conn.close()
        return 0
    if not args.link:
        available = " ".join(f"--link {f[2]}.{f[3]}" for f in found)
        sys.exit(
            "error: --delete requires an explicit --link for every relation to clean,\n"
            f"       because some dangling values are intentional synthetic markers.\n"
            f"       available: {available}"
        )

    snap = backup(conn, db, args.reason)
    print(f"\nsnapshot: {snap.name} ({snap.stat().st_size:,} bytes)")
    with conn:
        for _, _, ctable, ccol, _, sql in selected:
            cur = conn.execute(sql)
            if cur.rowcount:
                print(f"  deleted {ctable}.{ccol}: {cur.rowcount}")
    print("committed")

    remaining = find_orphans(conn)
    cleanable = [
        f for f in remaining if not looks_synthetic(orphan_samples(conn, *f[:4]))
    ]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print("\n--- post-check ---")
    print("  remaining dangling groups:", len(remaining), f"(cleanable: {len(cleanable)})")
    for ptable, pcol, ctable, ccol, n, _ in remaining:
        kind = "synthetic, kept" if looks_synthetic(orphan_samples(conn, ptable, pcol, ctable, ccol)) else "cleanable"
        print(f"    {ctable}.{ccol} -> {ptable}.{pcol}  {n}  [{kind}]")
    print("  integrity_check:", integrity)
    conn.close()
    ok = not cleanable and integrity == "ok"
    print("\nRESULT:", "OK" if ok else "INCOMPLETE")
    return 0 if ok else 1


def cmd_restore(args: argparse.Namespace) -> int:
    db = Path(args.db)
    snap = db.parent / args.backup if not Path(args.backup).is_absolute() else Path(args.backup)
    if not snap.exists():
        sys.exit(f"error: snapshot not found: {snap}")
    safety = backup(connect(db), db, f"pre-restore-{snap.stem}")
    shutil.copy2(snap, db)
    print(f"restored {db.name} from {snap.name}")
    print(f"current state saved as {safety.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"database path (default: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_target(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--id", nargs="*", default=None, help="explicit work-item ids")
        sp.add_argument("--project-id", nargs="*", default=None, help="project ids to sweep")
        sp.add_argument(
            "--scope",
            choices=SCOPES,
            default="work-item-domain",
            help="work-item-domain (default) or project-domain",
        )

    add_target(sub.add_parser("scope", help="read-only reference map and deletion plan"))
    d = sub.add_parser("delete", help="snapshot, delete the selected scope, verify")
    add_target(d)
    d.add_argument("--reason", default="delete-workitems", help="tag used in the snapshot filename")
    d.add_argument("--yes", action="store_true", help="actually execute (default is a dry run)")

    o = sub.add_parser("orphans", help="report or remove rows whose parent is gone")
    o.add_argument("--delete", action="store_true", help="remove the orphan rows")
    o.add_argument("--yes", action="store_true", help="confirm — required together with --delete")
    o.add_argument(
        "--link",
        nargs="*",
        default=None,
        help="only clean these child relations, e.g. --link prd_blocks.document_id",
    )
    o.add_argument("--reason", default="clean-orphans", help="tag used in the snapshot filename")

    r = sub.add_parser("restore", help="replace the db with a snapshot")
    r.add_argument("--backup", required=True, help="snapshot filename (or absolute path)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "scope":
        return cmd_scope(args)
    if args.cmd == "delete":
        return cmd_delete(args)
    if args.cmd == "orphans":
        return cmd_orphans(args)
    if args.cmd == "restore":
        return cmd_restore(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
