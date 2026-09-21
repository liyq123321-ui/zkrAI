"""One-off cleanup: remove three root work items and their plan children.

Scope (confirmed with the user): the work-item domain only. The owning
projects, PRD documents, spec versions, sessions and audit history are KEPT.

Tables touched (all keyed by the 10 work-item ids):
    work_items, work_item_dependencies, agent_specs, versions,
    comments_index, work_item_runs, tasks

A consistent snapshot is written first via sqlite3's backup API, so the
operation is reversible by restoring the .db file.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(r"C:\Users\zkr\Documents\ChatGPT\firstFlight Project\backend\data\gateway.db")
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
BACKUP = DB.with_name(f"gateway-before-delete-workitems-3roots-{STAMP}.db")

PROJECTS = [
    "73ccaabe-7647-441d-9697-0228f45dfb87",
    "6b6eaef3-c58b-4aee-91a0-4bbb73c2988f",
    "88e8e445-6fbe-4f5a-8dc9-494744b0953f",
]

# (table, column) pairs that carry a work-item foreign key.
TARGETS = [
    ("work_items", "id"),
    ("work_item_dependencies", "from_work_item_id"),
    ("work_item_dependencies", "to_work_item_id"),
    ("agent_specs", "work_item_id"),
    ("versions", "wi"),
    ("comments_index", "wi"),
    ("work_item_runs", "work_item_id"),
    ("tasks", "wi"),
]


def qmarks(n: int) -> str:
    return ",".join("?" * n)


def main() -> int:
    src = sqlite3.connect(DB)

    # --- resolve the work-item id set from the three projects ---------------
    ids = [
        r[0]
        for r in src.execute(
            f"select id from work_items where project_id in ({qmarks(len(PROJECTS))})",
            PROJECTS,
        )
    ]
    print(f"work-item ids resolved: {len(ids)}")
    for i in ids:
        print("   ", i)

    # --- 1. consistent snapshot --------------------------------------------
    dst = sqlite3.connect(BACKUP)
    with dst:
        src.backup(dst)
    dst.close()
    print(f"\nbackup written: {BACKUP.name} ({BACKUP.stat().st_size:,} bytes)")

    # verify the snapshot opens and holds the same row count
    chk = sqlite3.connect(BACKUP)
    n_src = src.execute("select count(*) from work_items").fetchone()[0]
    n_bak = chk.execute("select count(*) from work_items").fetchone()[0]
    chk.close()
    print(f"backup integrity: work_items src={n_src} backup={n_bak} -> {'OK' if n_src == n_bak else 'MISMATCH'}")
    if n_src != n_bak:
        print("ABORT: backup verification failed")
        return 1

    # --- 2. count what will go --------------------------------------------
    plan: list[tuple[str, int]] = []
    for table, col in TARGETS:
        n = src.execute(
            f'select count(*) from "{table}" where "{col}" in ({qmarks(len(ids))})', ids
        ).fetchone()[0]
        if n:
            plan.append((f"{table}.{col}", n))

    # distinct rows for dependency edges (a row may match both columns)
    dep = src.execute(
        f'select count(*) from work_item_dependencies '
        f'where from_work_item_id in ({qmarks(len(ids))}) or to_work_item_id in ({qmarks(len(ids))})',
        ids + ids,
    ).fetchone()[0]
    plan = [(k, v) for k, v in plan if not k.startswith("work_item_dependencies")]
    if dep:
        plan.append(("work_item_dependencies (distinct rows)", dep))

    print("\n--- rows to delete ---")
    total = 0
    for label, n in plan:
        print(f"  {label:<44} {n}")
        total += n
    print(f"  {'TOTAL':<44} {total}")

    # --- 3. delete inside one transaction ----------------------------------
    with src:
        src.execute(
            f'delete from work_item_dependencies '
            f'where from_work_item_id in ({qmarks(len(ids))}) or to_work_item_id in ({qmarks(len(ids))})',
            ids + ids,
        )
        for table, col in TARGETS:
            cur = src.execute(
                f'delete from "{table}" where "{col}" in ({qmarks(len(ids))})', ids
            )
            if cur.rowcount:
                print(f"  deleted {table}.{col}: {cur.rowcount}")
    print("\ntransaction committed")

    # --- 4. verify ---------------------------------------------------------
    print("\n--- post-check ---")
    leftovers = []
    for table, col in TARGETS:
        n = src.execute(
            f'select count(*) from "{table}" where "{col}" in ({qmarks(len(ids))})', ids
        ).fetchone()[0]
        if n:
            leftovers.append(f"{table}.{col}={n}")
    dep_left = src.execute(
        f'select count(*) from work_item_dependencies '
        f'where from_work_item_id in ({qmarks(len(ids))}) or to_work_item_id in ({qmarks(len(ids))})',
        ids + ids,
    ).fetchone()[0]
    print("  leftover references:", ", ".join(leftovers) if leftovers else "none")
    print("  leftover dependency edges:", dep_left)

    print("\n--- preserved (must be untouched) ---")
    for table, col in (("projects", "id"), ("spec_versions", "project_id"), ("prd_documents", "project_id"), ("agent_runtime_events", "project_id"), ("audit_events", "project_id")):
        n = src.execute(
            f'select count(*) from "{table}" where "{col}" in ({qmarks(len(PROJECTS))})', PROJECTS
        ).fetchone()[0]
        print(f"  {table}.{col}: {n}")

    src.close()
    ok = not leftovers and dep_left == 0
    print("\nRESULT:", "OK" if ok else "INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
