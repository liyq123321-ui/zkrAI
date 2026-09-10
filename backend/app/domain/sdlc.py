"""Structured planning evidence; approval evidence is deliberately not model output."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuleModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SelectionAnswer(RuleModel):
    question_id: str
    answer: Literal['yes', 'no', 'unknown']
    evidence: str


class LifecycleAssessment(RuleModel):
    """PM evidence used by deterministic YAML route ranking."""

    answers: list[SelectionAnswer] = Field(min_length=10, max_length=10)
    summary: str = Field(min_length=1, max_length=1200)

    @model_validator(mode='after')
    def validate_question_set(self):
        expected = {f'q{index}' for index in range(1, 11)}
        actual = [answer.question_id for answer in self.answers]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError('answers must contain q1 through q10 exactly once')
        return self


class LifecycleRouteStage(RuleModel):
    id: str
    name: str
    objective: str
    schedule: str
    number: int = 0
    scope: Literal['project', 'global', 'iteration', 'continuous'] = 'project'
    entry_criteria: str = ''
    exit_criteria: str = ''
    owner: str = ''
    deliverables: list[dict[str, str]] = Field(default_factory=list)


class LifecycleRouteOption(RuleModel):
    model: Literal['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental']
    name: str
    rank: Literal['recommended', 'alternative']
    score: float = Field(ge=0)
    reason: str
    stages: list[LifecycleRouteStage] = Field(min_length=1)


class LifecycleRouteDecision(RuleModel):
    rules_version: str
    rules_hash: str
    assessment: LifecycleAssessment
    options: list[LifecycleRouteOption] = Field(min_length=2, max_length=2)
    selected_model: Literal['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental'] | None = None


class LifecyclePhase(RuleModel):
    milestone_key: str
    stage_id: str
    iteration: int = Field(
        ge=0,
        description=(
            "Iteration cycle index: use 0 for every waterfall phase; "
            "iterative delivery rounds start at 1. This is not the stage number."
        ),
    )
    exit_gate_key: str
    schedule: str = Field(min_length=1)


class LifecycleTask(RuleModel):
    task_key: str
    activity_ids: list[str]
    deliverable_ids: list[str]
    module_key: str | None = None
    special_gate: Literal['core_requirements_review', 'architecture_review', 'module_design_review'] | None = None
    # This is a plan for a future review, not a recorded approval.
    gate_checks: list[str]


class LifecyclePlan(RuleModel):
    rules_version: str
    rules_hash: str
    model: Literal['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental']
    answers: list[SelectionAnswer] = Field(min_length=10, max_length=10)
    selection_reason: str = Field(min_length=1)
    phases: list[LifecyclePhase] = Field(min_length=1)
    tasks: list[LifecycleTask] = Field(min_length=1)
    iteration_weeks: int | None = None
    cadence_rationale: str | None = None
    operations_handover: str = Field(min_length=1)
