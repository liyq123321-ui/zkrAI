import pytest

from app.domain.types import CommandAction, ProjectPhase, SpecStatus
from app.domain.workflow import (
    IllegalAction,
    WorkflowSnapshot,
    assert_action_allowed,
    legal_actions,
    next_action,
)


@pytest.mark.parametrize(
    ("phase", "spec_status", "expected"),
    [
        (
            ProjectPhase.NEED_CLARIFICATION,
            None,
            (CommandAction.MESSAGE, CommandAction.SKIP_CLARIFICATION),
        ),
        (ProjectPhase.SPECIFICATION, None, (CommandAction.CREATE_SPEC,)),
        (
            ProjectPhase.REVIEW,
            SpecStatus.HUMAN_REVIEW,
            (
                CommandAction.APPROVE,
                CommandAction.REJECT,
                CommandAction.REWORK,
                CommandAction.RESTORE_SPEC_VERSION,
            ),
        ),
        (ProjectPhase.REVIEW, SpecStatus.AUTO_REVIEW, (CommandAction.CREATE_SPEC,)),
        (
            ProjectPhase.REVIEW,
            SpecStatus.REWORK,
            (
                CommandAction.APPROVE,
                CommandAction.REVISE,
                CommandAction.RESTORE_SPEC_VERSION,
            ),
        ),
        (
            ProjectPhase.REVIEW,
            SpecStatus.APPROVED,
            (
                CommandAction.CONVERT_TO_WORK_ITEM,
                CommandAction.RESTORE_SPEC_VERSION,
            ),
        ),
        (ProjectPhase.AGENT_SPECS_READY, SpecStatus.APPROVED, ()),
    ],
)
def test_legal_actions_are_derived(phase, spec_status, expected):
    assert legal_actions(WorkflowSnapshot(phase=phase, spec_status=spec_status)) == expected


def test_review_clarification_allows_message():
    assert legal_actions(
        WorkflowSnapshot(ProjectPhase.REVIEW, SpecStatus.NEED_CLARIFICATION)
    ) == (CommandAction.MESSAGE, CommandAction.RESTORE_SPEC_VERSION)


@pytest.mark.parametrize(
    ("phase", "spec_status"),
    [
        (ProjectPhase.REVIEW, SpecStatus.AUTO_REVIEW),
        (ProjectPhase.REVIEW, SpecStatus.APPROVED),
        (ProjectPhase.REVIEW, SpecStatus.REJECTED),
        (ProjectPhase.AGENT_SPECS_READY, SpecStatus.APPROVED),
    ],
)
def test_publish_review_is_rejected_outside_open_human_review_states(
    phase, spec_status
):
    with pytest.raises(IllegalAction):
        assert_action_allowed(
            WorkflowSnapshot(phase=phase, spec_status=spec_status),
            CommandAction.PUBLISH_REVIEW,
        )


def test_unapproved_spec_cannot_be_converted():
    with pytest.raises(IllegalAction) as exc_info:
        assert_action_allowed(
            WorkflowSnapshot(ProjectPhase.REVIEW, SpecStatus.HUMAN_REVIEW),
            CommandAction.CONVERT_TO_WORK_ITEM,
        )

    assert exc_info.value.code == "ILLEGAL_ACTION"
    assert exc_info.value.action is CommandAction.CONVERT_TO_WORK_ITEM
    assert exc_info.value.legal_actions == (
        CommandAction.APPROVE,
        CommandAction.REJECT,
        CommandAction.REWORK,
        CommandAction.RESTORE_SPEC_VERSION,
    )


def test_publish_review_is_internal_but_still_accepted_for_coordinator():
    snapshot = WorkflowSnapshot(ProjectPhase.REVIEW, SpecStatus.HUMAN_REVIEW)

    assert CommandAction.PUBLISH_REVIEW not in legal_actions(snapshot)
    assert_action_allowed(snapshot, CommandAction.PUBLISH_REVIEW)


@pytest.mark.parametrize(
    ("phase", "spec_status", "expected"),
    [
        (ProjectPhase.INTAKE, None, "WAIT_FOR_PM_ANALYSIS"),
        (ProjectPhase.NEED_CLARIFICATION, None, "ANSWER_CLARIFICATION"),
        (ProjectPhase.SPECIFICATION, None, "CREATE_SPEC"),
        (ProjectPhase.REVIEW, SpecStatus.AUTO_REVIEW, "RETRY_AUTO_REVIEW"),
        (ProjectPhase.REVIEW, SpecStatus.HUMAN_REVIEW, "HUMAN_REVIEW"),
        (ProjectPhase.REVIEW, SpecStatus.REWORK, "REVISE_SPEC"),
        (ProjectPhase.REVIEW, SpecStatus.NEED_CLARIFICATION, "ANSWER_CLARIFICATION"),
        (ProjectPhase.REVIEW, SpecStatus.APPROVED, "CONVERT_TO_WORK_ITEM"),
        (ProjectPhase.AGENT_SPECS_READY, SpecStatus.APPROVED, "NONE"),
    ],
)
def test_next_action_is_stable(phase, spec_status, expected):
    assert next_action(WorkflowSnapshot(phase=phase, spec_status=spec_status)) == expected


@pytest.mark.parametrize(
    ("phase", "spec_status"),
    [
        (ProjectPhase.SPECIFICATION, SpecStatus.APPROVED),
        (ProjectPhase.NEED_CLARIFICATION, SpecStatus.HUMAN_REVIEW),
    ],
)
def test_invalid_phase_status_combinations_have_no_next_action(phase, spec_status):
    snapshot = WorkflowSnapshot(phase=phase, spec_status=spec_status)
    assert legal_actions(snapshot) == ()
    assert next_action(snapshot) == "NONE"
