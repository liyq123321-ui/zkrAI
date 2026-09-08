"""Idempotently backfill concise summaries for project ROOT work items."""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import Project, WorkItem
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
            with self._session_factory() as db:
                root = db.get(WorkItem, root_id)
                project = db.get(Project, project_id)
                if root is None or project is None:
                    failed[project_id] = "project or ROOT work item no longer exists"
                    continue
                if self._has_summary(root.summary):
                    skipped.append(project_id)
                    continue
                payload = self._project_service._analysis_payload(db, project)

            try:
                analysis = self._project_service._validated_analysis(
                    await self._agent.analyze_brief(payload)
                )
            except Exception as error:
                failed[project_id] = str(error) or type(error).__name__
                continue

            summary = analysis.brief_updates.summary
            if summary is None:
                failed[project_id] = "PM analysis did not return a valid summary"
                continue

            with self._session_factory() as db:
                root = db.get(WorkItem, root_id)
                if root is None:
                    failed[project_id] = "ROOT work item no longer exists"
                    continue
                if self._has_summary(root.summary):
                    skipped.append(project_id)
                    continue
                root.summary = summary
                db.commit()
                updated.append(project_id)

        return SummaryBackfillResult(updated=updated, skipped=skipped, failed=failed)

    @staticmethod
    def _has_summary(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())
