from app.services.session_service import (
    get_workflow_state,
    update_workflow_state,
    increment_clarification_round,
)


PHASE_REQUIREMENT_DISCOVERY = "requirement_discovery"
PHASE_CLARIFICATION = "clarification"
PHASE_DESIGN = "design"
PHASE_ALLOCATION = "allocation"
PHASE_APPROVAL = "approval"
PHASE_SPECIFICATION = "specification"
PHASE_COMPLETED = "completed"


def get_current_phase(session_id: str):
    state = get_workflow_state(session_id)

    if state is None:
        return None

    return state["current_phase"]


def start_requirement_discovery(session_id: str):
    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_REQUIREMENT_DISCOVERY,
        status="active",
        waiting_for_user=True,
    )


def start_clarification(session_id: str):
    round_number = increment_clarification_round(session_id)

    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_CLARIFICATION,
        status="active",
        waiting_for_user=True,
    )

    return round_number


def start_design(session_id: str):
    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_DESIGN,
        status="active",
        waiting_for_user=False,
    )


def start_allocation(session_id: str):
    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_ALLOCATION,
        status="active",
        waiting_for_user=False,
    )


def start_approval(session_id: str):
    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_APPROVAL,
        status="waiting_approval",
        waiting_for_user=True,
    )


def start_specification(session_id: str):
    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_SPECIFICATION,
        status="active",
        waiting_for_user=False,
    )


def complete_workflow(session_id: str):
    update_workflow_state(
        session_id=session_id,
        current_phase=PHASE_COMPLETED,
        status="completed",
        waiting_for_user=False,
    )
