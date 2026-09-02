"""Pure, deterministic policy for the project specification workflow."""

from dataclasses import dataclass

from app.domain.types import CommandAction, ProjectPhase, SpecStatus


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    """The workflow state needed to derive commands and the next action."""

    phase: ProjectPhase
    spec_status: SpecStatus | None = None


ACTION_TABLE: dict[
    tuple[ProjectPhase, SpecStatus | None], tuple[CommandAction, ...]
] = {
    (ProjectPhase.INTAKE, None): (),
    (ProjectPhase.NEED_CLARIFICATION, None): (CommandAction.MESSAGE,),
    (ProjectPhase.SPECIFICATION, None): (CommandAction.CREATE_SPEC,),
    (ProjectPhase.REVIEW, SpecStatus.HUMAN_REVIEW): (
        CommandAction.APPROVE,
        CommandAction.REJECT,
        CommandAction.REWORK,
        CommandAction.RESTORE_SPEC_VERSION,
    ),
    (ProjectPhase.REVIEW, SpecStatus.AUTO_REVIEW): (CommandAction.CREATE_SPEC,),
    (ProjectPhase.REVIEW, SpecStatus.REWORK): (
        CommandAction.APPROVE,
        CommandAction.REVISE,
        CommandAction.RESTORE_SPEC_VERSION,
    ),
    (ProjectPhase.REVIEW, SpecStatus.NEED_CLARIFICATION): (
        CommandAction.MESSAGE,
        CommandAction.RESTORE_SPEC_VERSION,
    ),
    (ProjectPhase.REVIEW, SpecStatus.APPROVED): (
        CommandAction.CONVERT_TO_WORK_ITEM,
        CommandAction.RESTORE_SPEC_VERSION,
    ),
    (ProjectPhase.REVIEW, SpecStatus.REJECTED): (
        CommandAction.RESTORE_SPEC_VERSION,
    ),
    (ProjectPhase.AGENT_SPECS_READY, SpecStatus.APPROVED): (),
}

_INTERNAL_ACTIONS: dict[
    tuple[ProjectPhase, SpecStatus | None], tuple[CommandAction, ...]
] = {
    (ProjectPhase.REVIEW, SpecStatus.HUMAN_REVIEW): (
        CommandAction.PUBLISH_REVIEW,
    ),
    (ProjectPhase.REVIEW, SpecStatus.REWORK): (CommandAction.PUBLISH_REVIEW,),
}

NEXT_ACTION_TABLE: dict[tuple[ProjectPhase, SpecStatus | None], str] = {
    (ProjectPhase.INTAKE, None): "WAIT_FOR_PM_ANALYSIS",
    (ProjectPhase.NEED_CLARIFICATION, None): "ANSWER_CLARIFICATION",
    (ProjectPhase.SPECIFICATION, None): "CREATE_SPEC",
    (ProjectPhase.REVIEW, SpecStatus.AUTO_REVIEW): "RETRY_AUTO_REVIEW",
    (ProjectPhase.REVIEW, SpecStatus.HUMAN_REVIEW): "HUMAN_REVIEW",
    (ProjectPhase.REVIEW, SpecStatus.REWORK): "REVISE_SPEC",
    (ProjectPhase.REVIEW, SpecStatus.NEED_CLARIFICATION): "ANSWER_CLARIFICATION",
    (ProjectPhase.REVIEW, SpecStatus.APPROVED): "CONVERT_TO_WORK_ITEM",
    (ProjectPhase.AGENT_SPECS_READY, SpecStatus.APPROVED): "NONE",
}


class IllegalAction(Exception):
    """Raised when a command is not legal for the current workflow snapshot."""

    code = "ILLEGAL_ACTION"

    def __init__(
        self,
        action: CommandAction,
        legal_actions: tuple[CommandAction, ...],
    ) -> None:
        self.action = action
        self.legal_actions = legal_actions
        super().__init__(
            f"{self.code}: action {action.value!r} is not legal; "
            f"legal actions are {tuple(item.value for item in legal_actions)!r}"
        )


def legal_actions(snapshot: WorkflowSnapshot) -> tuple[CommandAction, ...]:
    """Return the commands exposed to a human for ``snapshot``."""

    return ACTION_TABLE.get((snapshot.phase, snapshot.spec_status), ())


def assert_action_allowed(snapshot: WorkflowSnapshot, action: CommandAction) -> None:
    """Raise :class:`IllegalAction` unless ``action`` is legal in ``snapshot``."""

    public = legal_actions(snapshot)
    allowed = public + _INTERNAL_ACTIONS.get(
        (snapshot.phase, snapshot.spec_status), ()
    )
    if action not in allowed:
        raise IllegalAction(action, public)


def next_action(snapshot: WorkflowSnapshot) -> str:
    """Return the stable machine-readable operation expected for ``snapshot``."""

    return NEXT_ACTION_TABLE.get((snapshot.phase, snapshot.spec_status), "NONE")
