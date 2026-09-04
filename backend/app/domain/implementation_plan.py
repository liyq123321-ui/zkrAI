"""Detailed implementation contracts, shared by initial and existing task planning."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def reject_blank_text(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("implementation details must not be blank")
        if isinstance(value, list) and any(isinstance(item, str) and not item.strip() for item in value):
            raise ValueError("implementation details must not contain blank entries")
        return value


class InterfaceField(_PlanModel):
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    required: bool
    description: str = Field(min_length=1)


class InterfaceDefinition(_PlanModel):
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    inputs: list[InterfaceField]
    outputs: list[InterfaceField]
    error_handling: list[str] = Field(min_length=1)


class DataStructureDefinition(_PlanModel):
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    validation_rules: list[str] = Field(min_length=1)


class DesignDecision(_PlanModel):
    decision: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    status: Literal["FIXED", "PROPOSED"]


class ImplementationStep(_PlanModel):
    step_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    implementation_method: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    verification_method: str = Field(min_length=1)


class ImplementationPlan(_PlanModel):
    overview: str = Field(min_length=1)
    interface_notes: str = Field(min_length=1)
    interfaces: list[InterfaceDefinition]
    data_structures: list[DataStructureDefinition]
    design_decisions: list[DesignDecision]
    steps: list[ImplementationStep] = Field(min_length=1)
