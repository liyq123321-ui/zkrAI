"""Idempotently backfill concise summaries for project ROOT work items."""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import Project, WorkItem
from app.domain.types import ProjectBriefUpdates
from app.services.project_service import ProjectService


@dataclass(frozen=True)
class SummaryBackfillResult:
    updated: list[str]
    skipped: list[str]
    failed: dict[str, str]


class ProjectSummaryBackfill:
    """Generate only missing ROOT summaries without changing project briefs."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        agent: AgentGateway,
    ) -> None:
        self._session_factory = session_factory
        self._agent = agent
        self._project_service = ProjectService(session_factory, agent)

    async def run(self) -> SummaryBackfillResult:
        updated: list[str] = []
        skipped: list[str] = []
        failed: dict[str, str] = {}

        with self._session_factory() as db:
            roots = (
                db.query(WorkItem.project_id, WorkItem.id)
                .filter(WorkItem.kind == "ROOT", WorkItem.local_key == "root")
                .order_by(WorkItem.project_id, WorkItem.id)
                .all()
            )

        for project_id, root_id in roots:
            try:
                with self._session_factory() as db:
                    root = db.get(WorkItem, root_id)
                    project = db.get(Project, project_id)
                    if root is None or project is None:
                        failed[project_id] = "project or ROOT work item no longer exists"
                        continue
                    if self._is_valid_summary(root.summary):
                        skipped.append(project_id)
                        continue
                    payload = self._project_service._analysis_payload(db, project)
            except Exception as error:
                failed[project_id] = self._error_message(error)
                continue

            try:
                analysis = self._project_service._validated_analysis(
                    await self._agent.analyze_brief(payload)
                )
            except Exception as error:
                failed[project_id] = self._error_message(error)
                continue

            summary = analysis.brief_updates.summary
            if summary is None:
                failed[project_id] = "PM analysis did not return a valid summary"
                continue

            try:
                with self._session_factory() as db:
                    root = db.get(WorkItem, root_id)
                    if root is None:
                        failed[project_id] = "ROOT work item no longer exists"
                        continue
                    if self._is_valid_summary(root.summary):
                        skipped.append(project_id)
                        continue
                    observed_summary = root.summary
                    expected_summary = (
                        WorkItem.summary.is_(None)
                        if observed_summary is None
                        else WorkItem.summary == observed_summary
                    )
                    write = db.execute(
                        update(WorkItem)
                        .where(
                            WorkItem.id == root_id,
                            WorkItem.project_id == project_id,
                            WorkItem.kind == "ROOT",
                            WorkItem.local_key == "root",
                            expected_summary,
                        )
                        .values(summary=summary)
                        .execution_options(synchronize_session=False)
                    )
                    if write.rowcount == 1:
                        db.commit()
                        updated.append(project_id)
                        continue
                    db.rollback()
            except Exception as error:
                failed[project_id] = self._error_message(error)
                continue

            with self._session_factory() as db:
                current = db.get(WorkItem, root_id)
                if current is not None and self._is_valid_summary(current.summary):
                    skipped.append(project_id)
                else:
                    failed[project_id] = "ROOT summary changed before update"

        return SummaryBackfillResult(updated=updated, skipped=skipped, failed=failed)

    @staticmethod
    def _is_valid_summary(value: object) -> bool:
        if not isinstance(value, str):
            return False
        return ProjectBriefUpdates(summary=value).summary == value

    @staticmethod
    def _error_message(error: Exception) -> str:
        return str(error) or type(error).__name__
