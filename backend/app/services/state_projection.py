"""Single source of truth for current workflow state projection."""

from sqlalchemy import desc, exists
from sqlalchemy.orm import Session

from app.database.models import (
    ClarificationRequest,
    ClarificationResponse,
    Project,
    SpecReview,
    SpecVersion,
)
from app.domain.types import ClarificationQuestion, ProjectPhase, ReviewFinding, SpecStatus
from app.domain.workflow import WorkflowSnapshot, legal_actions, next_action
from app.schemas.workflow import SessionState


def current_spec(db: Session, project: Project) -> SpecVersion | None:
    return db.get(SpecVersion, project.current_spec_version_id) if project.current_spec_version_id else None


def followup_spec_version_id(
    current_spec_version_id: str | None, current_spec_status: SpecStatus | None
) -> str | None:
    """Bind follow-up questions to the active Spec only at its clarification gate."""

    if current_spec_status is SpecStatus.NEED_CLARIFICATION:
        return current_spec_version_id
    return None


def active_clarification_request(
    db: Session, project: Project, version: SpecVersion | None = None
) -> ClarificationRequest | None:
    """Return the latest unanswered request for the active workflow boundary."""

    version = version if version is not None else current_spec(db, project)
    spec_status = SpecStatus(version.status) if version is not None else None
    if spec_status is SpecStatus.NEED_CLARIFICATION:
        binding = ClarificationRequest.spec_version_id == version.id
    elif ProjectPhase(project.phase) is ProjectPhase.NEED_CLARIFICATION and version is None:
        binding = ClarificationRequest.spec_version_id.is_(None)
    else:
        return None
    answered = exists().where(
        ClarificationResponse.clarification_request_id == ClarificationRequest.id
    )
    return (
        db.query(ClarificationRequest)
        .filter(
            ClarificationRequest.project_id == project.id,
            binding,
            ~answered,
        )
        .order_by(
            desc(ClarificationRequest.analysis_round),
            desc(ClarificationRequest.created_at),
            desc(ClarificationRequest.id),
        )
        .first()
    )

def project_state(db: Session, project: Project) -> SessionState:
    """Build the complete detached state returned by every command and query."""

    version = current_spec(db, project)
    phase = ProjectPhase(project.phase)
    spec_status = SpecStatus(version.status) if version is not None else None
    snapshot = WorkflowSnapshot(phase, spec_status)
    request = active_clarification_request(db, project, version)
    questions = (
        [ClarificationQuestion.model_validate(item) for item in request.questions]
        if request is not None
        else []
    )
    reviews = (
        db.query(SpecReview)
        .filter_by(spec_version_id=version.id)
        .order_by(SpecReview.created_at, SpecReview.id)
        .all()
        if version is not None
        else []
    )
    findings = [
        ReviewFinding.model_validate(item)
        for review in reviews
        for item in (review.findings or [])
    ]
    return SessionState(
        session_id=project.session_id,
        project_id=project.id,
        phase=phase,
        state_version=project.state_version,
        current_spec_version_id=project.current_spec_version_id,
        current_spec_status=spec_status,
        legal_actions=list(legal_actions(snapshot)),
        next_action=next_action(snapshot),
        outstanding_questions=questions,
        review_findings=findings,
    )

