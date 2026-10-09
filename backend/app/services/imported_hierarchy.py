"""Give an imported PRD the heading hierarchy its file format lost.

A DOCX saved without Heading styles, a PDF, or a document whose titles are plain
paragraphs all arrive flat. Importing them verbatim keeps the text but leaves the
reader with a wall of section-sized Blocks, so one Agent call decides which of
the document's own lines are headings and at what level.

The Agent never returns document text. It only picks positions, and the service
re-splits the original paragraphs around them, so this pass cannot rewrite,
drop or invent content. When the call fails the verbatim import is simply kept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.agents.gateway import AgentGateway
from app.database.models import AgentCall, AgentSession, Project
from app.domain.imported_layout import ImportedHeadingPlan
from app.services.block_service import BlockError, BlockService
from app.services.imported_prd_layout import heading_digest, resolve_heading_map


def _now() -> datetime:
    return datetime.now(UTC)


class ImportedHierarchyService:
    def __init__(self, blocks: BlockService, session_factory, agent: AgentGateway) -> None:
        self.blocks = blocks
        self.factory = session_factory
        self.agent = agent

    async def structure(self, document_id: str, actor: str, expected_revision: int):
        context = self._context(document_id, actor)
        try:
            plan = ImportedHeadingPlan.model_validate(
                await self.agent.structure_imported_prd(context["payload"])
            )
        except Exception as error:
            self._finish(context["call_id"], "FAILED", error=str(error))
            raise BlockError(
                "Agent 未能给出分层结果，已保留按段落整体导入的原文：" + str(error)[:200], 502
            ) from error

        headings = resolve_heading_map(context["content"], plan.headings)
        if not headings:
            self._finish(context["call_id"], "FAILED", error="empty heading map")
            raise BlockError("Agent 没有选出可用的章节标题，已保留按段落整体导入的原文。", 502)

        snapshot = self.blocks.import_source(
            document_id, actor, expected_revision, replace=True, headings=headings
        )
        self._finish(context["call_id"], "SUCCEEDED", response={
            "headings": len(headings), "summary": plan.summary,
        })
        return {**snapshot, "organized": {"headings": len(headings), "summary": plan.summary}}

    def _context(self, document_id: str, actor: str) -> dict:
        with self.factory() as db:
            document = self.blocks.document(db, document_id, actor)
            project = db.get(Project, document.project_id)
            brief = dict(project.brief or {}) if project is not None else {}
            source = brief.get("source_prd")
            content = str(source.get("content") or "") if isinstance(source, dict) else ""
            if not content.strip():
                raise BlockError("这个项目没有导入的原始 PRD 文件", 422)
            if document.submitted_spec_id:
                raise BlockError("PRD 已冻结，不能再重排分层")
            digest = heading_digest(content)
            if not digest:
                raise BlockError("原文里没有可以用作章节标题的独立段落", 422)
            session = db.query(AgentSession).filter_by(project_id=document.project_id).first()
            payload = {
                "project_id": document.project_id,
                "document_title": document.title,
                "source_filename": str(source.get("filename") or ""),
                "heading_candidates": digest,
            }
            call = AgentCall(
                id=str(uuid4()), project_id=document.project_id,
                agent_session_id=session.id if session is not None else None,
                operation="structure_imported_prd", request=payload, status="PENDING",
            )
            db.add(call)
            db.commit()
            return {"payload": payload, "content": content, "call_id": call.id}

    def _finish(self, call_id: str, status: str, *, response=None, error: str | None = None) -> None:
        with self.factory() as db:
            call = db.get(AgentCall, call_id)
            if call is None:
                return
            call.status = status
            call.error = (error or "")[:2000] or None
            call.response = response
            call.completed_at = _now()
            db.commit()
