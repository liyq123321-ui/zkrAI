"""Versioned Project Spec generation and hybrid automatic review."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import (
    AgentCall,
    AgentSession,
    Artifact,
    AuditEvent,
    ClarificationRequest,
    ClarificationResponse,
    Project,
    SpecReview,
    SpecVersion,
    clarification_boundary_key,
)
from app.domain.types import ErDiagram, ProjectPhase, ProjectSpecPayload, ReviewKind, ReviewVerdict, SemanticReview, SpecStatus
from app.services.drawio_diagrams import diagram_anchor, diagram_review_projection
from app.services.spec_review import merge_review_outcome, prevents_semantic_review, run_rule_review


class SpecGenerationNotAllowed(Exception):
    """Raised when a project cannot generate a new Spec in its current state."""


class SpecRevisionNotAllowed(Exception):
    """Raised when a project does not have a rework Spec to revise."""


class AgentCallInDoubt(RuntimeError):
    """A persisted external Agent call may have completed, but its result is unavailable."""


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compact_spec_diagrams(spec: dict[str, object]) -> dict[str, object]:
    compact = dict(spec)
    diagrams = spec.get("er_diagrams")
    if isinstance(diagrams, list):
        compact["er_diagrams"] = [
            {
                "diagram_id": diagram.diagram_id,
                "title": diagram.title,
                "after_section": diagram.after_section.value,
                "semantic_projection": diagram_review_projection(diagram.drawio_xml),
            }
            for diagram in (ErDiagram.model_validate(item) for item in diagrams)
        ]
    return compact


def _compact_generation_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    compact = dict(snapshot)
    parent_spec = compact.get("parent_spec")
    if isinstance(parent_spec, dict):
        compact["parent_spec"] = _compact_spec_diagrams(parent_spec)
    return compact


def _render_markdown(content: dict[str, object]) -> str:
    """Render canonical, stable Markdown from a JSON-compatible Spec payload."""

    sections = (
        ("Background and Goals", "background_and_goals"),
        ("Users and Scenarios", "users_and_scenarios"),
        ("Functional Requirements", "functional_requirements"),
        ("Non-functional Requirements", "non_functional_requirements"),
        ("System Boundaries", "system_boundaries"),
        ("Exclusions", "exclusions"),
        ("Fixed Parts", "fixed_parts"),
        ("Configurable Parts", "configurable_parts"),
        ("Extension Points", "extension_points"),
        ("Core Objects", "core_objects"),
        ("Main Flows", "main_flows"),
        ("Exceptional Flows", "exceptional_flows"),
        ("Permissions and Responsibilities", "permissions_and_responsibilities"),
        ("Deliverable Requirements", "deliverable_requirements"),
        ("Acceptance Criteria", "acceptance_criteria"),
        ("Risks", "risks"),
        ("Assumptions", "assumptions"),
        ("Open Questions", "open_questions"),
        ("Source References", "source_refs"),
    )
    lines = ["# Project Spec"]
    diagrams_by_section: dict[str, list[ErDiagram]] = {}
    for diagram in content.get("er_diagrams", []):
        if isinstance(diagram, dict):
            parsed = ErDiagram.model_validate(diagram)
            diagrams_by_section.setdefault(parsed.after_section.value, []).append(parsed)
    for title, key in sections:
        lines.extend(("", f"## {title}"))
        values = content.get(key, [])
        if not values:
            lines.append("- None")
        else:
            for value in values:
                if isinstance(value, dict):
                    lines.append(f"- {json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}")
                else:
                    lines.append(f"- {value}")
        for diagram in diagrams_by_section.get(key, []):
            escaped_title = (
                diagram.title.replace("\\", "\\\\")
                .replace("[", "\\[")
                .replace("]", "\\]")
            )
            lines.extend(("", f"[ER 图：{escaped_title}](#{diagram_anchor(diagram)})"))
    return "\n".join(lines) + "\n"


class SpecService:
    """Create immutable Spec revisions and advance their automatic review gate."""

    def __init__(self, session_factory: Callable[[], Session], agent: AgentGateway) -> None:
        self._session_factory = session_factory
        self._agent = agent

    async def create_spec(self, project_id: str) -> SpecVersion:
        """Generate revision one or resume an unfinished automatic review."""

        with self._session_factory() as db:
            project = self._project(db, project_id)
            current = self._current_spec(db, project)
            if (
                project.phase == ProjectPhase.REVIEW.value
                and current is not None
                and current.status == SpecStatus.AUTO_REVIEW.value
            ):
                version_id = current.id
            elif project.phase == ProjectPhase.SPECIFICATION.value and current is None:
                version_id = None
            else:
                raise SpecGenerationNotAllowed("project is not ready to create or resume a Spec")

        if version_id is None:
            version_id = await self._generate_version(project_id, None, "Initial specification")
        return await self._run_automatic_review(project_id, version_id)

    async def revise_spec(self, project_id: str, comments: str) -> SpecVersion:
        """Generate a new immutable revision after automatic or human rework."""

        if not comments.strip():
            raise ValueError("revision comments are required")
        with self._session_factory() as db:
            project = self._project(db, project_id)
            current = self._current_spec(db, project)
            if current is None or current.status != SpecStatus.REWORK.value:
                raise SpecRevisionNotAllowed("current Spec is not in REWORK")
            parent_id = current.id

        version_id = await self._generate_version(project_id, parent_id, comments)
        return await self._run_automatic_review(project_id, version_id)

    async def prepare_external_revision(
        self,
        context,
        generated: ProjectSpecPayload,
        parent: SpecVersion | None,
        generation_call_id: str,
        change_summary: str,
        generation_source: str,
        *,
        source_refs: list[str] | None = None,
        require_new_revision: bool = False,
        forced_clarification_questions: list[dict[str, object]] | None = None,
    ):
        """Prepare immutable version data and the same hybrid review for any generator."""

        from app.services.command_service import CommandHandlerFailure, PreparedCommand

        with self._session_factory() as db:
            call = db.get(AgentCall, generation_call_id)
            expected_operation = {
                "PM_AGENT": "generate_spec",
                "GITEA_REVIEW_COMMENTS": "rewrite_prd",
                "HISTORICAL_RESTORE": "restore_spec",
            }.get(generation_source)
            if (
                call is None
                or call.project_id != context.project_id
                or call.status != "RESULT_READY"
                or expected_operation is None
                or call.operation != expected_operation
                or call.request.get("command_id") != context.request.command_id
                or call.request.get("input_hash") != context.input_hash
                or (parent is not None and parent.project_id != context.project_id)
            ):
                raise RuntimeError("Spec generation result is unavailable")
            latest_revision = (
                db.query(SpecVersion.revision)
                .filter_by(project_id=context.project_id)
                .order_by(desc(SpecVersion.revision))
                .first()
            )
            generation_request = dict(call.request)
            generator_session_id = call.agent_session_id

        provenance_refs = list(
            source_refs if source_refs is not None else (parent.input_refs if parent else generated.source_refs)
        )
        content = generated.model_copy(update={"source_refs": provenance_refs})
        content_dict = content.model_dump(mode="json")
        content_hash = _canonical_hash(content_dict)
        if (
            parent is not None
            and parent.content_hash == content_hash
            and not require_new_revision
        ):
            return PreparedCommand(
                payload={
                    "no_change": True,
                    "parent_version_id": parent.id,
                    "parent_revision": parent.revision,
                    "content_hash": content_hash,
                },
                agent_backed=True,
                agent_call_ids=[generation_call_id],
                audit_payload={"spec_revision_no_change": True},
            )

        rule_findings = run_rule_review(content, set(provenance_refs))
        semantic = None
        reviewer_call_id = None
        reviewer_session_id = "SYSTEM"
        if not prevents_semantic_review(rule_findings):
            with self._session_factory() as db:
                reviewer_payload = self._spec_reviewer_payload(
                    content_dict,
                    content_hash,
                    provenance_refs,
                    generation_request,
                    command_id=context.request.command_id,
                    input_hash=context.input_hash,
                )
                candidates = [
                    call
                    for call in db.query(AgentCall)
                    .filter_by(
                        project_id=context.project_id,
                        operation="review_spec",
                    )
                    .order_by(desc(AgentCall.started_at), desc(AgentCall.id))
                    .all()
                    if isinstance(call.request, dict)
                    and call.request.get("command_id") == context.request.command_id
                    and call.request.get("input_hash") == context.input_hash
                ]
                binding_mismatches = [
                    call
                    for call in candidates
                    if call.request != reviewer_payload
                    and call.status in {"RESULT_READY", "PENDING", "AMBIGUOUS"}
                ]
                if binding_mismatches:
                    raise CommandHandlerFailure(
                        "Spec semantic review result is unavailable",
                        agent_call_ids=[
                            generation_call_id,
                            *(call.id for call in binding_mismatches),
                        ],
                    )
                exact = [
                    call for call in candidates if call.request == reviewer_payload
                ]
                unresolved = [
                    call
                    for call in exact
                    if call.status in {"PENDING", "AMBIGUOUS"}
                ]
                if unresolved:
                    raise CommandHandlerFailure(
                        "Spec semantic review result is unavailable",
                        agent_call_ids=[
                            generation_call_id,
                            *(call.id for call in unresolved),
                        ],
                    )
                ready = [call for call in exact if call.status == "RESULT_READY"]
                validated: dict[str, SemanticReview] = {}
                for call in ready:
                    try:
                        validated[call.id] = SemanticReview.model_validate(call.response)
                    except Exception as error:
                        raise CommandHandlerFailure(
                            "Spec semantic review result is unavailable",
                            agent_call_ids=[generation_call_id, call.id],
                        ) from error
                if ready:
                    reusable = ready[0]
                    reviewer_call_id = reusable.id
                    reviewer_session_id = reusable.agent_session_id
                    semantic = validated[reusable.id]
                else:
                    reviewer_session = self._agent_session(
                        db,
                        context.project_id,
                        "REVIEWER",
                        "Review Project Spec semantic completeness",
                    )
                    reviewer_session_id = reviewer_session.id
                    reviewer_call = AgentCall(
                        id=_new_id(),
                        project_id=context.project_id,
                        agent_session_id=reviewer_session.id,
                        operation="review_spec",
                        request=reviewer_payload,
                        status="PENDING",
                    )
                    db.add(reviewer_call)
                    db.commit()
                    reviewer_call_id = reviewer_call.id
            if semantic is None:
                try:
                    semantic = await self._agent.review_spec(reviewer_payload)
                except Exception as error:
                    self._record_call_failure(
                        context.project_id,
                        reviewer_call_id,
                        error,
                        "SPEC_SEMANTIC_REVIEW_FAILED",
                    )
                    raise CommandHandlerFailure(
                        str(error),
                        agent_call_ids=[generation_call_id, reviewer_call_id],
                    ) from error
                self._record_call_result(
                    context.project_id,
                    reviewer_call_id,
                    semantic.model_dump(mode="json"),
                )

        outcome = merge_review_outcome(rule_findings, semantic)
        if forced_clarification_questions and outcome is SpecStatus.HUMAN_REVIEW:
            outcome = SpecStatus.REWORK
        return PreparedCommand(
            payload={
                "version_id": _new_id(),
                "revision": (latest_revision[0] if latest_revision else 0) + 1,
                "content": content_dict,
                "content_hash": content_hash,
                "input_refs": provenance_refs,
                "parent_version_id": parent.id if parent else None,
                "change_summary": change_summary,
                "generation_source": generation_source,
                "generator_agent_session_id": generator_session_id,
                "rule_review_id": _new_id(),
                "rule_findings": [
                    item.model_dump(mode="json") for item in rule_findings
                ],
                "semantic_review_id": _new_id(),
                "semantic": semantic.model_dump(mode="json") if semantic else None,
                "reviewer_session_id": reviewer_session_id,
                "outcome": outcome.value,
                "clarification_request_id": _new_id(),
                "forced_clarification_questions": list(
                    forced_clarification_questions or []
                ),
            },
            agent_backed=True,
            agent_call_ids=[
                generation_call_id,
                *([reviewer_call_id] if reviewer_call_id is not None else []),
            ],
        )

    def materialize_revision(self, uow, context, prepared):
        """Materialize one prepared immutable version and its RULE/AGENT receipts."""

        from app.services.command_service import CommandHandlerResult

        if prepared.payload.get("no_change"):
            uow.mark_spec_generation_call_succeeded(prepared.agent_call_ids[0])
            return CommandHandlerResult(
                phase=ProjectPhase.REVIEW,
                current_spec_version_id=str(prepared.payload["parent_version_id"]),
                spec_status=context.state.current_spec_status,
                audit_payload={"spec_revision_no_change": True},
            )

        content = ProjectSpecPayload.model_validate(prepared.payload["content"])
        status = SpecStatus(str(prepared.payload["outcome"]))
        version = SpecVersion(
            id=str(prepared.payload["version_id"]),
            project_id=context.project_id,
            revision=int(prepared.payload["revision"]),
            content=content.model_dump(mode="json"),
            markdown=_render_markdown(content.model_dump(mode="json")),
            generation_source=str(prepared.payload["generation_source"]),
            input_refs=list(prepared.payload["input_refs"]),
            generator_agent_session_id=str(
                prepared.payload["generator_agent_session_id"]
            ),
            generator_call_id=prepared.agent_call_ids[0],
            parent_version_id=prepared.payload["parent_version_id"],
            change_summary=str(prepared.payload["change_summary"]),
            content_hash=str(prepared.payload["content_hash"]),
            status=status.value,
        )
        uow.add_spec_version(version)
        rule_findings = list(prepared.payload["rule_findings"])
        uow.add_spec_review(
            SpecReview(
                id=str(prepared.payload["rule_review_id"]),
                project_id=context.project_id,
                spec_version_id=version.id,
                kind=ReviewKind.RULE.value,
                reviewer_id="SYSTEM",
                input_spec_hash=version.content_hash,
                verdict=(
                    ReviewVerdict.PASS.value
                    if not rule_findings
                    else ReviewVerdict.REJECT.value
                ),
                findings=rule_findings,
                command_id=context.request.command_id,
            )
        )
        semantic_data = prepared.payload["semantic"]
        if semantic_data is None:
            semantic = None
            verdict, findings, comments = "SKIPPED", [], "STRUCTURAL_RULE_FAILURE"
        else:
            semantic = SemanticReview.model_validate(semantic_data)
            verdict = semantic.verdict.value
            findings = [
                item.model_dump(mode="json") for item in semantic.findings
            ]
            comments = None
        uow.add_spec_review(
            SpecReview(
                id=str(prepared.payload["semantic_review_id"]),
                project_id=context.project_id,
                spec_version_id=version.id,
                kind=ReviewKind.AGENT.value,
                reviewer_id=str(prepared.payload["reviewer_session_id"]),
                input_spec_hash=version.content_hash,
                verdict=verdict,
                findings=findings,
                comments=comments,
                command_id=context.request.command_id,
            )
        )
        if status is SpecStatus.NEED_CLARIFICATION:
            questions = list(
                prepared.payload.get("forced_clarification_questions") or []
            ) or self._prepared_clarification_questions(
                content, semantic, int(prepared.payload["revision"])
            )
            uow.add_clarification_request(
                ClarificationRequest(
                    id=str(prepared.payload["clarification_request_id"]),
                    project_id=context.project_id,
                    spec_version_id=version.id,
                    boundary_key=clarification_boundary_key(version.id),
                    questions=questions,
                    analysis_round=int(prepared.payload["revision"]),
                    blocking=True,
                    agent_session_id=version.generator_agent_session_id,
                )
            )
        uow.mark_spec_generation_call_succeeded(prepared.agent_call_ids[0])
        if len(prepared.agent_call_ids) > 1:
            uow.mark_spec_reviewer_call_succeeded(prepared.agent_call_ids[1])
        return CommandHandlerResult(
            phase=ProjectPhase.REVIEW,
            current_spec_version_id=version.id,
            spec_status=status,
            created_resource_ids=[version.id],
            audit_payload={"spec_version_id": version.id},
        )

    def as_command_handler(self):
        """Expose generation and hybrid review through the central command boundary."""
        from app.services.command_service import (
            CommandHandlerFailure,
            CommandHandlerResult,
            PreparedCommand,
        )

        service = self

        class _Handler:
            async def prepare(self, context):
                if context.request.action.value == "restore_spec_version":
                    return await service._prepare_historical_restore(context)
                if context.request.action.value == "create_spec":
                    with service._session_factory() as db:
                        project = service._project(db, context.project_id)
                        existing = service._current_spec(db, project)
                        if (
                            existing is not None
                            and existing.status == SpecStatus.AUTO_REVIEW.value
                        ):
                            resume_data = {
                                "version_id": existing.id,
                                "revision": existing.revision,
                                "content": dict(existing.content),
                                "content_hash": existing.content_hash,
                                "input_refs": list(existing.input_refs),
                                "generator_call_id": existing.generator_call_id,
                                "has_rule_review": service._receipt(
                                    db, existing, ReviewKind.RULE.value
                                )
                                is not None,
                            }
                        else:
                            resume_data = None
                    if resume_data is not None:
                        return await service._prepare_existing_auto_review(
                            context, resume_data
                        )
                with service._session_factory() as db:
                    project = service._project(db, context.project_id)
                    parent = service._current_spec(db, project)
                    if context.request.action.value == "create_spec":
                        if parent is not None:
                            raise SpecGenerationNotAllowed(
                                "an initial Spec already exists"
                            )
                        change_summary = "Initial specification"
                    else:
                        comments = (context.request.message or "").strip()
                        if not comments:
                            raise ValueError("revision comments are required")
                        if parent is None or parent.status != SpecStatus.REWORK.value:
                            raise SpecRevisionNotAllowed("current Spec is not in REWORK")
                        change_summary = comments
                    pm_session = service._agent_session(
                        db, project.id, "PM", "Generate project specifications"
                    )
                    generation_payload = service._generation_payload(
                        db,
                        project,
                        parent,
                        change_summary,
                        decision_actor_id=(
                            context.request.actor_id if parent is not None else None
                        ),
                        decision_id=(
                            context.request.command_id if parent is not None else None
                        ),
                    )
                    generation_payload.update(
                        generation_request_hash=_canonical_hash(generation_payload),
                        command_id=context.request.command_id,
                        input_hash=context.input_hash,
                    )
                    generation_call = next(
                        (
                            call
                            for call in db.query(AgentCall)
                            .filter_by(
                                project_id=project.id,
                                operation="generate_spec",
                                status="RESULT_READY",
                            )
                            .order_by(desc(AgentCall.started_at), desc(AgentCall.id))
                            .all()
                            if call.request == generation_payload
                        ),
                        None,
                    )
                    generated = (
                        ProjectSpecPayload.model_validate(generation_call.response)
                        if generation_call is not None
                        else None
                    )
                    if generation_call is None:
                        generation_call = AgentCall(
                            id=_new_id(),
                            project_id=project.id,
                            agent_session_id=pm_session.id,
                            operation="generate_spec",
                            request=generation_payload,
                            status="PENDING",
                        )
                        db.add(generation_call)
                    db.commit()
                call_ids = [generation_call.id]
                if generated is None:
                    try:
                        generated = await service._agent.generate_spec(generation_payload)
                    except Exception as error:
                        service._record_call_failure(
                            context.project_id,
                            generation_call.id,
                            error,
                            "SPEC_GENERATION_FAILED",
                        )
                        raise CommandHandlerFailure(
                            str(error), agent_call_ids=call_ids
                        ) from error
                    service._record_call_result(
                        context.project_id,
                        generation_call.id,
                        generated.model_dump(mode="json"),
                    )

                return await service.prepare_external_revision(
                    context,
                    generated,
                    parent,
                    generation_call.id,
                    change_summary,
                    "PM_AGENT",
                    source_refs=list(generation_payload["input_refs"]),
                )

            def materialize(self, uow, context, prepared):
                if prepared.payload.get("existing_auto_review"):
                    version_id = str(prepared.payload["version_id"])
                    rule_findings = list(prepared.payload["rule_findings"])
                    if prepared.payload["create_rule_review"]:
                        uow.add_spec_review(
                            SpecReview(
                                id=str(prepared.payload["rule_review_id"]),
                                project_id=context.project_id,
                                spec_version_id=version_id,
                                kind=ReviewKind.RULE.value,
                                reviewer_id="SYSTEM",
                                input_spec_hash=str(prepared.payload["content_hash"]),
                                verdict=(ReviewVerdict.PASS.value if not rule_findings else ReviewVerdict.REJECT.value),
                                findings=rule_findings,
                            )
                        )
                    semantic_data = prepared.payload["semantic"]
                    if semantic_data is None:
                        verdict, findings, comments = "SKIPPED", [], "STRUCTURAL_RULE_FAILURE"
                    else:
                        semantic = SemanticReview.model_validate(semantic_data)
                        verdict = semantic.verdict.value
                        findings = [item.model_dump(mode="json") for item in semantic.findings]
                        comments = None
                    uow.add_spec_review(
                        SpecReview(
                            id=str(prepared.payload["semantic_review_id"]),
                            project_id=context.project_id,
                            spec_version_id=version_id,
                            kind=ReviewKind.AGENT.value,
                            reviewer_id=str(prepared.payload["reviewer_session_id"]),
                            input_spec_hash=str(prepared.payload["content_hash"]),
                            verdict=verdict,
                            findings=findings,
                            comments=comments,
                        )
                    )
                    if prepared.agent_call_ids:
                        uow.mark_spec_reviewer_call_succeeded(
                            prepared.agent_call_ids[0]
                        )
                    return CommandHandlerResult(
                        phase=ProjectPhase.REVIEW,
                        current_spec_version_id=version_id,
                        spec_status=SpecStatus(str(prepared.payload["outcome"])),
                        audit_payload={"spec_version_id": version_id, "resumed_auto_review": True},
                    )
                return service.materialize_revision(uow, context, prepared)

        return _Handler()

    async def _prepare_historical_restore(self, context):
        """Clone historical content as n+1 and run the normal review gate."""

        from app.services.command_service import PreparedCommand, assert_reviewer

        source_revision = context.request.payload.get("source_revision")
        reason = (context.request.message or "").strip()
        if (
            not isinstance(source_revision, int)
            or isinstance(source_revision, bool)
            or source_revision <= 0
        ):
            raise ValueError("source_revision must be a positive integer")
        if not reason:
            raise ValueError("a restore reason is required")

        with self._session_factory() as db:
            project = self._project(db, context.project_id)
            assert_reviewer(project, context.request.actor_id)
            parent = self._current_spec(db, project)
            if parent is None:
                raise SpecRevisionNotAllowed("there is no current Spec to restore from")
            source = (
                db.query(SpecVersion)
                .filter_by(project_id=project.id, revision=source_revision)
                .one_or_none()
            )
            if source is None:
                raise KeyError("historical Spec revision not found")
            if source.revision >= parent.revision:
                raise ValueError("source_revision must identify an older Spec")

            restore_session = self._agent_session(
                db,
                project.id,
                "SYSTEM",
                "Restore historical project specification content",
            )
            restore_request = {
                "command_id": context.request.command_id,
                "input_hash": context.input_hash,
                "source_revision": source.revision,
                "source_spec_version_id": source.id,
                "source_content_hash": source.content_hash,
                "parent_version_id": parent.id,
                "reason": reason,
            }
            restore_call = next(
                (
                    call
                    for call in db.query(AgentCall)
                    .filter_by(
                        project_id=project.id,
                        operation="restore_spec",
                        status="RESULT_READY",
                    )
                    .all()
                    if call.request == restore_request
                ),
                None,
            )
            if restore_call is None:
                restore_call = AgentCall(
                    id=_new_id(),
                    project_id=project.id,
                    agent_session_id=restore_session.id,
                    operation="restore_spec",
                    request=restore_request,
                    response=dict(source.content),
                    status="RESULT_READY",
                    completed_at=_now(),
                )
                db.add(restore_call)
            source_content = dict(source.content)
            source_refs = list(source.input_refs)
            source_id = source.id
            parent_id = parent.id
            db.commit()

        prepared = await self.prepare_external_revision(
            context,
            ProjectSpecPayload.model_validate(source_content),
            self._version_snapshot(context.project_id, parent_id),
            restore_call.id,
            reason,
            "HISTORICAL_RESTORE",
            source_refs=source_refs,
            require_new_revision=True,
        )
        return PreparedCommand(
            payload=prepared.payload,
            agent_backed=prepared.agent_backed,
            agent_call_ids=prepared.agent_call_ids,
            audit_payload={
                **dict(prepared.audit_payload),
                "generation_source": "HISTORICAL_RESTORE",
                "source_revision": source_revision,
                "source_spec_version_id": source_id,
                "restore_reason": reason,
            },
        )

    def _version_snapshot(self, project_id: str, version_id: str) -> SpecVersion:
        """Return a detached immutable version for long-running preparation."""

        with self._session_factory() as db:
            version = self._version_for_project(db, project_id, version_id)
            db.expunge(version)
            return version

    async def _prepare_existing_auto_review(self, context, data: dict[str, object]):
        """Prepare only the missing hybrid review for an existing AUTO_REVIEW version."""
        from app.services.command_service import CommandHandlerFailure, PreparedCommand

        content = ProjectSpecPayload.model_validate(data["content"])
        rule_findings = run_rule_review(content, set(data["input_refs"]))
        semantic = None
        reviewer_call_id = None
        reviewer_session_id = "SYSTEM"
        if not prevents_semantic_review(rule_findings):
            with self._session_factory() as db:
                reviewer_session = self._agent_session(
                    db,
                    context.project_id,
                    "REVIEWER",
                    "Review Project Spec semantic completeness",
                )
                reviewer_session_id = reviewer_session.id
                generation_call = db.get(AgentCall, str(data["generator_call_id"]))
                if (
                    generation_call is None
                    or generation_call.project_id != context.project_id
                    or generation_call.operation not in {"generate_spec", "diagram_edit"}
                ):
                    raise RuntimeError("Spec generation source snapshot is unavailable")
                payload = self._spec_reviewer_payload(
                    content.model_dump(mode="json"),
                    str(data["content_hash"]),
                    list(data["input_refs"]),
                    generation_call.request,
                    command_id=context.request.command_id,
                    input_hash=context.input_hash,
                )
                call = AgentCall(
                    id=_new_id(),
                    project_id=context.project_id,
                    agent_session_id=reviewer_session.id,
                    operation="review_spec",
                    request=payload,
                    status="PENDING",
                )
                db.add(call)
                db.commit()
                reviewer_call_id = call.id
            try:
                semantic = await self._agent.review_spec(payload)
            except Exception as error:
                self._record_call_failure(
                    context.project_id,
                    reviewer_call_id,
                    error,
                    "SPEC_SEMANTIC_REVIEW_FAILED",
                )
                raise CommandHandlerFailure(
                    str(error), agent_call_ids=[reviewer_call_id]
                ) from error
            self._record_call_result(
                context.project_id,
                reviewer_call_id,
                semantic.model_dump(mode="json"),
            )
        outcome = merge_review_outcome(rule_findings, semantic)
        return PreparedCommand(
            payload={
                "existing_auto_review": True,
                **data,
                "create_rule_review": not bool(data["has_rule_review"]),
                "rule_review_id": _new_id(),
                "rule_findings": [item.model_dump(mode="json") for item in rule_findings],
                "semantic_review_id": _new_id(),
                "semantic": semantic.model_dump(mode="json") if semantic else None,
                "reviewer_session_id": reviewer_session_id,
                "outcome": outcome.value,
            },
            agent_backed=reviewer_call_id is not None,
            agent_call_ids=[reviewer_call_id] if reviewer_call_id else [],
        )

    @staticmethod
    def _prepared_clarification_questions(
        spec: ProjectSpecPayload,
        semantic: SemanticReview | None,
        revision: int,
    ) -> list[dict[str, object]]:
        questions = [
            {
                "question_id": f"SPEC-{revision}-OPEN-Q{index}",
                "question": item.question,
                "reason": "This blocking Project Spec decision must be resolved before revision.",
                "affected_areas": ["open_questions"],
                "blocking": True,
            }
            for index, item in enumerate(
                (item for item in spec.open_questions if item.blocking), start=1
            )
        ]
        if semantic is not None:
            questions.extend(
                {
                    "question_id": f"SPEC-{revision}-SEMANTIC-Q{index}",
                    "question": item.message,
                    "reason": item.suggested_resolution,
                    "affected_areas": [item.spec_path.strip("/") or "spec"],
                    "blocking": True,
                }
                for index, item in enumerate(
                    (
                        item
                        for item in semantic.findings
                        if item.code == "NEEDS_HUMAN_DECISION"
                        and item.blocks_progress
                    ),
                    start=1,
                )
            )
            if semantic.verdict is ReviewVerdict.NEED_INFO and not questions:
                questions.append(
                    {
                        "question_id": f"SPEC-{revision}-REVIEWER-Q1",
                        "question": "What additional project decision or source evidence should be supplied for this Spec?",
                        "reason": "The Reviewer requested more information without identifying a specific finding.",
                        "affected_areas": ["spec"],
                        "blocking": True,
                    }
                )
        if not questions:
            raise RuntimeError("NEED_CLARIFICATION requires an answerable decision")
        return questions

    async def _generate_version(
        self, project_id: str, parent_version_id: str | None, change_summary: str
    ) -> str:
        """Generate once, durably save its result, then materialize an immutable version."""

        with self._session_factory() as db:
            project = self._project(db, project_id)
            pm_session = self._agent_session(db, project_id, "PM", "Generate project specifications")
            parent = db.get(SpecVersion, parent_version_id) if parent_version_id else None
            payload = self._generation_payload(db, project, parent, change_summary)
            payload["generation_request_hash"] = _canonical_hash(payload)
            call = self._generation_call(db, project_id, payload)
            if call is not None and call.status == "RESULT_READY":
                call_id = call.id
                generated = ProjectSpecPayload.model_validate(call.response)
            elif call is not None and call.status == "NO_CHANGE":
                if parent is None:
                    raise RuntimeError("initial Spec generation cannot be a no-change result")
                return parent.id
            elif call is not None and call.status == "PENDING":
                raise AgentCallInDoubt(
                    f"generation Agent call {call.id} is pending; result must be recovered before retry"
                )
            else:
                call = AgentCall(
                    id=_new_id(),
                    project_id=project_id,
                    agent_session_id=pm_session.id,
                    operation="generate_spec",
                    request=payload,
                    status="PENDING",
                )
                db.add(call)
                db.commit()
                call_id = call.id
                generated = None

        if generated is None:
            try:
                generated = await self._agent.generate_spec(payload)
            except Exception as error:
                self._record_call_failure(project_id, call_id, error, "SPEC_GENERATION_FAILED")
                raise
            self._record_call_result(project_id, call_id, generated.model_dump(mode="json"))

        return self._materialize_generated_version(project_id, call_id, generated)

    def _materialize_generated_version(
        self,
        project_id: str,
        call_id: str,
        generated: ProjectSpecPayload,
    ) -> str:
        """Turn a durably saved PM response into one immutable SpecVersion."""

        with self._session_factory() as db:
            project = self._project(db, project_id)
            call = db.get(AgentCall, call_id)
            if call is None:
                raise RuntimeError("generation Agent call disappeared")
            existing = db.query(SpecVersion).filter_by(generator_call_id=call_id).one_or_none()
            if existing is not None:
                return existing.id
            provenance_refs = list(call.request["input_refs"])
            content = generated.model_copy(update={"source_refs": provenance_refs}).model_dump(mode="json")
            content_hash = _canonical_hash(content)
            parent_version_id = call.request["parent_version_id"]
            change_summary = call.request["change_summary"]
            parent = db.get(SpecVersion, parent_version_id) if parent_version_id else None
            if parent is not None and parent.content_hash == content_hash:
                call.status = "NO_CHANGE"
                call.completed_at = _now()
                db.add(
                    AuditEvent(
                        id=_new_id(),
                        project_id=project_id,
                        session_id=project.session_id,
                        event_type="SPEC_REVISION_NO_CHANGE",
                        actor_id=None,
                        payload={"parent_version_id": parent.id, "generator_call_id": call.id},
                    )
                )
                db.commit()
                return parent.id
            revision = (
                db.query(SpecVersion.revision)
                .filter_by(project_id=project_id)
                .order_by(desc(SpecVersion.revision))
                .first()
            )
            version = SpecVersion(
                id=_new_id(),
                project_id=project_id,
                revision=(revision[0] if revision else 0) + 1,
                content=content,
                markdown=_render_markdown(content),
                generation_source="PM_AGENT",
                input_refs=provenance_refs,
                generator_agent_session_id=call.agent_session_id,
                generator_call_id=call.id,
                parent_version_id=parent_version_id,
                change_summary=change_summary,
                content_hash=content_hash,
                status=SpecStatus.DRAFT.value,
            )
            call.status = "SUCCEEDED"
            call.completed_at = _now()
            db.add(version)
            db.flush()
            version.status = SpecStatus.AUTO_REVIEW.value
            project.current_spec_version_id = version.id
            project.phase = ProjectPhase.REVIEW.value
            project.state_version += 1
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type="SPEC_VERSION_GENERATED",
                    actor_id=None,
                    payload={"spec_version_id": version.id, "revision": version.revision, "content_hash": content_hash},
                )
            )
            db.commit()
            return version.id

    async def _run_automatic_review(self, project_id: str, version_id: str) -> SpecVersion:
        """Persist/reuse rule results, then either skip or invoke the Reviewer Agent."""

        with self._session_factory() as db:
            project = self._project(db, project_id)
            version = self._version_for_project(db, project_id, version_id)
            if version.status != SpecStatus.AUTO_REVIEW.value:
                return version
            rule_receipt = self._receipt(db, version, ReviewKind.RULE.value)
            if rule_receipt is None:
                findings = run_rule_review(
                    self._payload(version), set(version.input_refs)
                )
                rule_receipt = SpecReview(
                    id=_new_id(),
                    project_id=project_id,
                    spec_version_id=version.id,
                    kind=ReviewKind.RULE.value,
                    reviewer_id="SYSTEM",
                    input_spec_hash=version.content_hash,
                    verdict=ReviewVerdict.PASS.value if not findings else ReviewVerdict.REJECT.value,
                    findings=[item.model_dump(mode="json") for item in findings],
                    comments=None,
                )
                db.add(rule_receipt)
                db.commit()
            rule_findings = [self._finding(item) for item in rule_receipt.findings]
            agent_receipt = self._receipt(db, version, ReviewKind.AGENT.value)
            if agent_receipt is not None:
                semantic = self._semantic_receipt(agent_receipt)
                return self._finalize_review(project_id, version.id, rule_findings, semantic)
            if prevents_semantic_review(rule_findings):
                db.add(
                    SpecReview(
                        id=_new_id(),
                        project_id=project_id,
                        spec_version_id=version.id,
                        kind=ReviewKind.AGENT.value,
                        reviewer_id="SYSTEM",
                        input_spec_hash=version.content_hash,
                        verdict="SKIPPED",
                        findings=[],
                        comments="STRUCTURAL_RULE_FAILURE",
                    )
                )
                db.commit()
                return self._finalize_review(project_id, version.id, rule_findings, None)
            call = self._semantic_call(db, version)
            if call is not None and call.status == "RESULT_READY":
                call_id = call.id
                semantic = SemanticReview.model_validate(call.response)
                payload = None
            elif call is not None and call.status == "PENDING":
                raise AgentCallInDoubt(
                    f"semantic-review Agent call {call.id} is pending; result must be recovered before retry"
                )
            else:
                reviewer_session = self._agent_session(
                    db, project_id, "REVIEWER", "Review Project Spec semantic completeness"
                )
                generation_call = db.get(AgentCall, version.generator_call_id)
                if (
                    generation_call is None
                    or generation_call.project_id != project_id
                    or generation_call.operation not in {"generate_spec", "diagram_edit"}
                ):
                    raise RuntimeError("Spec generation source snapshot is unavailable")
                payload = self._spec_reviewer_payload(
                    dict(version.content),
                    version.content_hash,
                    list(version.input_refs),
                    generation_call.request,
                )
                call = AgentCall(
                    id=_new_id(),
                    project_id=project_id,
                    agent_session_id=reviewer_session.id,
                    operation="review_spec",
                    request=payload,
                    status="PENDING",
                )
                db.add(call)
                db.commit()
                call_id = call.id
                semantic = None

        if semantic is None:
            assert payload is not None
            try:
                semantic = await self._agent.review_spec(payload)
            except Exception as error:
                self._record_call_failure(project_id, call_id, error, "SPEC_SEMANTIC_REVIEW_FAILED")
                with self._session_factory() as db:
                    return self._version_for_project(db, project_id, version_id)
            self._record_call_result(project_id, call_id, semantic.model_dump(mode="json"))

        self._materialize_semantic_receipt(project_id, version_id, call_id, semantic)

        with self._session_factory() as db:
            version = self._version_for_project(db, project_id, version_id)
            rule_receipt = self._receipt(db, version, ReviewKind.RULE.value)
            assert rule_receipt is not None
            return self._finalize_review(
                project_id,
                version.id,
                [self._finding(item) for item in rule_receipt.findings],
                semantic,
            )

    def _materialize_semantic_receipt(
        self, project_id: str, version_id: str, call_id: str, semantic: SemanticReview
    ) -> None:
        """Persist a durable semantic-review result without rerunning the Reviewer Agent."""

        with self._session_factory() as db:
            version = self._version_for_project(db, project_id, version_id)
            if self._receipt(db, version, ReviewKind.AGENT.value) is not None:
                return
            call = db.get(AgentCall, call_id)
            if call is None:
                raise RuntimeError("semantic-review Agent call disappeared")
            call.status = "SUCCEEDED"
            call.completed_at = _now()
            db.add(
                SpecReview(
                    id=_new_id(),
                    project_id=project_id,
                    spec_version_id=version.id,
                    kind=ReviewKind.AGENT.value,
                    reviewer_id=call.agent_session_id,
                    input_spec_hash=version.content_hash,
                    verdict=semantic.verdict.value,
                    findings=[item.model_dump(mode="json") for item in semantic.findings],
                    comments=None,
                )
            )
            db.commit()

    def _finalize_review(
        self,
        project_id: str,
        version_id: str,
        rule_findings: list,
        semantic: SemanticReview | None,
    ) -> SpecVersion:
        with self._session_factory() as db:
            project = self._project(db, project_id)
            version = self._version_for_project(db, project_id, version_id)
            status = merge_review_outcome(rule_findings, semantic)
            if version.status != status.value:
                version.status = status.value
                project.phase = ProjectPhase.REVIEW.value
                project.state_version += 1
                clarification_request = None
                if status is SpecStatus.NEED_CLARIFICATION:
                    clarification_request = self._ensure_spec_clarification_request(
                        db, project, version, semantic
                    )
                db.add(
                    AuditEvent(
                        id=_new_id(),
                        project_id=project_id,
                        session_id=project.session_id,
                        event_type="SPEC_AUTO_REVIEW_COMPLETED",
                        actor_id=None,
                        payload={
                            "spec_version_id": version.id,
                            "status": status.value,
                            "clarification_request_id": (
                                clarification_request.id if clarification_request is not None else None
                            ),
                        },
                    )
                )
                db.commit()
            return version

    def _ensure_spec_clarification_request(
        self,
        db: Session,
        project: Project,
        version: SpecVersion,
        semantic: SemanticReview | None,
    ) -> ClarificationRequest:
        """Create exactly one focused human-decision request for an immutable Spec version."""

        existing = (
            db.query(ClarificationRequest)
            .filter_by(project_id=project.id, spec_version_id=version.id)
            .order_by(
                desc(ClarificationRequest.analysis_round),
                desc(ClarificationRequest.created_at),
                desc(ClarificationRequest.id),
            )
            .first()
        )
        if existing is not None:
            return existing
        spec = self._payload(version)
        questions = [
            {
                "question_id": f"SPEC-{version.revision}-OPEN-Q{index}",
                "question": question.question,
                "reason": "This blocking Project Spec decision must be resolved before revision.",
                "affected_areas": ["open_questions"],
                "blocking": True,
            }
            for index, question in enumerate(
                (item for item in spec.open_questions if item.blocking), start=1
            )
        ]
        if semantic is not None:
            for index, finding in enumerate(
                (
                    item
                    for item in semantic.findings
                    if item.code == "NEEDS_HUMAN_DECISION" and item.blocks_progress
                ),
                start=1,
            ):
                questions.append(
                    {
                        "question_id": f"SPEC-{version.revision}-SEMANTIC-Q{index}",
                        "question": finding.message,
                        "reason": finding.suggested_resolution,
                        "affected_areas": [finding.spec_path.strip("/") or "spec"],
                        "blocking": True,
                    }
                )
            if semantic.verdict is ReviewVerdict.NEED_INFO and not questions:
                questions.append(
                    {
                        "question_id": f"SPEC-{version.revision}-REVIEWER-Q1",
                        "question": "What additional project decision or source evidence should be supplied for this Spec?",
                        "reason": "The Reviewer requested more information without identifying a specific finding.",
                        "affected_areas": ["spec"],
                        "blocking": True,
                    }
                )
        if not questions:
            raise RuntimeError("NEED_CLARIFICATION requires at least one answerable decision")
        latest_round = (
            db.query(ClarificationRequest.analysis_round)
            .filter_by(project_id=project.id)
            .order_by(desc(ClarificationRequest.analysis_round))
            .first()
        )
        request = ClarificationRequest(
            id=_new_id(),
            project_id=project.id,
            spec_version_id=version.id,
            boundary_key=clarification_boundary_key(version.id),
            questions=questions,
            analysis_round=(latest_round[0] if latest_round is not None else 0) + 1,
            blocking=True,
            agent_session_id=version.generator_agent_session_id,
            agent_call_id=None,
        )
        try:
            with db.begin_nested():
                db.add(request)
                db.flush()
        except IntegrityError:
            existing = (
                db.query(ClarificationRequest)
                .filter_by(project_id=project.id, spec_version_id=version.id)
                .order_by(
                    desc(ClarificationRequest.analysis_round),
                    desc(ClarificationRequest.created_at),
                    desc(ClarificationRequest.id),
                )
                .first()
            )
            if existing is None:
                raise
            return existing
        return request

    def _generation_payload(
        self,
        db: Session,
        project: Project,
        parent: SpecVersion | None,
        comments: str,
        *,
        decision_actor_id: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, object]:
        decision_history: list[dict[str, object]] = []
        for call in (
            db.query(AgentCall)
            .filter_by(project_id=project.id, operation="generate_spec")
            .order_by(AgentCall.started_at, AgentCall.id)
            .all()
        ):
            request = dict(call.request or {})
            decision = request.get("authenticated_revision_decision")
            if not isinstance(decision, dict):
                continue
            saved = dict(decision)
            saved.setdefault("decision_id", request.get("command_id"))
            decision_history.append(saved)
        payload: dict[str, object] = {
            "brief": project.brief,
            "input_refs": self._input_refs(db, project),
            "artifacts": self._artifact_snapshot(db, project.id),
            "clarification_history": self._clarification_history(db, project.id),
            "change_summary": comments,
            "parent_version_id": parent.id if parent is not None else None,
            "authenticated_revision_decision_history": decision_history,
        }
        if parent is not None:
            payload["parent_spec"] = parent.content
            payload["parent_spec_hash"] = parent.content_hash
            payload["review_comments"] = comments
            if decision_actor_id is not None:
                payload["authenticated_revision_decision"] = {
                    "decision_id": decision_id,
                    "actor_id": decision_actor_id,
                    "authority": "AUTHORIZED_PROJECT_REVIEWER",
                    "instruction": comments,
                }
        return payload

    @staticmethod
    def _artifact_snapshot(db: Session, project_id: str) -> list[dict[str, object]]:
        return [
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "content": artifact.content,
                "external_ref": artifact.external_ref,
                "content_hash": artifact.content_hash,
                "source_actor_id": artifact.source_actor_id,
            }
            for artifact in db.query(Artifact)
            .filter_by(project_id=project_id)
            .order_by(Artifact.created_at, Artifact.id)
            .all()
        ]

    @staticmethod
    def _spec_reviewer_payload(
        spec: dict[str, object],
        spec_hash: str,
        input_refs: list[str],
        generation_source_snapshot: dict[str, object],
        *,
        command_id: str | None = None,
        input_hash: str | None = None,
    ) -> dict[str, object]:
        spec_copy = json.loads(json.dumps(spec, sort_keys=True, ensure_ascii=False))
        snapshot_copy = json.loads(
            json.dumps(generation_source_snapshot, sort_keys=True, ensure_ascii=False)
        )
        payload: dict[str, object] = {
            "spec": _compact_spec_diagrams(spec_copy),
            "spec_hash": spec_hash,
            "input_refs": list(input_refs),
            "generation_source_snapshot": _compact_generation_snapshot(snapshot_copy),
        }
        if command_id is not None:
            payload["command_id"] = command_id
        if input_hash is not None:
            payload["input_hash"] = input_hash
        return payload

    @staticmethod
    def _payload(version: SpecVersion):
        return ProjectSpecPayload.model_validate(version.content)

    @staticmethod
    def _finding(value: dict[str, object]):
        from app.domain.types import ReviewFinding

        return ReviewFinding.model_validate(value)

    @staticmethod
    def _semantic_receipt(receipt: SpecReview) -> SemanticReview | None:
        if receipt.verdict == "SKIPPED":
            return None
        return SemanticReview(
            verdict=ReviewVerdict(receipt.verdict),
            findings=[SpecService._finding(item) for item in receipt.findings],
        )

    @staticmethod
    def _receipt(db: Session, version: SpecVersion, kind: str) -> SpecReview | None:
        return (
            db.query(SpecReview)
            .filter_by(spec_version_id=version.id, kind=kind, input_spec_hash=version.content_hash)
            .order_by(SpecReview.created_at)
            .first()
        )

    @staticmethod
    def _generation_call(
        db: Session, project_id: str, payload: dict[str, object]
    ) -> AgentCall | None:
        calls = (
            db.query(AgentCall)
            .filter_by(project_id=project_id, operation="generate_spec")
            .order_by(desc(AgentCall.started_at))
            .all()
        )
        return next(
            (
                call
                for call in calls
                if call.request.get("generation_request_hash") == payload["generation_request_hash"]
                and call.status in {"PENDING", "RESULT_READY", "NO_CHANGE"}
            ),
            None,
        )

    @staticmethod
    def _semantic_call(db: Session, version: SpecVersion) -> AgentCall | None:
        calls = (
            db.query(AgentCall)
            .filter_by(project_id=version.project_id, operation="review_spec")
            .order_by(desc(AgentCall.started_at))
            .all()
        )
        return next(
            (
                call
                for call in calls
                if call.request.get("spec_hash") == version.content_hash
                and call.status in {"PENDING", "RESULT_READY"}
            ),
            None,
        )

    @staticmethod
    def _current_spec(db: Session, project: Project) -> SpecVersion | None:
        return db.get(SpecVersion, project.current_spec_version_id) if project.current_spec_version_id else None

    @staticmethod
    def _project(db: Session, project_id: str) -> Project:
        project = db.get(Project, project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    @staticmethod
    def _version_for_project(db: Session, project_id: str, version_id: str) -> SpecVersion:
        version = db.get(SpecVersion, version_id)
        if version is None or version.project_id != project_id:
            raise KeyError("Spec version not found for project")
        return version

    @staticmethod
    def _agent_session(db: Session, project_id: str, role: str, purpose: str) -> AgentSession:
        session = db.query(AgentSession).filter_by(project_id=project_id, role=role).one_or_none()
        if session is None:
            session = AgentSession(
                id=_new_id(), project_id=project_id, role=role, purpose=purpose, metadata_json={}
            )
            db.add(session)
            db.flush()
        return session

    @staticmethod
    def _input_refs(db: Session, project: Project) -> list[str]:
        """Return all and only persisted project evidence consumed by Spec generation."""

        refs = [
            f"artifact:{artifact.id}"
            for artifact in db.query(Artifact)
            .filter_by(project_id=project.id)
            .order_by(Artifact.created_at, Artifact.id)
            .all()
        ]
        requests = (
            db.query(ClarificationRequest)
            .filter_by(project_id=project.id)
            .order_by(ClarificationRequest.analysis_round, ClarificationRequest.created_at, ClarificationRequest.id)
            .all()
        )
        for request in requests:
            refs.append(f"clarification_request:{request.id}")
            responses = (
                db.query(ClarificationResponse)
                .filter_by(clarification_request_id=request.id)
                .order_by(ClarificationResponse.created_at, ClarificationResponse.id)
                .all()
            )
            refs.extend(f"clarification_response:{response.id}" for response in responses)
        return refs

    @staticmethod
    def _clarification_history(db: Session, project_id: str) -> list[dict[str, object]]:
        history: list[dict[str, object]] = []
        requests = (
            db.query(ClarificationRequest)
            .filter_by(project_id=project_id)
            .order_by(ClarificationRequest.analysis_round, ClarificationRequest.created_at, ClarificationRequest.id)
            .all()
        )
        for request in requests:
            responses = (
                db.query(ClarificationResponse)
                .filter_by(clarification_request_id=request.id)
                .order_by(ClarificationResponse.created_at, ClarificationResponse.id)
                .all()
            )
            history.append(
                {
                    "request_id": request.id,
                    "questions": request.questions,
                    "responses": [
                        {
                            "response_id": response.id,
                            "answers": response.answers,
                            "actor_id": response.actor_id,
                        }
                        for response in responses
                    ],
                }
            )
        return history

    def _record_call_result(self, project_id: str, call_id: str, result: dict[str, object]) -> None:
        """Checkpoint an Agent response before any derived records are written."""

        with self._session_factory() as db:
            call = db.get(AgentCall, call_id)
            if call is None or call.project_id != project_id:
                raise RuntimeError("Agent call disappeared")
            call.status = "RESULT_READY"
            call.response = result
            call.completed_at = _now()
            db.commit()

    def _record_call_failure(self, project_id: str, call_id: str, error: Exception, event_type: str) -> None:
        with self._session_factory() as db:
            project = self._project(db, project_id)
            call = db.get(AgentCall, call_id)
            if call is None:
                raise RuntimeError("Agent call disappeared")
            call.status = "FAILED"
            call.error = str(error)
            call.completed_at = _now()
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type=event_type,
                    actor_id=None,
                    payload={
                        "agent_call_id": call.id,
                        "error_code": "AGENT_OPERATION_FAILED",
                        "message": "Spec Agent operation failed; retry the workflow action.",
                    },
                )
            )
            db.commit()
