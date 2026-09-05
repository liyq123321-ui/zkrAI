import warnings
from datetime import UTC, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database.database import Base, create_engine_for_url, init_database, make_session_factory
from app.database import database as database_module
from app.database.models import ProcessedCommand, Project, SpecVersion, WorkItem


def test_mvp_schema_contains_authoritative_records(engine):
    init_database(engine)

    names = set(inspect(engine).get_table_names())

    assert {
        "projects",
        "agent_sessions",
        "agent_calls",
        "artifacts",
        "clarification_requests",
        "clarification_responses",
        "spec_versions",
        "spec_reviews",
        "work_items",
        "work_item_dependencies",
        "agent_specs",
        "audit_events",
        "processed_commands",
        "versions",
        "comments_index",
        "tasks",
    } <= names


def test_prd_review_tables_have_required_columns_and_uniqueness(engine):
    """The external Gitea projection must reject duplicate versions and snapshots."""

    init_database(engine)
    schema = inspect(engine)

    version_column_meta = {
        item["name"]: item for item in schema.get_columns("versions")
    }
    version_columns = set(version_column_meta)
    comment_columns = {
        item["name"]: item for item in schema.get_columns("comments_index")
    }
    task_column_meta = {item["name"]: item for item in schema.get_columns("tasks")}
    task_columns = set(task_column_meta)
    version_unique = {
        frozenset(item.get("column_names") or [])
        for item in schema.get_unique_constraints("versions")
    }
    task_unique = {
        frozenset(item.get("column_names") or [])
        for item in schema.get_unique_constraints("tasks")
    }

    assert {
        "wi", "version", "spec_version_id", "filename", "pr_number",
        "commit_sha", "content_hash", "spec_content_hash", "change_summary", "created_at",
    } <= version_columns
    assert {
        "id", "wi", "pr_number", "path", "line", "author_type", "body",
        "resolved", "created_at", "updated_at",
    } <= set(comment_columns)
    assert schema.get_pk_constraint("comments_index")["constrained_columns"] == ["id"]
    assert comment_columns["updated_at"]["nullable"] is False
    assert {
        "id", "wi", "initiator_actor_id", "status", "base_version", "base_commit_sha",
        "comment_ids", "comment_snapshot", "comment_snapshot_hash",
        "auto_resolve_findings", "finding_snapshot",
        "decision_history_snapshot",
        "reply_receipts",
        "new_version", "new_spec_version_id", "new_commit_sha",
        "error_code", "error", "created_at", "updated_at",
    } <= task_columns
    assert frozenset({"wi", "version"}) in version_unique
    assert frozenset({"spec_version_id"}) in version_unique
    assert frozenset({"wi", "base_version", "comment_snapshot_hash"}) in task_unique
    assert version_column_meta["spec_content_hash"]["nullable"] is False
    assert task_column_meta["initiator_actor_id"]["nullable"] is False
    assert task_column_meta["auto_resolve_findings"]["nullable"] is False
    assert task_column_meta["finding_snapshot"]["nullable"] is False
    assert task_column_meta["decision_history_snapshot"]["nullable"] is False
    assert task_column_meta["reply_receipts"]["nullable"] is False
    command_columns = {
        item["name"] for item in schema.get_columns("command_attempts")
    }
    assert {"prepare_owner_id", "prepare_owner_started_at"} <= command_columns


def test_command_jobs_have_durable_status_and_idempotent_identity(engine):
    init_database(engine)
    schema = inspect(engine)

    columns = {item["name"]: item for item in schema.get_columns("command_jobs")}
    unique = {
        frozenset(item.get("column_names") or [])
        for item in schema.get_unique_constraints("command_jobs")
    }

    assert {
        "id", "project_id", "session_id", "command_id", "input_hash",
        "request_payload", "status", "status_version", "result",
        "error_code", "error_message", "created_at", "started_at",
        "completed_at", "updated_at", "progress_stage", "progress_message",
        "last_activity_at",
    } <= set(columns)
    assert frozenset({"session_id", "command_id"}) in unique
    assert columns["status"]["nullable"] is False
    assert columns["status_version"]["nullable"] is False


def test_existing_command_jobs_gain_progress_columns_idempotently(tmp_path):
    target = create_engine_for_url(f"sqlite:///{tmp_path / 'legacy-command-jobs.sqlite'}")
    try:
        with target.begin() as connection:
            connection.execute(text("""
                CREATE TABLE command_jobs (
                    id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL,
                    session_id VARCHAR NOT NULL, command_id VARCHAR NOT NULL,
                    input_hash VARCHAR(64) NOT NULL, request_payload JSON NOT NULL,
                    status VARCHAR NOT NULL, status_version INTEGER NOT NULL,
                    result JSON, error_code VARCHAR, error_message TEXT,
                    created_at DATETIME NOT NULL, started_at DATETIME,
                    completed_at DATETIME, updated_at DATETIME NOT NULL,
                    UNIQUE (session_id, command_id)
                )
            """))
            connection.execute(text("""
                INSERT INTO command_jobs VALUES (
                    'job-1', 'project-1', 'session-1', 'command-1', 'hash', '{}',
                    'processing', 2, NULL, NULL, NULL,
                    '2026-01-01 00:00:00', '2026-01-01 00:00:01', NULL,
                    '2026-01-01 00:00:01'
                )
            """))

        init_database(target)
        init_database(target)

        columns = {item["name"] for item in inspect(target).get_columns("command_jobs")}
        with target.connect() as connection:
            row = connection.execute(text(
                "SELECT status, error_code, progress_stage FROM command_jobs"
            )).one()
        assert {"progress_stage", "progress_message", "last_activity_at"} <= columns
        assert row == ("failed", "PROCESS_INTERRUPTED", "failed")
    finally:
        target.dispose()


def test_legacy_review_contracts_migrate_hash_actor_receipts_and_command_owner_idempotently(
    tmp_path,
):
    """Existing bindings/tasks retain provenance while adopting corrected contracts."""
    target = create_engine_for_url(f"sqlite:///{tmp_path / 'legacy-review.sqlite'}")
    markdown = "# Cafe\u0301\r\n"
    structured_hash = "s" * 64
    try:
        with target.begin() as connection:
            connection.execute(text("""
                CREATE TABLE projects (
                    id VARCHAR PRIMARY KEY, final_approver VARCHAR NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE work_items (
                    id VARCHAR PRIMARY KEY, project_id VARCHAR, session_id VARCHAR,
                    parent_id VARCHAR, type VARCHAR, department VARCHAR, title VARCHAR,
                    description TEXT, spec TEXT, status VARCHAR
                )
            """))
            connection.execute(text("""
                CREATE TABLE spec_versions (
                    id VARCHAR PRIMARY KEY, markdown TEXT NOT NULL, content_hash VARCHAR(64) NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE versions (
                    wi VARCHAR NOT NULL, version INTEGER NOT NULL,
                    spec_version_id VARCHAR NOT NULL, filename TEXT NOT NULL,
                    pr_number INTEGER NOT NULL, commit_sha VARCHAR,
                    content_hash VARCHAR(64) NOT NULL, change_summary TEXT,
                    created_at DATETIME, PRIMARY KEY (wi, version),
                    UNIQUE (spec_version_id)
                )
            """))
            connection.execute(text("""
                CREATE TABLE tasks (
                    id VARCHAR PRIMARY KEY, wi VARCHAR NOT NULL, status VARCHAR NOT NULL,
                    base_version INTEGER NOT NULL, base_commit_sha VARCHAR NOT NULL,
                    comment_ids JSON NOT NULL, comment_snapshot JSON NOT NULL,
                    comment_snapshot_hash VARCHAR(64) NOT NULL,
                    new_version INTEGER, new_spec_version_id VARCHAR,
                    new_commit_sha VARCHAR, error_code VARCHAR, error TEXT,
                    created_at DATETIME, updated_at DATETIME,
                    UNIQUE (wi, base_version, comment_snapshot_hash)
                )
            """))
            connection.execute(text("""
                CREATE TABLE command_attempts (
                    id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL,
                    session_id VARCHAR NOT NULL, command_id VARCHAR NOT NULL,
                    input_hash VARCHAR(64) NOT NULL, expected_state_version INTEGER NOT NULL,
                    action VARCHAR NOT NULL, status VARCHAR NOT NULL,
                    prepared_payload JSON, agent_call_ids JSON NOT NULL,
                    error TEXT, created_at DATETIME, prepared_at DATETIME, updated_at DATETIME,
                    UNIQUE (session_id, command_id)
                )
            """))
            connection.execute(text("INSERT INTO projects VALUES ('p', 'owner')"))
            connection.execute(text("""
                INSERT INTO work_items
                    (id, project_id, session_id, parent_id, type, department, title,
                     description, spec, status)
                VALUES ('root', 'p', 's', NULL, 'root', NULL, 'Root', NULL, NULL, 'todo')
            """))
            connection.execute(
                text("INSERT INTO spec_versions VALUES ('spec-1', :markdown, :hash)"),
                {"markdown": markdown, "hash": structured_hash},
            )
            connection.execute(text("""
                INSERT INTO versions VALUES
                    ('root', 1, 'spec-1', 'docs/prd/root/v1.md', 7, 'commit',
                     'legacy-structured-hash', 'Initial', '2026-01-01 00:00:00')
            """))
            connection.exec_driver_sql("""
                INSERT INTO tasks VALUES
                    ('task-1', 'root', 'error', 1, 'commit', '[10]',
                     '[{"id":10}]', 'digest', NULL, NULL, NULL, 'X', 'failed',
                     '2026-01-01 00:00:00', '2026-01-01 00:00:01')
            """)
            connection.execute(text("""
                INSERT INTO command_attempts VALUES
                    ('attempt', 'p', 's', 'cmd', 'hash', 1, 'message', 'PREPARING',
                     NULL, '[]', NULL, '2026-01-01 00:00:00', NULL,
                     '2026-01-01 00:00:00')
            """))

        init_database(target)
        init_database(target)

        with target.connect() as connection:
            version = connection.execute(
                text("SELECT content_hash, spec_content_hash FROM versions")
            ).one()
            task = connection.execute(
                text(
                    "SELECT initiator_actor_id, auto_resolve_findings, "
                    "finding_snapshot, decision_history_snapshot, reply_receipts FROM tasks"
                )
            ).one()
        expected_markdown = "# Caf\u00e9\n"
        assert version == (
            hashlib.sha256(expected_markdown.encode()).hexdigest(),
            structured_hash,
        )
        assert task[0] == "owner"
        assert bool(task[1]) is False
        assert json.loads(task[2]) == []
        assert json.loads(task[3]) == []
        assert json.loads(task[4]) == {}
        assert {"prepare_owner_id", "prepare_owner_started_at"} <= {
            item["name"] for item in inspect(target).get_columns("command_attempts")
        }
    finally:
        target.dispose()


def test_schema_rejects_duplicate_spec_revisions(db_session):
    db_session.add_all(
        [
            SpecVersion(
                id="spec-1",
                project_id="project-1",
                revision=1,
                content={},
                markdown="",
                generation_source="PM",
                input_refs=[],
                generator_agent_session_id="agent-session-1",
                generator_call_id="agent-call-1",
                change_summary="Initial version",
                content_hash="a" * 64,
            ),
            SpecVersion(
                id="spec-2",
                project_id="project-1",
                revision=1,
                content={},
                markdown="",
                generation_source="PM",
                input_refs=[],
                generator_agent_session_id="agent-session-1",
                generator_call_id="agent-call-2",
                change_summary="Conflicting version",
                content_hash="b" * 64,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_schema_rejects_duplicate_processed_command_ids(db_session):
    db_session.add(Project(
        id="project-1",
        session_id="session-1",
        creation_request_id="request-1",
        brief={},
        final_approver="approver-1",
    ))
    db_session.flush()
    db_session.add_all(
        [
            ProcessedCommand(
                id="command-receipt-1",
                session_id="session-1",
                command_id="command-1",
                input_hash="a" * 64,
                state_version=0,
                result={},
                side_effect_refs=[],
            ),
            ProcessedCommand(
                id="command-receipt-2",
                session_id="session-1",
                command_id="command-1",
                input_hash="b" * 64,
                state_version=1,
                result={},
                side_effect_refs=[],
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_timestamp_defaults_are_aware_utc_without_utcnow_warnings(db_session):
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        project = Project(
            id="project-utc",
            session_id="session-utc",
            creation_request_id="request-utc",
            brief={},
            final_approver="approver-utc",
        )
        db_session.add(project)
        db_session.flush()

    utcnow_warnings = [
        warning
        for warning in caught_warnings
        if issubclass(warning.category, DeprecationWarning)
        and "datetime.utcnow" in str(warning.message)
    ]

    assert utcnow_warnings == []
    assert project.created_at.tzinfo is UTC
    assert project.created_at.utcoffset() == timedelta(0)
    assert project.updated_at.tzinfo is UTC
    assert project.updated_at.utcoffset() == timedelta(0)
    assert all(
        column.type.timezone is True
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_at")
    )


def test_import_main_does_not_create_sqlite_path_but_database_init_does(tmp_path):
    """Importing application modules must not mutate the configured filesystem."""
    database_path = tmp_path / "fresh-parent" / "workflow.sqlite"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    project_root = Path(__file__).resolve().parents[2]

    imported = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert imported.returncode == 0, imported.stderr
    assert not database_path.parent.exists()

    initialized = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.database.database import engine, init_database; init_database(engine)",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert initialized.returncode == 0, initialized.stderr
    assert database_path.exists()


def test_legacy_work_items_migrate_idempotently_and_allow_project_intake(tmp_path):
    """An existing pre-PRD work_items table must not break the first new intake write."""
    import asyncio
    from collections import deque

    from app.domain.types import ClarificationAnalysis
    from app.schemas.workflow import SessionCreateRequest
    from app.services.project_service import ProjectService
    from tests.helpers.factories import make_complete_brief
    from tests.helpers.fake_agent import ScriptedAgentGateway

    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'legacy-work-items.sqlite'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE work_items (
                id VARCHAR NOT NULL PRIMARY KEY,
                session_id VARCHAR,
                parent_id VARCHAR,
                type VARCHAR,
                department VARCHAR,
                title VARCHAR,
                description TEXT,
                spec TEXT,
                status VARCHAR
            )
        """))
        connection.execute(
            text("""
                INSERT INTO work_items
                    (id, session_id, parent_id, type, department, title, description, spec, status)
                VALUES
                    ('legacy-wi', 'legacy-session', NULL, 'task', 'ops', 'Legacy', 'keep me', '{}', 'todo')
            """)
        )

    init_database(engine)
    init_database(engine)

    columns = {item["name"] for item in inspect(engine).get_columns("work_items")}
    assert {
        "project_id", "local_key", "kind", "executable", "objective", "scope",
        "exclusions", "inputs", "outputs", "acceptance_criteria", "required_skills",
        "responsible_role", "suggested_assignee",
    } <= columns
    indexes = {item["name"]: item for item in inspect(engine).get_indexes("work_items")}
    assert indexes["uq_work_item_local_key"]["unique"] == 1
    assert indexes["ix_work_items_project_id"]["column_names"] == ["project_id"]
    assert indexes["ix_work_items_session_id"]["column_names"] == ["session_id"]
    with engine.connect() as connection:
        legacy = connection.execute(
            text("SELECT title, description, department FROM work_items WHERE id='legacy-wi'")
        ).one()
    assert legacy == ("Legacy", "keep me", "ops")

    factory = make_session_factory(engine)
    brief = make_complete_brief()
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )
    state = asyncio.run(
        ProjectService(factory, agent).create_session(
            SessionCreateRequest(request_id="post-migration-intake", actor_id="owner", brief=brief)
        )
    )
    assert state.phase.value == "SPECIFICATION"
    with factory() as db:
        assert db.query(Project).count() == 1
        assert db.query(WorkItem).filter_by(kind="ROOT").count() == 1
    engine.dispose()


def test_fresh_schema_enforces_clarification_round_and_primary_response_slots(engine):
    """Fresh databases must reject duplicate logical rounds and duplicate primary answers."""
    init_database(engine)
    schema = inspect(engine)
    request_columns = {item["name"]: item for item in schema.get_columns("clarification_requests")}
    response_columns = {item["name"]: item for item in schema.get_columns("clarification_responses")}
    request_constraints = {item["name"] for item in schema.get_unique_constraints("clarification_requests")}
    response_constraints = {item["name"] for item in schema.get_unique_constraints("clarification_responses")}

    assert request_columns["boundary_key"]["nullable"] is False
    assert response_columns["response_slot"]["nullable"] is False
    assert "uq_clarification_boundary_round" in request_constraints
    assert "uq_clarification_response_slot" in response_constraints

    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO clarification_requests
            (id, project_id, spec_version_id, boundary_key, questions, analysis_round, blocking, created_at)
            VALUES ('fresh-request', 'fresh-project', 'fresh-spec', 'SPEC:fresh-spec', '[]', 1, 1, '2026-01-01 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO clarification_responses
            (id, project_id, clarification_request_id, response_slot, actor_id, answers, created_at)
            VALUES ('fresh-answer', 'fresh-project', 'fresh-request', 'PRIMARY', 'owner', '{}', '2026-01-01 00:00:00')
        """))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO clarification_requests
                (id, project_id, spec_version_id, boundary_key, questions, analysis_round, blocking, created_at)
                VALUES ('duplicate-request', 'fresh-project', 'fresh-spec', 'SPEC:fresh-spec', '[]', 1, 1, '2026-01-02 00:00:00')
            """))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO clarification_responses
                (id, project_id, clarification_request_id, response_slot, actor_id, answers, created_at)
                VALUES ('duplicate-answer', 'fresh-project', 'fresh-request', 'PRIMARY', 'owner', '{}', '2026-01-02 00:00:00')
            """))


def test_init_database_migrates_legacy_clarifications_idempotently_without_loss(tmp_path):
    """The SQLite forward migration must preserve every legacy request and answer."""
    target = create_engine_for_url(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    try:
        with target.begin() as connection:
            connection.execute(text("""
                CREATE TABLE clarification_requests (
                    id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    spec_version_id VARCHAR,
                    questions JSON NOT NULL,
                    analysis_round INTEGER NOT NULL DEFAULT 1,
                    blocking BOOLEAN NOT NULL DEFAULT 1,
                    agent_session_id VARCHAR,
                    agent_call_id VARCHAR,
                    created_at DATETIME,
                    CONSTRAINT uq_spec_clarification_request UNIQUE (project_id, spec_version_id)
                )
            """))
            connection.execute(text("""
                CREATE TABLE clarification_responses (
                    id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    clarification_request_id VARCHAR NOT NULL,
                    actor_id VARCHAR NOT NULL,
                    answers JSON NOT NULL,
                    created_at DATETIME
                )
            """))
            requests = [
                {"id": "legacy-intake", "project": "p1", "spec": None, "round": 1},
                {"id": "legacy-intake-duplicate-round", "project": "p1", "spec": None, "round": 1},
                {"id": "legacy-spec", "project": "p1", "spec": "s1", "round": 1},
            ]
            for item in requests:
                connection.execute(
                    text("""
                        INSERT INTO clarification_requests
                        (id, project_id, spec_version_id, questions, analysis_round, blocking, created_at)
                        VALUES (:id, :project, :spec, :questions, :round, 1, '2026-01-01 00:00:00')
                    """),
                    {**item, "questions": json.dumps([{"question_id": item["id"]}])},
                )
            for response_id, answer in (("legacy-answer-a", "A"), ("legacy-answer-b", "B")):
                connection.execute(
                    text("""
                        INSERT INTO clarification_responses
                        (id, project_id, clarification_request_id, actor_id, answers, created_at)
                        VALUES (:id, 'p1', 'legacy-spec', 'owner', :answers, '2026-01-01 00:00:00')
                    """),
                    {"id": response_id, "answers": json.dumps({"message": answer})},
                )

        init_database(target)
        init_database(target)

        schema = inspect(target)
        assert "uq_spec_clarification_request" not in {
            item["name"] for item in schema.get_unique_constraints("clarification_requests")
        }
        assert "uq_clarification_boundary_round" in {
            item["name"] for item in schema.get_unique_constraints("clarification_requests")
        }
        with target.begin() as connection:
            request_rows = connection.execute(
                text("SELECT id, boundary_key, analysis_round FROM clarification_requests ORDER BY id")
            ).mappings().all()
            response_rows = connection.execute(
                text("SELECT id, answers, response_slot FROM clarification_responses ORDER BY id")
            ).mappings().all()
            assert {item["id"] for item in request_rows} == {
                "legacy-intake",
                "legacy-intake-duplicate-round",
                "legacy-spec",
            }
            assert sorted(
                item["analysis_round"]
                for item in request_rows
                if item["boundary_key"] == "INTAKE"
            ) == [1, 2]
            assert {item["id"] for item in response_rows} == {"legacy-answer-a", "legacy-answer-b"}
            assert {item["response_slot"] for item in response_rows} == {
                "PRIMARY",
                "LEGACY:2:legacy-answer-b",
            }
            assert {json.loads(item["answers"])["message"] for item in response_rows} == {"A", "B"}
            connection.execute(text("""
                INSERT INTO clarification_requests
                (id, project_id, spec_version_id, boundary_key, questions, analysis_round, blocking, created_at)
                VALUES ('legacy-spec-round-2', 'p1', 's1', 'SPEC:s1', '[]', 2, 1, '2026-01-02 00:00:00')
            """))
        with pytest.raises(IntegrityError):
            with target.begin() as connection:
                connection.execute(text("""
                    INSERT INTO clarification_requests
                    (id, project_id, spec_version_id, boundary_key, questions, analysis_round, blocking, created_at)
                    VALUES ('duplicate-round-2', 'p1', 's1', 'SPEC:s1', '[]', 2, 1, '2026-01-03 00:00:00')
                """))
    finally:
        target.dispose()


def test_sqlite_clarification_migration_failure_rolls_back_schema_and_every_row(
    tmp_path, monkeypatch
):
    """DDL and data copy must share one real SQLite transaction."""
    target = create_engine_for_url(f"sqlite:///{tmp_path / 'atomic-legacy.sqlite'}")
    original_requests = [
        {
            "id": "atomic-intake-a",
            "project_id": "atomic-project",
            "spec_version_id": None,
            "questions": json.dumps(
                [{"question_id": "INTAKE-A", "blocking": False, "tags": ["α", 17]}]
            ),
            "analysis_round": 7,
            "blocking": 0,
            "agent_session_id": None,
            "agent_call_id": "intake-call-sentinel",
            "created_at": "2026-02-03 04:05:06.123456",
        },
        {
            "id": "atomic-intake-b",
            "project_id": "atomic-project",
            "spec_version_id": None,
            "questions": json.dumps(
                [{"question_id": "INTAKE-B", "meta": {"attempt": 2, "valid": True}}]
            ),
            "analysis_round": 7,
            "blocking": 1,
            "agent_session_id": "intake-agent-session-sentinel",
            "agent_call_id": None,
            "created_at": "2026-02-04 05:06:07.234567",
        },
        {
            "id": "atomic-spec",
            "project_id": "atomic-project",
            "spec_version_id": "atomic-spec-v1",
            "questions": json.dumps(
                [{"question_id": "SPEC-A", "options": ["keep", "replace"]}]
            ),
            "analysis_round": 13,
            "blocking": 0,
            "agent_session_id": "spec-agent-session-sentinel",
            "agent_call_id": "spec-agent-call-sentinel",
            "created_at": "2026-03-05 06:07:08.345678",
        },
    ]
    original_responses = [
        {
            "id": "atomic-answer-a",
            "project_id": "atomic-project",
            "clarification_request_id": "atomic-spec",
            "actor_id": "owner-a-sentinel",
            "answers": json.dumps({"message": "A", "approved": False, "rank": 3}),
            "created_at": "2026-04-06 07:08:09.456789",
        },
        {
            "id": "atomic-answer-b",
            "project_id": "atomic-project",
            "clarification_request_id": "atomic-spec",
            "actor_id": "owner-b-sentinel",
            "answers": json.dumps({"message": "B", "details": ["x", "y"]}),
            "created_at": "2026-04-07 08:09:10.567890",
        },
        {
            "id": "atomic-answer-intake",
            "project_id": "atomic-project",
            "clarification_request_id": "atomic-intake-a",
            "actor_id": "intake-owner-sentinel",
            "answers": json.dumps({"message": "Intake", "locations": ["上海", "Paris"]}),
            "created_at": "2026-04-08 09:10:11.678901",
        },
    ]

    def raw_snapshot(connection):
        request_rows = tuple(connection.exec_driver_sql("""
            SELECT * FROM clarification_requests ORDER BY id
        """).all())
        response_rows = tuple(connection.exec_driver_sql("""
            SELECT * FROM clarification_responses ORDER BY id
        """).all())
        schema_rows = tuple(connection.exec_driver_sql("""
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index')
              AND (
                tbl_name IN ('clarification_requests', 'clarification_responses')
                OR name LIKE '%clarification%'
              )
            ORDER BY type, name
        """).all())
        return request_rows, response_rows, schema_rows

    try:
        with target.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        with target.begin() as connection:
            connection.execute(text("""
                CREATE TABLE clarification_requests (
                    id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    spec_version_id VARCHAR,
                    questions JSON NOT NULL,
                    analysis_round INTEGER NOT NULL DEFAULT 1,
                    blocking BOOLEAN NOT NULL DEFAULT 1,
                    agent_session_id VARCHAR,
                    agent_call_id VARCHAR,
                    created_at DATETIME,
                    CONSTRAINT uq_spec_clarification_request UNIQUE (project_id, spec_version_id)
                )
            """))
            connection.execute(text("""
                CREATE TABLE clarification_responses (
                    id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    clarification_request_id VARCHAR NOT NULL,
                    actor_id VARCHAR NOT NULL,
                    answers JSON NOT NULL,
                    created_at DATETIME
                )
            """))
            connection.execute(text("""
                CREATE INDEX ix_clarification_requests_project_id
                ON clarification_requests (project_id)
            """))
            connection.execute(text("""
                CREATE INDEX ix_clarification_requests_spec_version_id
                ON clarification_requests (spec_version_id)
            """))
            connection.execute(text("""
                CREATE INDEX ix_clarification_responses_project_id
                ON clarification_responses (project_id)
            """))
            connection.execute(text("""
                CREATE INDEX ix_clarification_responses_clarification_request_id
                ON clarification_responses (clarification_request_id)
            """))
            connection.execute(text("""
                INSERT INTO clarification_requests
                (id, project_id, spec_version_id, questions, analysis_round, blocking,
                 agent_session_id, agent_call_id, created_at)
                VALUES
                (:id, :project_id, :spec_version_id, :questions, :analysis_round, :blocking,
                 :agent_session_id, :agent_call_id, :created_at)
            """), original_requests)
            connection.execute(text("""
                INSERT INTO clarification_responses
                (id, project_id, clarification_request_id, actor_id, answers, created_at)
                VALUES
                (:id, :project_id, :clarification_request_id, :actor_id, :answers, :created_at)
            """), original_responses)

        with target.connect() as connection:
            original_snapshot = raw_snapshot(connection)
        assert {row.name for row in original_snapshot[2]} >= {
            "clarification_requests",
            "clarification_responses",
            "ix_clarification_requests_project_id",
            "ix_clarification_requests_spec_version_id",
            "ix_clarification_responses_project_id",
            "ix_clarification_responses_clarification_request_id",
        }
        assert any(
            row.type == "index" and row.name.startswith("sqlite_autoindex_clarification_requests")
            for row in original_snapshot[2]
        )

        original_transform = database_module._migrated_clarification_responses

        def fail_after_new_tables_are_created(rows):
            raise RuntimeError("injected copy failure")

        monkeypatch.setattr(
            database_module,
            "_migrated_clarification_responses",
            fail_after_new_tables_are_created,
        )
        with pytest.raises(RuntimeError, match="injected copy failure"):
            init_database(target)

        with target.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            failed_snapshot = raw_snapshot(connection)
        assert failed_snapshot == original_snapshot
        assert not any(
            "temp" in row.name.lower() or "shadow" in row.name.lower()
            for row in failed_snapshot[2]
        )
        failed_schema = inspect(target)
        assert "boundary_key" not in {
            item["name"] for item in failed_schema.get_columns("clarification_requests")
        }
        assert "response_slot" not in {
            item["name"] for item in failed_schema.get_columns("clarification_responses")
        }
        assert "uq_spec_clarification_request" in {
            item["name"]
            for item in failed_schema.get_unique_constraints("clarification_requests")
        }

        monkeypatch.setattr(
            database_module,
            "_migrated_clarification_responses",
            original_transform,
        )
        init_database(target)

        migrated_schema = inspect(target)
        request_constraints = {
            item["name"]
            for item in migrated_schema.get_unique_constraints("clarification_requests")
        }
        response_constraints = {
            item["name"]
            for item in migrated_schema.get_unique_constraints("clarification_responses")
        }
        assert "uq_spec_clarification_request" not in request_constraints
        assert "uq_clarification_boundary_round" in request_constraints
        assert "uq_clarification_response_slot" in response_constraints
        with target.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            migrated_requests = connection.execute(text("""
                SELECT id, project_id, spec_version_id, questions, analysis_round, blocking,
                       agent_session_id, agent_call_id, created_at, boundary_key
                FROM clarification_requests ORDER BY id
            """)).all()
            migrated_responses = connection.execute(text("""
                SELECT id, project_id, clarification_request_id, actor_id, answers,
                       created_at, response_slot
                FROM clarification_responses ORDER BY id
            """)).all()
            migrated_master = raw_snapshot(connection)[2]

        expected_request_rows = []
        expected_rounds = {
            "atomic-intake-a": 7,
            "atomic-intake-b": 8,
            "atomic-spec": 13,
        }
        for source in sorted(original_requests, key=lambda row: row["id"]):
            expected_request_rows.append(
                (
                    source["id"],
                    source["project_id"],
                    source["spec_version_id"],
                    source["questions"],
                    expected_rounds[source["id"]],
                    source["blocking"],
                    source["agent_session_id"],
                    source["agent_call_id"],
                    source["created_at"],
                    (
                        f"SPEC:{source['spec_version_id']}"
                        if source["spec_version_id"]
                        else "INTAKE"
                    ),
                )
            )
        assert migrated_requests == expected_request_rows

        expected_slots = {
            "atomic-answer-a": "PRIMARY",
            "atomic-answer-b": "LEGACY:2:atomic-answer-b",
            "atomic-answer-intake": "PRIMARY",
        }
        expected_response_rows = [
            (
                source["id"],
                source["project_id"],
                source["clarification_request_id"],
                source["actor_id"],
                source["answers"],
                source["created_at"],
                expected_slots[source["id"]],
            )
            for source in sorted(original_responses, key=lambda row: row["id"])
        ]
        assert migrated_responses == expected_response_rows
        assert "uq_spec_clarification_request" not in "\n".join(
            row.sql or "" for row in migrated_master
        )
        assert {row.name for row in migrated_master} >= {
            "clarification_requests",
            "clarification_responses",
            "ix_clarification_requests_project_id",
            "ix_clarification_requests_spec_version_id",
            "ix_clarification_responses_project_id",
            "ix_clarification_responses_clarification_request_id",
        }
        assert sum(
            row.type == "index"
            and row.name.startswith("sqlite_autoindex_clarification_requests")
            for row in migrated_master
        ) >= 2
        assert sum(
            row.type == "index"
            and row.name.startswith("sqlite_autoindex_clarification_responses")
            for row in migrated_master
        ) >= 2
        assert not any(
            "temp" in row.name.lower() or "shadow" in row.name.lower()
            for row in migrated_master
        )

        with target.connect() as connection:
            first_success_snapshot = raw_snapshot(connection)
        init_database(target)
        with target.connect() as connection:
            assert raw_snapshot(connection) == first_success_snapshot
        with pytest.raises(IntegrityError):
            with target.begin() as connection:
                connection.execute(text("""
                    INSERT INTO clarification_requests
                    (id, project_id, spec_version_id, boundary_key, questions, analysis_round, blocking, created_at)
                    VALUES ('atomic-duplicate-round', 'atomic-project', 'atomic-spec-v1', 'SPEC:atomic-spec-v1', '[]', 13, 1, '2026-01-02 00:00:00')
                """))
        with pytest.raises(IntegrityError):
            with target.begin() as connection:
                connection.execute(text("""
                    INSERT INTO clarification_responses
                    (id, project_id, clarification_request_id, response_slot, actor_id, answers, created_at)
                    VALUES ('atomic-duplicate-answer', 'atomic-project', 'atomic-spec', 'PRIMARY', 'owner', '{}', '2026-01-02 00:00:00')
                """))
    finally:
        target.dispose()
