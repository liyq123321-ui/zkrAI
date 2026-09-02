import uuid
from datetime import datetime

from app.database.database import SessionLocal
from app.database.models import (
    Conversation,
    Message,
    WorkflowState,
)


def create_session(
    workflow: str,
    agent: str
):
    db = SessionLocal()

    try:
        session_id = str(uuid.uuid4())

        session = Conversation(
            id=session_id,
            workflow=workflow,
            agent=agent
        )

        db.add(session)

        state = WorkflowState(
            id=str(uuid.uuid4()),
            session_id=session_id,
            current_phase="requirement_discovery",
            clarification_round=0,
            status="active",
            waiting_for_user=True
        )

        db.add(state)

        db.commit()

        return session_id

    finally:
        db.close()


def session_exists(
    session_id: str
) -> bool:
    db = SessionLocal()

    try:
        session = (
            db.query(Conversation)
            .filter(
                Conversation.id == session_id
            )
            .first()
        )

        return session is not None

    finally:
        db.close()


def add_message(
    session_id: str,
    role: str,
    content: str
):
    db = SessionLocal()

    try:
        message = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content
        )

        db.add(message)
        db.commit()

    finally:
        db.close()


def get_history(
    session_id: str
):
    db = SessionLocal()

    try:
        messages = (
            db.query(Message)
            .filter(
                Message.session_id == session_id
            )
            .order_by(
                Message.created_at
            )
            .all()
        )

        return [
            {
                "role": m.role,
                "content": m.content
            }
            for m in messages
        ]

    finally:
        db.close()


def get_workflow_state(
    session_id: str
):
    db = SessionLocal()

    try:
        state = (
            db.query(WorkflowState)
            .filter(
                WorkflowState.session_id == session_id
            )
            .first()
        )

        if state is None:
            return None

        return {
            "id": state.id,
            "session_id": state.session_id,
            "current_phase": state.current_phase,
            "clarification_round": state.clarification_round,
            "status": state.status,
            "waiting_for_user": state.waiting_for_user,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    finally:
        db.close()


def update_workflow_state(
    session_id: str,
    current_phase=None,
    clarification_round=None,
    status=None,
    waiting_for_user=None
):
    db = SessionLocal()

    try:
        state = (
            db.query(WorkflowState)
            .filter(
                WorkflowState.session_id == session_id
            )
            .first()
        )

        if state is None:
            return False

        if current_phase is not None:
            state.current_phase = current_phase

        if clarification_round is not None:
            state.clarification_round = clarification_round

        if status is not None:
            state.status = status

        if waiting_for_user is not None:
            state.waiting_for_user = waiting_for_user

        state.updated_at = datetime.utcnow()

        db.commit()

        return True

    finally:
        db.close()


def increment_clarification_round(
    session_id: str
):
    db = SessionLocal()

    try:
        state = (
            db.query(WorkflowState)
            .filter(
                WorkflowState.session_id == session_id
            )
            .first()
        )

        if state is None:
            return None

        state.clarification_round += 1
        state.updated_at = datetime.utcnow()

        db.commit()

        return state.clarification_round

    finally:
        db.close()

