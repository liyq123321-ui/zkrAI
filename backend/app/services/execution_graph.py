"""Derived execution state over the WorkItem dependency DAG.

This module holds pure functions only. Keeping them free of SQLAlchemy lets
the rules be unit tested directly and reused by both the read projection and
the command handlers, so that "what the UI is offered" and "what the command
engine accepts" can never drift apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Run ledger status -> work item status exposed to clients.
RUN_TO_ITEM_STATUS = {
    "STARTED": "in_progress",
    "SUCCEEDED": "done",
    "FAILED": "failed",
    "BLOCKED": "blocked",
}

# Actions offered per derived item status. Kept here so the projection and the
# command handlers agree on the same vocabulary.
ACTIONS_BY_ITEM_STATUS = {
    "todo": ("start_task",),
    "in_progress": ("complete_task", "fail_task"),
    "failed": ("start_task",),
    "blocked": ("start_task",),
    "done": (),
}


def collapse_runs(runs: Iterable[object]) -> dict[str, str]:
    """Collapse an execution ledger into the latest status per work item.

    The caller must order ``runs`` by ``(work_item_id, created_at, id)``; this
    function then simply keeps the last entry it sees, which makes the result
    deterministic and independent of dict/set iteration order.
    """

    status_by_item: dict[str, str] = {}
    for run in runs:
        work_item_id = getattr(run, "work_item_id", None)
        if work_item_id is None:
            continue
        status_by_item[work_item_id] = RUN_TO_ITEM_STATUS.get(
            getattr(run, "status", ""), "todo"
        )
    return status_by_item


def ready_work_item_ids(
    executable_ids: set[str],
    dependencies: Mapping[str, list[str]],
    status_by_item: Mapping[str, str],
) -> list[str]:
    """Return executable items whose dependencies are all done.

    ``dependencies`` maps an item to the items it depends on and only contains
    keys for items that actually have edges, hence the ``.get(..., ())``
    fallback: an item with no dependencies must still be ready.

    The graph is guaranteed acyclic by the decomposition validator, so this is
    a single pass rather than a topological sort -- no scheduler needs an
    ordering, only the set of runnable items.
    """

    done = {item for item, status in status_by_item.items() if status == "done"}
    return sorted(
        item_id
        for item_id in executable_ids
        if status_by_item.get(item_id, "todo") != "done"
        and set(dependencies.get(item_id, ())) <= done
    )


def startable_work_item_ids(
    executable_ids: set[str],
    dependencies: Mapping[str, list[str]],
    status_by_item: Mapping[str, str],
) -> list[str]:
    """Items that may START right now: untouched, with every dependency done.

    Distinct from :func:`ready_work_item_ids`, which also returns items already
    in progress. Both the read projection and the START handler must use this
    one so the UI can never offer a start the engine would reject.
    """

    ready = set(ready_work_item_ids(executable_ids, dependencies, status_by_item))
    return sorted(
        item_id for item_id in ready if status_by_item.get(item_id, "todo") == "todo"
    )


def available_actions(
    item_id: str,
    executable: bool,
    status: str,
    ready_ids: set[str],
) -> list[str]:
    """Per-item actions for the projection layer.

    ``legal_actions`` in ``SessionState`` is project-scoped: it can say "the
    workflow permits starting a task" but not "these specific tasks may start".
    The backend therefore computes the per-item answer and ships it, so the
    frontend keeps rendering instead of re-deriving workflow rules.
    """

    if not executable:
        return []
    if status == "todo" and item_id not in ready_ids:
        return []
    return list(ACTIONS_BY_ITEM_STATUS.get(status, ()))


def graph_depths(
    item_ids: set[str],
    dependencies: Mapping[str, list[str]],
) -> dict[str, int]:
    """Longest-path depth per item, for laying the DAG out in lanes.

    Iterative memoised DFS with an explicit stack; a cycle yields depth 0 for
    its participants instead of recursing forever.
    """

    depth: dict[str, int] = {}
    state: dict[str, int] = {item_id: 0 for item_id in item_ids}
    for root in sorted(item_ids):
        if state[root] != 0:
            continue
        frames: list[tuple[str, object]] = [(root, iter(sorted(dependencies.get(root, ()))))]
        state[root] = 1
        while frames:
            node, iterator = frames[-1]
            advanced = False
            for dependency in iterator:
                if dependency not in item_ids:
                    continue
                if state[dependency] == 0:
                    state[dependency] = 1
                    frames.append(
                        (dependency, iter(sorted(dependencies.get(dependency, ()))))
                    )
                    advanced = True
                    break
                if state[dependency] == 1:
                    continue
            if advanced:
                continue
            frames.pop()
            best = 0
            for dependency in dependencies.get(node, ()):
                if dependency in depth:
                    best = max(best, depth[dependency] + 1)
            depth[node] = best
            state[node] = 2
    return depth
