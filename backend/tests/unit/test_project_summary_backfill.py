from collections import deque

import pytest
from sqlalchemy import event, update

from app.database.database import Base, create_engine_for_url, make_session_factory
from app.database.models import Project, WorkItem
from app.domain.types import ClarificationAnalysis, ProjectBriefUpdates, ProjectPhase
from app.services.project_summary_backfill import ProjectSummaryBackfill


class BackfillAgent:
    def __init__(self, results):
        self.results = deque(results)
        self.payloads: list[dict[str, object]] = []

    async def analyze_brief(self, payload):
        self.payloads.append(payload)
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def _analysis(summary: object, **updates: object) -> ClarificationAnalysis:
    return ClarificationAnalysis(
        ready_for_spec=True,
        questions=[],
        assumptions=[],
        brief_updates={"summary": summary, **updates},
    )


def _add_project(
    db_session,
    project_id: str,
    *,
    summary: str | None = None,
    kind: str = "ROOT",
) -> tuple[Project, WorkItem]:
    project = Project(
        id=project_id,
        session_id=f"session-{project_id}",
        creation_request_id=f"request-{project_id}",
        brief={"title": f"Title for {project_id}", "motivation": "Keep this unchanged"},
        final_approver="owner-1",
        project_manager_ids=["owner-1"],
        root_owner_ids=["owner-1"],
        phase=ProjectPhase.INTAKE.value,
        state_version=0,
    )
    root = WorkItem(
        id=f"root-{project_id}",
        session_id=project.session_id,
        project_id=project.id,
        local_key="root",
        kind=kind,
        title=f"Original title for {project_id}",
        objective=f"Original objective for {project_id}",
        summary=summary,
    )
    db_session.add_all([project, root])
    db_session.commit()
    return project, root


@pytest.mark.asyncio
async def test_backfill_updates_missing_roots_independently_and_is_idempotent(
    session_factory, db_session
):
    existing_project, existing_root = _add_project(
        db_session, "project-a-existing", summary="已有摘要"
    )
    missing_project, missing_root = _add_project(db_session, "project-b-missing")
    failing_project, failing_root = _add_project(db_session, "project-c-failing")
    agent = BackfillAgent(
        [
            _analysis("补齐后的摘要"),
            RuntimeError("agent unavailable"),
            RuntimeError("agent still unavailable"),
        ]
    )
    service = ProjectSummaryBackfill(session_factory, agent)

    first = await service.run()
    second = await service.run()

    assert first.updated == [missing_project.id]
    assert first.skipped == [existing_project.id]
    assert list(first.failed) == [failing_project.id]
    assert second.updated == []
    assert second.skipped == [existing_project.id, missing_project.id]
    assert list(second.failed) == [failing_project.id]
    assert [payload["brief"]["title"] for payload in agent.payloads] == [
        "Title for project-b-missing",
        "Title for project-c-failing",
        "Title for project-c-failing",
    ]
    with session_factory() as db:
        stored_missing = db.get(WorkItem, missing_root.id)
        assert stored_missing.summary == "补齐后的摘要"
        assert stored_missing.title == "Original title for project-b-missing"
        assert stored_missing.objective == "Original objective for project-b-missing"
        assert db.get(WorkItem, existing_root.id).summary == "已有摘要"
        assert db.get(WorkItem, failing_root.id).summary is None


@pytest.mark.asyncio
async def test_backfill_rejects_missing_or_invalid_summary_without_mutating_project(
    session_factory, db_session
):
    missing_project, missing_root = _add_project(db_session, "project-invalid")
    original_brief = dict(missing_project.brief)
    agent = BackfillAgent(
        [
            ClarificationAnalysis(
                ready_for_spec=False,
                questions=[
                    {
                        "question_id": "Q-1",
                        "question": "Should this be ignored?",
                        "reason": "Only the summary is relevant.",
                        "affected_areas": ["scope"],
                        "blocking": True,
                    }
                ],
                assumptions=["This must not be persisted."],
                brief_updates=ProjectBriefUpdates(
                    summary="超过二十个中文字符的项目摘要不应该被回填到项目根任务中",
                    motivation="This must also be ignored.",
                ),
            )
        ]
    )

    result = await ProjectSummaryBackfill(session_factory, agent).run()

    assert result.updated == []
    assert result.skipped == []
    assert list(result.failed) == [missing_project.id]
    with session_factory() as db:
        assert db.get(WorkItem, missing_root.id).summary is None
        assert db.get(Project, missing_project.id).brief == original_brief


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored_summary",
    [
        " 前后存在空格 ",
        "包含\n换行",
        "超过二十个中文字符的存量项目摘要必须重新生成后才有效",
    ],
)
async def test_backfill_replaces_invalid_stored_summary(
    session_factory, db_session, stored_summary
):
    project, root = _add_project(
        db_session, "project-invalid-stored", summary=stored_summary
    )
    agent = BackfillAgent([_analysis("有效项目摘要")])

    result = await ProjectSummaryBackfill(session_factory, agent).run()

    assert result.updated == [project.id]
    assert result.skipped == []
    assert result.failed == {}
    with session_factory() as db:
        assert db.get(WorkItem, root.id).summary == "有效项目摘要"


@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_summary_populated_during_agent_call(
    tmp_path,
):
    target_engine = create_engine_for_url(f"sqlite:///{tmp_path / 'concurrent.sqlite'}")
    Base.metadata.create_all(target_engine)
    target_session_factory = make_session_factory(target_engine)
    with target_session_factory() as db:
        project, root = _add_project(db, "project-concurrent")

    competing_summary = "写入边界的并发摘要"
    competing_write_done = False

    def write_competing_summary_at_update_boundary(
        connection, cursor, statement, parameters, context, executemany
    ):
        nonlocal competing_write_done
        if competing_write_done or not statement.lstrip().upper().startswith(
            "UPDATE WORK_ITEMS"
        ):
            return
        competing_write_done = True
        with target_engine.begin() as competing_connection:
            competing_connection.execute(
                update(WorkItem)
                .where(WorkItem.id == root.id)
                .values(summary=competing_summary)
            )

    event.listen(
        target_engine, "before_cursor_execute", write_competing_summary_at_update_boundary
    )

    class ConcurrentUpdateAgent:
        async def analyze_brief(self, payload):
            return _analysis("AI生成摘要")

    try:
        result = await ProjectSummaryBackfill(
            target_session_factory, ConcurrentUpdateAgent()
        ).run()
    finally:
        event.remove(
            target_engine,
            "before_cursor_execute",
            write_competing_summary_at_update_boundary,
        )

    assert competing_write_done is True
    assert result.updated == []
    assert result.skipped == [project.id]
    assert result.failed == {}
    with target_session_factory() as db:
        assert db.get(WorkItem, root.id).summary == competing_summary


@pytest.mark.asyncio
async def test_backfill_payload_failure_does_not_stop_later_project(
    session_factory, db_session, monkeypatch
):
    failed_project, failed_root = _add_project(db_session, "project-a-payload-fails")
    later_project, later_root = _add_project(db_session, "project-b-later")
    agent = BackfillAgent([_analysis("后续项目摘要")])
    service = ProjectSummaryBackfill(session_factory, agent)
    real_analysis_payload = service._project_service._analysis_payload

    def build_payload(db, project):
        if project.id == failed_project.id:
            raise RuntimeError("payload construction failed")
        return real_analysis_payload(db, project)

    monkeypatch.setattr(service._project_service, "_analysis_payload", build_payload)

    result = await service.run()

    assert result.updated == [later_project.id]
    assert result.skipped == []
    assert result.failed == {failed_project.id: "payload construction failed"}
    with session_factory() as db:
        assert db.get(WorkItem, failed_root.id).summary is None
        assert db.get(WorkItem, later_root.id).summary == "后续项目摘要"


@pytest.mark.asyncio
async def test_backfill_commit_failure_does_not_stop_later_project(
    session_factory, db_session
):
    failed_project, failed_root = _add_project(db_session, "project-a-commit-fails")
    later_project, later_root = _add_project(db_session, "project-b-later")
    agent = BackfillAgent([_analysis("首次写入摘要"), _analysis("后续项目摘要")])
    fail_next_commit = True

    def fail_first_commit(session):
        nonlocal fail_next_commit
        if fail_next_commit:
            fail_next_commit = False
            raise RuntimeError("summary commit failed")

    event.listen(session_factory.class_, "before_commit", fail_first_commit)
    try:
        result = await ProjectSummaryBackfill(session_factory, agent).run()
    finally:
        event.remove(session_factory.class_, "before_commit", fail_first_commit)

    assert result.updated == [later_project.id]
    assert result.skipped == []
    assert result.failed == {failed_project.id: "summary commit failed"}
    with session_factory() as db:
        assert db.get(WorkItem, failed_root.id).summary is None
        assert db.get(WorkItem, later_root.id).summary == "后续项目摘要"


@pytest.mark.asyncio
async def test_backfill_ignores_non_root_work_items(session_factory, db_session):
    project, task = _add_project(db_session, "project-task-only", kind="TASK")
    agent = BackfillAgent([])

    result = await ProjectSummaryBackfill(session_factory, agent).run()

    assert result.updated == []
    assert result.skipped == []
    assert result.failed == {}
    assert agent.payloads == []
    with session_factory() as db:
        assert db.get(WorkItem, task.id).summary is None
