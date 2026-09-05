import json
import hashlib
import unicodedata
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings


Base = declarative_base()


def create_engine_for_url(database_url: str):
    kwargs = {"connect_args": {"check_same_thread": False}}
    if database_url == "sqlite://":
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_database(target_engine: Engine) -> None:
    from app.database import models  # noqa: F401

    if target_engine.url.get_backend_name() == "sqlite":
        database_path = target_engine.url.database
        if database_path and database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        _migrate_sqlite_clarification_integrity(target_engine)
        _migrate_sqlite_work_items(target_engine)
        _migrate_sqlite_gitea_review_contracts(target_engine)
        _migrate_sqlite_command_jobs(target_engine)
    Base.metadata.create_all(target_engine)


def _migrate_sqlite_command_jobs(target_engine: Engine) -> None:
    """Add progress fields and settle pre-index active jobs during startup."""

    schema = inspect(target_engine)
    if "command_jobs" not in set(schema.get_table_names()):
        return
    columns = {column["name"] for column in schema.get_columns("command_jobs")}
    additions = {
        "progress_stage": "VARCHAR",
        "progress_message": "TEXT",
        "last_activity_at": "DATETIME",
    }
    with target_engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE command_jobs ADD COLUMN "{name}" {sql_type}'
                    )
            now = datetime.now().isoformat(sep=" ")
            connection.execute(
                text("""
                    UPDATE command_jobs
                    SET status = 'failed',
                        status_version = status_version + 1,
                        result = NULL,
                        error_code = 'PROCESS_INTERRUPTED',
                        error_message = :message,
                        progress_stage = 'failed',
                        progress_message = :message,
                        last_activity_at = :now,
                        completed_at = :now,
                        updated_at = :now
                    WHERE status IN ('pending', 'processing')
                """),
                {
                    "message": "The background command process was interrupted; retry the command.",
                    "now": now,
                },
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _migrate_sqlite_gitea_review_contracts(target_engine: Engine) -> None:
    """Forward-migrate hashes, review actors/receipts, and command leases.

    The legacy ``versions.content_hash`` held a structured Spec hash despite
    its transport role.  Rebuilding lets us preserve that value explicitly as
    ``spec_content_hash`` while replacing ``content_hash`` with the normalized
    Markdown SHA-256 required by the Gitea binding contract.
    """

    schema = inspect(target_engine)
    names = set(schema.get_table_names())
    versions_need_rebuild = "versions" in names and "spec_content_hash" not in {
        column["name"] for column in schema.get_columns("versions")
    }
    tasks_need_rebuild = "tasks" in names and not {
        "initiator_actor_id",
        "auto_resolve_findings",
        "finding_snapshot",
        "decision_history_snapshot",
        "reply_receipts",
    } <= {column["name"] for column in schema.get_columns("tasks")}
    command_columns = (
        {column["name"] for column in schema.get_columns("command_attempts")}
        if "command_attempts" in names
        else set()
    )
    command_additions = {
        "prepare_owner_id": "VARCHAR",
        "prepare_owner_started_at": "DATETIME",
    }
    if (
        not versions_need_rebuild
        and not tasks_need_rebuild
        and not (set(command_additions) - command_columns)
    ):
        return

    with target_engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if versions_need_rebuild:
                _rebuild_sqlite_prd_versions(connection)
            if tasks_need_rebuild:
                _rebuild_sqlite_review_tasks(connection)
            for name, sql_type in command_additions.items():
                if "command_attempts" in names and name not in command_columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE command_attempts ADD COLUMN "{name}" {sql_type}'
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _rebuild_sqlite_prd_versions(connection: Connection) -> None:
    from app.database.models import utc_now

    if "spec_versions" not in set(inspect(connection).get_table_names()):
        raise RuntimeError("cannot migrate PRD versions without Spec versions")
    rows = [
        dict(row)
        for row in connection.execute(text("SELECT * FROM versions")).mappings()
    ]
    specs = {
        row["id"]: dict(row)
        for row in connection.execute(
            text("SELECT id, markdown, content_hash FROM spec_versions")
        ).mappings()
    }
    migrated = []
    for row in rows:
        spec = specs.get(row["spec_version_id"])
        if spec is None or not isinstance(spec.get("markdown"), str):
            raise RuntimeError("cannot migrate a PRD version with no matching Spec")
        markdown = unicodedata.normalize("NFC", spec["markdown"]).replace(
            "\r\n", "\n"
        ).replace("\r", "\n")
        migrated.append(
            {
                "wi": row["wi"],
                "version": row["version"],
                "spec_version_id": row["spec_version_id"],
                "filename": row["filename"],
                "pr_number": row["pr_number"],
                "commit_sha": row.get("commit_sha"),
                "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                "spec_content_hash": spec["content_hash"],
                "change_summary": row.get("change_summary"),
                "created_at": _datetime_value(row.get("created_at")) or utc_now(),
            }
        )
    connection.execute(text("DROP TABLE versions"))
    table = Base.metadata.tables["versions"]
    table.create(connection)
    if migrated:
        connection.execute(table.insert(), migrated)


def _rebuild_sqlite_review_tasks(connection: Connection) -> None:
    from app.database.models import utc_now

    rows = [
        dict(row)
        for row in connection.execute(text("SELECT * FROM tasks")).mappings()
    ]
    actors = {
        row["wi"]: row["final_approver"]
        for row in connection.execute(
            text(
                "SELECT work_items.id AS wi, projects.final_approver AS final_approver "
                "FROM work_items JOIN projects ON projects.id = work_items.project_id"
            )
        ).mappings()
    }
    migrated = []
    for row in rows:
        actor = row.get("initiator_actor_id") or actors.get(row["wi"])
        if not isinstance(actor, str) or not actor:
            raise RuntimeError("cannot migrate a review task without an initiating actor")
        migrated.append(
            {
                "id": row["id"],
                "wi": row["wi"],
                "initiator_actor_id": actor,
                "status": row["status"],
                "base_version": row["base_version"],
                "base_commit_sha": row["base_commit_sha"],
                "comment_ids": _json_value(row["comment_ids"]),
                "comment_snapshot": _json_value(row["comment_snapshot"]),
                "comment_snapshot_hash": row["comment_snapshot_hash"],
                "auto_resolve_findings": bool(row.get("auto_resolve_findings", False)),
                "finding_snapshot": _json_value(row.get("finding_snapshot") or []),
                "decision_history_snapshot": _json_value(
                    row.get("decision_history_snapshot") or []
                ),
                "reply_receipts": _json_value(row.get("reply_receipts") or {}),
                "new_version": row.get("new_version"),
                "new_spec_version_id": row.get("new_spec_version_id"),
                "new_commit_sha": row.get("new_commit_sha"),
                "error_code": row.get("error_code"),
                "error": row.get("error"),
                "created_at": _datetime_value(row.get("created_at")) or utc_now(),
                "updated_at": _datetime_value(row.get("updated_at")) or utc_now(),
            }
        )
    connection.execute(text("DROP TABLE tasks"))
    table = Base.metadata.tables["tasks"]
    table.create(connection)
    if migrated:
        connection.execute(table.insert(), migrated)


def _migrate_sqlite_work_items(target_engine: Engine) -> None:
    """Add the project workflow columns to the pre-PRD work_items table in place."""

    schema = inspect(target_engine)
    if "work_items" not in set(schema.get_table_names()):
        return
    existing_columns = {column["name"] for column in schema.get_columns("work_items")}
    has_project_local_uniqueness = any(
        set(constraint.get("column_names") or []) == {"project_id", "local_key"}
        for constraint in schema.get_unique_constraints("work_items")
    ) or any(
        bool(index.get("unique"))
        and set(index.get("column_names") or []) == {"project_id", "local_key"}
        for index in schema.get_indexes("work_items")
    )
    additions = {
        "project_id": "VARCHAR",
        "local_key": "VARCHAR",
        "kind": "VARCHAR",
        "executable": "BOOLEAN",
        "objective": "TEXT",
        "scope": "JSON",
        "exclusions": "JSON",
        "inputs": "JSON",
        "outputs": "JSON",
        "acceptance_criteria": "JSON",
        "required_skills": "JSON",
        "responsible_role": "VARCHAR",
        "suggested_assignee": "VARCHAR",
    }
    with target_engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for name, sql_type in additions.items():
                if name not in existing_columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE work_items ADD COLUMN "{name}" {sql_type}'
                    )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_work_items_project_id ON work_items (project_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_work_items_session_id ON work_items (session_id)"
            )
            if not has_project_local_uniqueness:
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_work_item_local_key "
                    "ON work_items (project_id, local_key)"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _migrate_sqlite_clarification_integrity(target_engine: Engine) -> None:
    """Rebuild legacy clarification tables with durable logical uniqueness.

    SQLite cannot drop the former ``(project_id, spec_version_id)`` unique
    constraint or add required columns in place.  Rebuilding both related
    tables also lets us retain every legacy answer while assigning one primary
    response slot and deterministic legacy slots to historical duplicates.
    """

    schema = inspect(target_engine)
    table_names = set(schema.get_table_names())
    request_name = "clarification_requests"
    response_name = "clarification_responses"
    if request_name not in table_names:
        return

    request_columns = {column["name"] for column in schema.get_columns(request_name)}
    request_constraints = {
        constraint["name"] for constraint in schema.get_unique_constraints(request_name)
    }
    response_columns = (
        {column["name"] for column in schema.get_columns(response_name)}
        if response_name in table_names
        else set()
    )
    response_constraints = (
        {
            constraint["name"]
            for constraint in schema.get_unique_constraints(response_name)
        }
        if response_name in table_names
        else set()
    )
    requests_are_current = (
        "boundary_key" in request_columns
        and "uq_clarification_boundary_round" in request_constraints
        and "uq_spec_clarification_request" not in request_constraints
    )
    responses_are_current = (
        response_name in table_names
        and "response_slot" in response_columns
        and "uq_clarification_response_slot" in response_constraints
    )
    if requests_are_current and responses_are_current:
        return

    with target_engine.connect() as connection:
        # Python's sqlite3 driver does not begin a transaction for DDL in its
        # legacy transaction mode.  Issue BEGIN IMMEDIATE at the driver level
        # before any SELECT/DROP/CREATE so every schema and data-copy step is
        # covered by the same rollback-capable SQLite transaction.  It also
        # prevents another writer from changing the source during migration.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            _rebuild_sqlite_clarification_tables(
                connection,
                request_name=request_name,
                response_name=response_name,
                response_table_exists=response_name in table_names,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _rebuild_sqlite_clarification_tables(
    connection: Connection,
    *,
    request_name: str,
    response_name: str,
    response_table_exists: bool,
) -> None:
    request_rows = [
        dict(row)
        for row in connection.execute(text(f"SELECT * FROM {request_name}")).mappings()
    ]
    response_rows = (
        [
            dict(row)
            for row in connection.execute(text(f"SELECT * FROM {response_name}")).mappings()
        ]
        if response_table_exists
        else []
    )

    if response_table_exists:
        connection.execute(text(f"DROP TABLE {response_name}"))
    connection.execute(text(f"DROP TABLE {request_name}"))

    request_table = Base.metadata.tables[request_name]
    response_table = Base.metadata.tables[response_name]
    request_table.create(connection)
    response_table.create(connection)

    migrated_requests = _migrated_clarification_requests(request_rows)
    migrated_responses = _migrated_clarification_responses(response_rows)
    if migrated_requests:
        connection.execute(request_table.insert(), migrated_requests)
    if migrated_responses:
        connection.execute(response_table.insert(), migrated_responses)


def _json_value(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _datetime_value(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _migrated_clarification_requests(rows: list[dict]) -> list[dict]:
    from app.database.models import clarification_boundary_key, utc_now

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        boundary = clarification_boundary_key(row.get("spec_version_id"))
        row["boundary_key"] = boundary
        groups.setdefault((row["project_id"], boundary), []).append(row)

    migrated: list[dict] = []
    for grouped_rows in groups.values():
        ordered = sorted(
            grouped_rows,
            key=lambda row: (
                int(row.get("analysis_round") or 1),
                str(row.get("created_at") or ""),
                row["id"],
            ),
        )
        original_rounds = [int(row.get("analysis_round") or 1) for row in ordered]
        next_round = max(original_rounds, default=0) + 1
        used_rounds: set[int] = set()
        for row in ordered:
            analysis_round = int(row.get("analysis_round") or 1)
            if analysis_round in used_rounds:
                while next_round in used_rounds:
                    next_round += 1
                analysis_round, next_round = next_round, next_round + 1
            used_rounds.add(analysis_round)
            migrated.append(
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "spec_version_id": row.get("spec_version_id"),
                    "boundary_key": row["boundary_key"],
                    "questions": _json_value(row["questions"]),
                    "analysis_round": analysis_round,
                    "blocking": bool(row.get("blocking", True)),
                    "agent_session_id": row.get("agent_session_id"),
                    "agent_call_id": row.get("agent_call_id"),
                    "created_at": _datetime_value(row.get("created_at")) or utc_now(),
                }
            )
    return migrated


def _migrated_clarification_responses(rows: list[dict]) -> list[dict]:
    from app.database.models import utc_now

    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["clarification_request_id"], []).append(row)
    migrated: list[dict] = []
    for grouped_rows in groups.values():
        ordered = sorted(
            grouped_rows,
            key=lambda row: (str(row.get("created_at") or ""), row["id"]),
        )
        for ordinal, row in enumerate(ordered, start=1):
            migrated.append(
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "clarification_request_id": row["clarification_request_id"],
                    "response_slot": (
                        "PRIMARY" if ordinal == 1 else f"LEGACY:{ordinal}:{row['id']}"
                    ),
                    "actor_id": row["actor_id"],
                    "answers": _json_value(row["answers"]),
                    "created_at": _datetime_value(row.get("created_at")) or utc_now(),
                }
            )
    return migrated


_settings = Settings.from_env()
engine = create_engine_for_url(_settings.database_url)
SessionLocal = make_session_factory(engine)
