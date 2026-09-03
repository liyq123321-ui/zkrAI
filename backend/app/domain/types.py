"""Strict domain contracts for workflow agents."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_plain_text(value: str, *, max_length: int, field_name: str) -> str:
    """Canonicalize bounded plain text at public and Agent contract boundaries."""
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must not contain NUL")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must not exceed {max_length} characters")
    return normalized


class ProjectPhase(StrEnum):
    INTAKE = "INTAKE"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    SPECIFICATION = "SPECIFICATION"
    REVIEW = "REVIEW"
    DECOMPOSITION = "DECOMPOSITION"
    AGENT_SPECS_READY = "AGENT_SPECS_READY"


class SpecStatus(StrEnum):
    DRAFT = "DRAFT"
    AUTO_REVIEW = "AUTO_REVIEW"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REWORK = "REWORK"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    REJECTED = "REJECTED"


class CommandAction(StrEnum):
    MESSAGE = "message"
    SKIP_CLARIFICATION = "skip_clarification"
    CREATE_SPEC = "create_spec"
    REVISE = "revise"
    APPROVE = "approve"
    REJECT = "reject"
    REWORK = "rework"
    PUBLISH_REVIEW = "publish_review"
    CONVERT_TO_WORK_ITEM = "convert_to_work_item"
    RESTORE_SPEC_VERSION = "restore_spec_version"


class ReviewKind(StrEnum):
    RULE = "RULE"
    AGENT = "AGENT"
    HUMAN = "HUMAN"


class ReviewVerdict(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    NEED_INFO = "NEED_INFO"


class RewriteAction(StrEnum):
    MODIFIED = "MODIFIED"
    CLARIFIED = "CLARIFIED"
    NOT_ACCEPTED = "NOT_ACCEPTED"
    NEEDS_HUMAN_CONFIRMATION = "NEEDS_HUMAN_CONFIRMATION"


class WorkItemKind(StrEnum):
    ROOT = "ROOT"
    MILESTONE = "MILESTONE"
    TASK = "TASK"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(pattern=r"^(FR|NFR)-[0-9]{3}$")
    statement: str = Field(min_length=1)
    priority: str = Field(pattern=r"^(MUST|SHOULD|COULD)$")


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_ids: list[str] = Field(min_length=1)
    criterion: str = Field(min_length=1)
    verification_method: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    blocking: bool
    risk_owner: str | None = None
    accepted_consequence: str | None = None


class ProjectSpecPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background_and_goals: list[str] = Field(min_length=1)
    users_and_scenarios: list[str] = Field(min_length=1)
    functional_requirements: list[Requirement] = Field(min_length=1)
    non_functional_requirements: list[Requirement]
    system_boundaries: list[str] = Field(min_length=1)
    exclusions: list[str]
    fixed_parts: list[str]
    configurable_parts: list[str]
    extension_points: list[str]
    core_objects: list[str] = Field(min_length=1)
    main_flows: list[str] = Field(min_length=1)
    exceptional_flows: list[str]
    permissions_and_responsibilities: list[str] = Field(min_length=1)
    deliverable_requirements: list[str] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    risks: list[str]
    assumptions: list[str]
    open_questions: list[OpenQuestion]
    source_refs: list[str] = Field(min_length=1)


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(BLOCKER|MAJOR|MINOR|INFO)$")
    spec_path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    suggested_resolution: str = Field(min_length=1)
    blocks_progress: bool


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    affected_areas: list[str] = Field(min_length=1)
    blocking: bool

    @field_validator("question_id", "question", "reason")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("clarification question text must not be blank")
        return normalized

    @field_validator("affected_areas")
    @classmethod
    def normalize_affected_areas(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("clarification affected areas must not contain blank values")
        return normalized


class ProjectBriefUpdates(BaseModel):
    """Confirmed content changes; human authorization fields are not Agent-editable.

    Null/omitted fields retain their current value. Lists replace the whole field,
    so an explicit empty list clears it rather than resurrecting superseded scope.
    """

    model_config = ConfigDict(extra="forbid")

    motivation: str | None = Field(default=None, min_length=1)
    final_objective: str | None = Field(default=None, min_length=1)
    known_scope: list[str] | None = None
    exclusions: list[str] | None = None
    reference_materials: list[str] | None = None
    expected_deliverables: list[str] | None = None
    time_constraints: str | None = None
    staffing_constraints: str | None = None

    @field_validator("motivation", "final_objective")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Brief text must not be blank")
        return value.strip()

    def apply_to(self, brief: dict[str, object]) -> dict[str, object]:
        return {**brief, **self.model_dump(mode="json", exclude_none=True)}


class ClarificationAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready_for_spec: bool
    questions: list[ClarificationQuestion]
    assumptions: list[str]
    brief_updates: ProjectBriefUpdates = Field(default_factory=ProjectBriefUpdates)

    @model_validator(mode="after")
    def validate_readiness_consistency(self):
        blocking = [question for question in self.questions if question.blocking]
        if self.ready_for_spec and blocking:
            raise ValueError("ready_for_spec cannot be true while blocking questions remain")
        if not self.ready_for_spec and not blocking:
            raise ValueError("ready_for_spec=false requires an answerable blocking question")
        return self


class SemanticReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    findings: list[ReviewFinding]


class CommentResponse(BaseModel):
    """PM's truthful outcome for one frozen Gitea review comment."""

    model_config = ConfigDict(extra="forbid")

    comment_id: int = Field(gt=0)
    action: RewriteAction
    note: str = Field(min_length=1, max_length=4000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        return normalize_plain_text(value, max_length=4000, field_name="note")


class PrdRewriteOutput(BaseModel):
    """Strict structured output returned by the PM rewrite node."""

    model_config = ConfigDict(extra="forbid")

    spec: ProjectSpecPayload
    responses: list[CommentResponse] = Field(min_length=1)
    change_summary: str = Field(min_length=1, max_length=8000)

    @field_validator("change_summary")
    @classmethod
    def normalize_change_summary(cls, value: str) -> str:
        return normalize_plain_text(value, max_length=8000, field_name="change_summary")


class OutputDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    format: str = Field(min_length=1)
    required: bool


class WorkItemProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_key: str = Field(min_length=1)
    parent_key: str | None = None
    kind: WorkItemKind
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    dependency_keys: list[str]


class AgentSpecProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_item_key: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    exclusions: list[str]
    context_refs: list[str] = Field(min_length=1)
    inputs: list[str] = Field(min_length=1)
    outputs: list[OutputDefinition] = Field(min_length=1)
    fixed_constraints: list[str]
    configurable_parts: list[str]
    extension_points: list[str]
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    required_skills: list[str] = Field(min_length=1)
    allowed_tools: list[str]
    allowed_paths: list[str]
    responsible_role: str = Field(min_length=1)
    suggested_assignee: str = Field(min_length=1)
    dependency_keys: list[str]
    test_obligations: list[str] = Field(min_length=1)
    risks: list[str]
    open_questions: list[OpenQuestion]


class WorkBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    milestones: list[WorkItemProposal] = Field(min_length=1)
    tasks: list[WorkItemProposal] = Field(min_length=1)
    agent_specs: list[AgentSpecProposal] = Field(min_length=1)


class WorkBreakdownRevision(BaseModel):
    """Replacement records for affected existing items; omitted items stay intact."""

    model_config = ConfigDict(extra="forbid")

    milestones: list[WorkItemProposal] = Field(default_factory=list)
    tasks: list[WorkItemProposal] = Field(default_factory=list)
    agent_specs: list[AgentSpecProposal] = Field(default_factory=list)
