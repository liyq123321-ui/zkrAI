"""SQLAlchemy persistence models for legacy chat and project-spec workflows."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, text

from .database import Base


def utc_now() -> datetime:
    """Return an aware UTC timestamp for database defaults and updates."""

    return datetime.now(UTC)


def clarification_boundary_key(spec_version_id: str | None) -> str:
    return f"SPEC:{spec_version_id}" if spec_version_id else "INTAKE"


def _default_clarification_boundary(context) -> str:
    return clarification_boundary_key(context.get_current_parameters().get("spec_version_id"))


class Conversation(Base):
    """Legacy conversation record retained for the existing chat API."""

    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    workflow = Column(String, default="spec_only")
    agent = Column(String, default="planner")
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Message(Base):
    """Legacy message record retained for the existing chat API."""

    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Epic(Base):
    """Legacy epic record retained for existing task parsing code."""

    __tablename__ = "epics"

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True)
    title = Column(String)
    description = Column(Text)
    status = Column(String, default="planning")
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_project_session"),
        UniqueConstraint("creation_request_id", name="uq_project_creation_request"),
    )

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, unique=True, index=True)
    creation_request_id = Column(String, nullable=False, unique=True, index=True)
    brief = Column(JSON, nullable=False)
    final_approver = Column(String, nullable=False)
    project_manager_ids = Column(JSON, nullable=False, default=list)
    root_owner_ids = Column(JSON, nullable=False, default=list)
    phase = Column(String, nullable=False, default="INTAKE")
    state_version = Column(Integer, nullable=False, default=0)
    current_spec_version_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class SdlcRouteDecision(Base):
    """Recommended routes and the user's pre-planning lifecycle choice."""

    __tablename__ = "sdlc_route_decisions"
    __table_args__ = (UniqueConstraint("project_id", name="uq_sdlc_route_project"),)

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, unique=True, index=True)
    rules_version = Column(String, nullable=False)
    rules_hash = Column(String(64), nullable=False)
    assessment = Column(JSON, nullable=False)
    options = Column(JSON, nullable=False)
    selected_model = Column(String, nullable=True)
    selected_by = Column(String, nullable=True)
    selected_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    purpose = Column(String, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AgentCall(Base):
    __tablename__ = "agent_calls"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    agent_session_id = Column(String, nullable=False, index=True)
    operation = Column(String, nullable=False)
    request = Column(JSON, nullable=False, default=dict)
    response = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="PENDING")
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class AgentRuntimeEvent(Base):
    """Append-only, public-safe Codex JSONL event attached to one Agent call."""

    __tablename__ = "agent_runtime_events"
    __table_args__ = (
        Index(
            "ix_agent_runtime_events_session_time",
            "agent_session_id",
            "created_at",
            "id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False, index=True)
    agent_session_id = Column(String, nullable=False, index=True)
    agent_call_id = Column(String, nullable=False, index=True)
    operation = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    item_type = Column(String, nullable=True)
    status = Column(String, nullable=True)
    title = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class IntakeAnalysisClaim(Base):
    """One durable owner for PM intake analysis per project at a time."""

    __tablename__ = "intake_analysis_claims"
    __table_args__ = (UniqueConstraint("project_id", name="uq_intake_analysis_project"),)

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, unique=True, index=True)
    agent_call_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="PREPARING")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    content = Column(JSON, nullable=True)
    external_ref = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False)
    source_actor_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ClarificationRequest(Base):
    __tablename__ = "clarification_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "boundary_key",
            "analysis_round",
            name="uq_clarification_boundary_round",
        ),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    spec_version_id = Column(String, nullable=True, index=True)
    boundary_key = Column(String, nullable=False, default=_default_clarification_boundary)
    questions = Column(JSON, nullable=False)
    analysis_round = Column(Integer, nullable=False, default=1)
    blocking = Column(Boolean, nullable=False, default=True)
    agent_session_id = Column(String, nullable=True)
    agent_call_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ClarificationResponse(Base):
    __tablename__ = "clarification_responses"
    __table_args__ = (
        UniqueConstraint(
            "clarification_request_id",
            "response_slot",
            name="uq_clarification_response_slot",
        ),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    clarification_request_id = Column(String, nullable=False, index=True)
    response_slot = Column(String, nullable=False, default="PRIMARY")
    actor_id = Column(String, nullable=False)
    answers = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class SpecVersion(Base):
    __tablename__ = "spec_versions"
    __table_args__ = (UniqueConstraint("project_id", "revision", name="uq_spec_revision"),)

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    content = Column(JSON, nullable=False)
    markdown = Column(Text, nullable=False)
    generation_source = Column(String, nullable=False)
    input_refs = Column(JSON, nullable=False)
    generator_agent_session_id = Column(String, nullable=False)
    generator_call_id = Column(String, nullable=False)
    parent_version_id = Column(String, nullable=True)
    change_summary = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String, nullable=False, default="DRAFT")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class SpecReview(Base):
    __tablename__ = "spec_reviews"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    spec_version_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    reviewer_id = Column(String, nullable=False)
    input_spec_hash = Column(String(64), nullable=False)
    verdict = Column(String, nullable=False)
    findings = Column(JSON, nullable=False, default=list)
    comments = Column(Text, nullable=True)
    command_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class PrdPrototype(Base):
    """Version-bound HTML prototype generated from one immutable Spec."""

    __tablename__ = "prd_prototypes"
    __table_args__ = (
        UniqueConstraint("spec_version_id", name="uq_prd_prototype_spec_version"),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    spec_version_id = Column(String, nullable=False, unique=True, index=True)
    generator_agent_session_id = Column(String, nullable=False)
    generator_call_id = Column(String, nullable=False, unique=True)
    status = Column(String, nullable=False)
    title = Column(String, nullable=True)
    html = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    generation_summary = Column(Text, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class WorkItem(Base):
    """A legacy-compatible project root, milestone, or executable task."""

    __tablename__ = "work_items"
    __table_args__ = (UniqueConstraint("project_id", "local_key", name="uq_work_item_local_key"),)

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True, nullable=True)
    parent_id = Column(String, nullable=True)
    type = Column(String, nullable=True)
    department = Column(String, nullable=True)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    summary = Column(String, nullable=True)
    spec = Column(Text, nullable=True)
    status = Column(String, nullable=True, default="todo")

    project_id = Column(String, nullable=True, index=True)
    local_key = Column(String, nullable=True)
    kind = Column(String, nullable=True)
    executable = Column(Boolean, nullable=True)
    objective = Column(Text, nullable=True)
    scope = Column(JSON, nullable=True)
    exclusions = Column(JSON, nullable=True)
    inputs = Column(JSON, nullable=True)
    outputs = Column(JSON, nullable=True)
    acceptance_criteria = Column(JSON, nullable=True)
    required_skills = Column(JSON, nullable=True)
    responsible_role = Column(String, nullable=True)
    suggested_assignee = Column(String, nullable=True)


class WorkItemDependency(Base):
    __tablename__ = "work_item_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "from_work_item_id",
            "to_work_item_id",
            name="uq_work_item_edge",
        ),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    from_work_item_id = Column(String, nullable=False, index=True)
    to_work_item_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class AgentSpec(Base):
    __tablename__ = "agent_specs"
    __table_args__ = (UniqueConstraint("work_item_id", name="uq_agent_spec_work_item"),)

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    work_item_id = Column(String, nullable=False, index=True)
    source_spec_version_id = Column(String, nullable=False, index=True)
    dependency_work_item_ids = Column(JSON, nullable=False, default=list)
    content = Column(JSON, nullable=False)
    content_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False)
    actor_id = Column(String, nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ProcessedCommand(Base):
    __tablename__ = "processed_commands"
    __table_args__ = (UniqueConstraint("session_id", "command_id", name="uq_processed_command"),)

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    command_id = Column(String, nullable=False)
    input_hash = Column(String(64), nullable=False)
    state_version = Column(Integer, nullable=False)
    result = Column(JSON, nullable=False)
    side_effect_refs = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CommandAttempt(Base):
    """Durable two-phase command preparation, distinct from a successful receipt."""

    __tablename__ = "command_attempts"
    __table_args__ = (UniqueConstraint("session_id", "command_id", name="uq_command_attempt"),)

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    command_id = Column(String, nullable=False)
    input_hash = Column(String(64), nullable=False)
    expected_state_version = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PREPARING")
    prepare_owner_id = Column(String, nullable=True)
    prepare_owner_started_at = Column(DateTime(timezone=True), nullable=True)
    prepared_payload = Column(JSON, nullable=True)
    agent_call_ids = Column(JSON, nullable=False, default=list)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    prepared_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class CommandJob(Base):
    """Durable execution envelope for one asynchronous Session command."""

    __tablename__ = "command_jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "command_id", name="uq_command_job"),
        Index(
            "uq_command_job_active_session",
            "session_id",
            unique=True,
            sqlite_where=text("status IN ('pending', 'processing')"),
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    command_id = Column(String, nullable=False)
    input_hash = Column(String(64), nullable=False)
    request_payload = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default="pending")
    status_version = Column(Integer, nullable=False, default=1)
    result = Column(JSON, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    progress_stage = Column(String, nullable=True)
    progress_message = Column(Text, nullable=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class PrdVersion(Base):
    """Projection binding an immutable Spec revision to its Gitea PRD file."""

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("wi", "version", name="uq_prd_version"),
        UniqueConstraint("spec_version_id", name="uq_prd_spec_version"),
    )

    wi = Column(String, primary_key=True)
    version = Column(Integer, primary_key=True)
    spec_version_id = Column(String, nullable=False, index=True)
    filename = Column(Text, nullable=False)
    pr_number = Column(Integer, nullable=False)
    commit_sha = Column(String, nullable=True)
    # ``content_hash`` is the normalized Markdown transport hash.  The
    # immutable structured Spec hash remains a separate provenance bridge.
    content_hash = Column(String(64), nullable=False)
    spec_content_hash = Column(String(64), nullable=False)
    change_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CommentIndex(Base):
    """Disposable local index of authoritative Gitea review comments."""

    __tablename__ = "comments_index"

    id = Column(Integer, primary_key=True)
    wi = Column(String, nullable=False, index=True)
    pr_number = Column(Integer, nullable=False)
    path = Column(Text, nullable=False)
    line = Column(Integer, nullable=False)
    author_type = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    resolved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ReviewTask(Base):
    """Durable snapshot and progress record for one review publish attempt."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint(
            "wi",
            "base_version",
            "comment_snapshot_hash",
            name="uq_review_task_snapshot",
        ),
    )

    id = Column(String, primary_key=True)
    wi = Column(String, nullable=False, index=True)
    initiator_actor_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    base_version = Column(Integer, nullable=False)
    base_commit_sha = Column(String, nullable=False)
    comment_ids = Column(JSON, nullable=False, default=list)
    comment_snapshot = Column(JSON, nullable=False, default=list)
    comment_snapshot_hash = Column(String(64), nullable=False)
    auto_resolve_findings = Column(Boolean, nullable=False, default=False)
    finding_snapshot = Column(JSON, nullable=False, default=list)
    decision_history_snapshot = Column(JSON, nullable=False, default=list)
    reply_receipts = Column(JSON, nullable=False, default=dict)
    new_version = Column(Integer, nullable=True)
    new_spec_version_id = Column(String, nullable=True)
    new_commit_sha = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class WorkflowState(Base):
    __tablename__ = "workflow_states"

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True, nullable=False)
    current_phase = Column(String, default="requirement_discovery")
    clarification_round = Column(Integer, default=0)
    status = Column(String, default="active")
    waiting_for_user = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class WorkItemRun(Base):
    """Append-only execution ledger for the WorkItem dependency DAG.

    Current execution state is derived from the latest run per work item
    rather than stored on ``WorkItem`` itself, which keeps execution history
    auditable and lets the command guard admit new rows without opening a
    mutation exemption for existing ones.
    """

    __tablename__ = "work_item_runs"
    __table_args__ = (
        UniqueConstraint("work_item_id", "command_id", name="uq_work_item_run_command"),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    work_item_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    actor_id = Column(String, nullable=True)
    command_id = Column(String, nullable=False, index=True)
    agent_call_id = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
