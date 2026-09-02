"""Stable public errors for workflow transports such as HTTP and SSE."""

from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.codex import AgentExecutionError, AgentOutputError
from app.domain.workflow import IllegalAction
from app.services.command_service import (
    CommandConflict,
    CommandHandlerFailure,
    CommandHandlerRejected,
    CommandInDoubt,
    ForbiddenActor,
    StaleState,
)
from app.services.decomposition_service import (
    BreakdownValidationError,
    DecompositionAgentFailure,
    DecompositionNotAllowed,
)
from app.services.project_service import (
    ClarificationAlreadyAnswered,
    ClarificationNotAllowed,
    RequestConflict,
)
from app.services.spec_service import (
    AgentCallInDoubt,
    SpecGenerationNotAllowed,
    SpecRevisionNotAllowed,
)


@dataclass(frozen=True, slots=True)
class PublicWorkflowError:
    """The only error details a workflow transport may disclose."""

    code: str
    message: str
    status_code: int


def _causes(error: Exception) -> list[BaseException]:
    causes: list[BaseException] = []
    cursor: BaseException | None = error
    while cursor is not None and cursor not in causes:
        causes.append(cursor)
        cursor = cursor.__cause__ or cursor.__context__
    return causes


def classify_workflow_error(error: Exception) -> PublicWorkflowError:
    """Classify causal errors without exposing exception text, stderr, or local paths."""

    causes = _causes(error)
    if any(isinstance(item, (AgentOutputError, ValidationError)) for item in causes):
        return PublicWorkflowError(
            "INVALID_AGENT_RESULT",
            "An Agent response did not satisfy the required contract.",
            422,
        )
    if any(isinstance(item, AgentExecutionError) for item in causes):
        return PublicWorkflowError(
            "AGENT_UNAVAILABLE",
            "An Agent operation is unavailable; retry the workflow action.",
            503,
        )
    if isinstance(error, CommandHandlerRejected) and error.audit_payload.get(
        "decomposition_error_code"
    ):
        return PublicWorkflowError(
            str(error.audit_payload["decomposition_error_code"]),
            "The workflow request did not pass review.",
            422,
        )
    if isinstance(error, CommandHandlerFailure) and error.audit_payload.get(
        "decomposition_error_code"
    ) not in {None, "AGENT_FAILURE"}:
        return PublicWorkflowError(
            str(error.audit_payload["decomposition_error_code"]),
            "The workflow request did not pass validation.",
            422,
        )
    breakdown_error = next(
        (item for item in causes if isinstance(item, BreakdownValidationError)),
        None,
    )
    if breakdown_error is not None:
        return PublicWorkflowError(
            breakdown_error.code,
            "The workflow request did not pass validation.",
            422,
        )
    if isinstance(error, IllegalAction):
        return PublicWorkflowError(
            "ILLEGAL_ACTION",
            "The requested action is not legal in the current workflow state.",
            400,
        )
    if isinstance(error, ForbiddenActor):
        return PublicWorkflowError(
            "FORBIDDEN_ACTOR", "The actor is not allowed to perform this action.", 403
        )
    if isinstance(error, KeyError):
        return PublicWorkflowError("NOT_FOUND", "The requested workflow resource was not found.", 404)
    if isinstance(error, RequestConflict):
        return PublicWorkflowError(
            "REQUEST_CONFLICT",
            "The workflow state changed or the request conflicts; retry with current state.",
            409,
        )
    if isinstance(error, (CommandConflict, StaleState, ClarificationAlreadyAnswered)):
        code = str(getattr(error, "code", error.__class__.__name__.upper()))
        return PublicWorkflowError(
            code, "The workflow state changed or the request conflicts; retry with current state.", 409
        )
    if isinstance(error, (CommandHandlerRejected, ValidationError, ValueError)):
        return PublicWorkflowError("VALIDATION_ERROR", "The workflow request is invalid.", 422)
    if isinstance(
        error,
        (
            AgentCallInDoubt,
            CommandInDoubt,
            CommandHandlerFailure,
            DecompositionAgentFailure,
        ),
    ):
        return PublicWorkflowError(
            "AGENT_UNAVAILABLE",
            "An Agent operation is unavailable; retry the workflow action.",
            503,
        )
    if isinstance(
        error,
        (
            ClarificationNotAllowed,
            SpecGenerationNotAllowed,
            SpecRevisionNotAllowed,
            DecompositionNotAllowed,
        ),
    ):
        return PublicWorkflowError("ILLEGAL_ACTION", "The requested action is not legal.", 400)
    return PublicWorkflowError(
        "WORKFLOW_ERROR", "The workflow request could not be completed.", 500
    )

