"""Durable human draw.io edits that become immutable reviewed Spec revisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import (
    AgentCall,
    AgentSession,
    AuditEvent,
    PrdVersion,
    Project,
    ReviewTask,
    SpecVersion,
    WorkItem,
)
from app.domain.types import ProjectPhase, ProjectSpecPayload, SpecStatus, WorkItemKind
from app.services.drawio_diagrams import normalize_drawio_xml
from app.services.spec_service import SpecService, _canonical_hash, _render_markdown


DIAGRAM_REVISION_SNAPSHOT_TYPE = "DRAWIO_REVISION"


@dataclass(frozen=True, slots=True)
class DiagramRevisionSnapshot:
    diagram_id: str
    drawio_xml: str
    change_summary: str

    def as_list(self) -> list[dict[str, str]]:
        return [
            {
                "snapshot_type": DIAGRAM_REVISION_SNAPSHOT_TYPE,
                "diagram_id": self.diagram_id,
                "drawio_xml": self.drawio_xml,
                "change_summary": self.change_summary,
            }
        ]

    def digest(self) -> str:
        canonical = json.dumps(
            self.as_list(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diagram_revision_snapshot(task: ReviewTask) -> DiagramRevisionSnapshot | None:
    raw = task.comment_snapshot
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        return None
    item = raw[0]
    if item.get("snapshot_type") != DIAGRAM_REVISION_SNAPSHOT_TYPE:
        return None
    try:
        snapshot = DiagramRevisionSnapshot(
            diagram_id=str(item["diagram_id"]),
            drawio_xml=str(item["drawio_xml"]),
            change_summary=str(item["change_summary"]),
        )
    except KeyError:
        return None
    if snapshot.as_list() != raw or snapshot.digest() != task.comment_snapshot_hash:
        return None
    return snapshot


def is_diagram_revision_task(task: ReviewTask | None) -> bool:
    return task is not None and diagram_revision_snapshot(task) is not None


class DiagramRevisionService:
    """Materialize and automatically review one claimed diagram-edit task."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        agent: AgentGateway,
    ) -> None:
        self._session_factory = session_factory
        self._specs = SpecService(session_factory, agent)

    async def materialize_and_review(self, task_id: str) -> str:
        project_id, version_id = self._materialize(task_id)
        reviewed = await self._specs._run_automatic_review(project_id, version_id)
        if reviewed.status == SpecStatus.AUTO_REVIEW.value:
            raise RuntimeError("diagram revision automatic review did not complete")
        return reviewed.id

    def _materialize(self, task_id: str) -> tuple[str, str]:
        with self._session_factory() as db:
            with db.begin():
                task = db.get(ReviewTask, task_id)
                snapshot = diagram_revision_snapshot(task) if task is not None else None
                item = db.get(WorkItem, task.wi) if task is not None else None
                project = db.get(Project, item.project_id) if item is not None else None
                binding = (
                    db.get(PrdVersion, (task.wi, task.base_version))
                    if task is not None
                    else None
                )
                parent = (
                    db.get(SpecVersion, binding.spec_version_id)
                    if binding is not None
                    else None
                )
                current = (
                    db.get(SpecVersion, project.current_spec_version_id)
                    if project is not None and project.current_spec_version_id
                    else None
                )
                if (
                    task is None
                    or task.status != "processing"
                    or snapshot is None
                    or task.comment_ids != []
                    or task.finding_snapshot != []
                    or task.decision_history_snapshot != []
                    or task.reply_receipts != {}
                    or item is None
                    or item.kind != WorkItemKind.ROOT.value
                    or item.parent_id is not None
                    or project is None
                    or binding is None
                    or parent is None
                    or binding.commit_sha != task.base_commit_sha
                    or binding.spec_content_hash != parent.content_hash
                ):
                    raise RuntimeError("diagram revision task base is inconsistent")

                if task.new_spec_version_id is not None:
                    revised = db.get(SpecVersion, task.new_spec_version_id)
                    if (
                        revised is None
                        or current is None
                        or current.id != revised.id
                        or revised.parent_version_id != parent.id
                        or task.new_version != revised.revision
                        or revised.generation_source != "HUMAN_DRAWIO_EDIT"
                    ):
                        raise RuntimeError("diagram revision recovery state is inconsistent")
                    return project.id, revised.id

                if (
                    current is None
                    or current.id != parent.id
                    or parent.revision != task.base_version
                    or parent.status not in {
                        SpecStatus.HUMAN_REVIEW.value,
                        SpecStatus.REWORK.value,
                    }
                ):
                    raise RuntimeError("diagram revision base is stale")

                parent_payload = ProjectSpecPayload.model_validate(parent.content)
                matches = [
                    (index, diagram)
                    for index, diagram in enumerate(parent_payload.er_diagrams)
                    if diagram.diagram_id == snapshot.diagram_id
                ]
                if len(matches) != 1:
                    raise RuntimeError("diagram revision target is unavailable")
                index, original = matches[0]
                normalized = normalize_drawio_xml(snapshot.drawio_xml)
                revised_diagram = original.model_copy(update={"drawio_xml": normalized})
                diagram_values = list(parent_payload.er_diagrams)
                diagram_values[index] = revised_diagram
                revised_payload = ProjectSpecPayload.model_validate(
                    parent_payload.model_copy(update={"er_diagrams": diagram_values}).model_dump(mode="json")
                )
                content = revised_payload.model_dump(mode="json")
                content_hash = _canonical_hash(content)
                if content_hash == parent.content_hash:
                    raise RuntimeError("diagram revision unexpectedly produced no change")

                agent_session = (
                    db.query(AgentSession)
                    .filter_by(project_id=project.id, role="HUMAN_DRAWIO_EDITOR")
                    .one_or_none()
                )
                if agent_session is None:
                    agent_session = AgentSession(
                        id=_new_id(),
                        project_id=project.id,
                        role="HUMAN_DRAWIO_EDITOR",
                        purpose="Record authenticated draw.io diagram revisions",
                        metadata_json={},
                    )
                    db.add(agent_session)
                    db.flush()
                old_hash = hashlib.sha256(original.drawio_xml.encode("utf-8")).hexdigest()
                new_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                request = {
                    "task_id": task.id,
                    "snapshot_hash": task.comment_snapshot_hash,
                    "base_version": task.base_version,
                    "base_commit_sha": task.base_commit_sha,
                    "diagram_id": snapshot.diagram_id,
                    "old_diagram_hash": old_hash,
                    "new_diagram_hash": new_hash,
                    "change_summary": snapshot.change_summary,
                    "authenticated_revision_decision": {
                        "actor_id": task.initiator_actor_id,
                        "authority": "AUTHORIZED_PROJECT_REVIEWER",
                        "instruction": snapshot.change_summary,
                    },
                }
                generation_call = AgentCall(
                    id=_new_id(),
                    project_id=project.id,
                    agent_session_id=agent_session.id,
                    operation="diagram_edit",
                    request=request,
                    response=content,
                    status="SUCCEEDED",
                    completed_at=_now(),
                )
                latest = (
                    db.query(SpecVersion.revision)
                    .filter_by(project_id=project.id)
                    .order_by(desc(SpecVersion.revision))
                    .first()
                )
                revision = (latest[0] if latest is not None else 0) + 1
                revised = SpecVersion(
                    id=_new_id(),
                    project_id=project.id,
                    revision=revision,
                    content=content,
                    markdown=_render_markdown(content),
                    generation_source="HUMAN_DRAWIO_EDIT",
                    input_refs=list(parent.input_refs),
                    generator_agent_session_id=agent_session.id,
                    generator_call_id=generation_call.id,
                    parent_version_id=parent.id,
                    change_summary=snapshot.change_summary,
                    content_hash=content_hash,
                    status=SpecStatus.AUTO_REVIEW.value,
                )
                task.new_version = revision
                task.new_spec_version_id = revised.id
                project.current_spec_version_id = revised.id
                project.phase = ProjectPhase.REVIEW.value
                project.state_version += 1
                db.add_all(
                    [
                        generation_call,
                        revised,
                        AuditEvent(
                            id=_new_id(),
                            project_id=project.id,
                            session_id=project.session_id,
                            event_type="ER_DIAGRAM_REVISION_CREATED",
                            actor_id=task.initiator_actor_id,
                            payload={
                                "review_task_id": task.id,
                                "parent_version_id": parent.id,
                                "spec_version_id": revised.id,
                                "diagram_id": snapshot.diagram_id,
                                "old_diagram_hash": old_hash,
                                "new_diagram_hash": new_hash,
                                "change_summary": snapshot.change_summary,
                            },
                        ),
                    ]
                )
            return project.id, revised.id


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)
