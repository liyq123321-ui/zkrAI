"""Transport contracts for the project workflow API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.types import (
    ClarificationQuestion,
    CommandAction,
    ProjectPhase,
    ReviewFinding,
    SpecStatus,
)


class ProjectBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivation: str = Field(min_length=1)
    final_objective: str = Field(min_length=1)
    known_scope: list[str]
    exclusions: list[str]
    reference_materials: list[str]
    expected_deliverables: list[str]
    time_constraints: str
    staffing_constraints: str
    final_approver: str = Field(min_length=1)
    project_manager_ids: list[str] = Field(default_factory=list)
    root_owner_ids: list[str] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    actor_id: str | None = Field(default=None, min_length=1)
    brief: ProjectBrief


class SessionCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    action: CommandAction
    expected_state_version: int = Field(ge=0)
    actor_id: str | None = Field(default=None, min_length=1)
    message: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_id: str
    phase: ProjectPhase
    state_version: int
    current_spec_version_id: str | None
    current_spec_status: SpecStatus | None
    legal_actions: list[CommandAction]
    next_action: str
    outstanding_questions: list[ClarificationQuestion]
    review_findings: list[ReviewFinding]


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    state: SessionState
    created_resource_ids: list[str] = Field(default_factory=list)


CommandJobStatus = Literal["pending", "processing", "succeeded", "failed"]


class CommandJobError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class CommandJobAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    status: CommandJobStatus
    status_url: str
    events_url: str


class CommandJobRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    status: CommandJobStatus
    status_version: int = Field(ge=1)
    result: CommandResult | None = None
    error: CommandJobError | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


CommandSubmission = CommandResult | CommandJobAccepted
