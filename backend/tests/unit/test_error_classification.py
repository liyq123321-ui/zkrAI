"""Public workflow-error classification contracts."""

import pytest

from app.agents.codex import AgentExecutionError, AgentOutputError
from app.services.command_service import CommandHandlerFailure, CommandHandlerRejected
from app.services.decomposition_service import BreakdownValidationError
from app.services.error_classification import classify_workflow_error


def _nested_failure(wrapper_type: type[Exception]) -> Exception:
    """Build a causal chain that contains untrusted decomposition detail."""

    try:
        raise BreakdownValidationError(
            "LOCAL_KEY_EXISTS", "root", "secret detail at /private/agent-output.json"
        )
    except BreakdownValidationError as error:
        wrapper = wrapper_type("secret wrapper text /tmp/command.log")
        raise wrapper from error


@pytest.mark.parametrize(
    "error",
    [
        BreakdownValidationError(
            "LOCAL_KEY_EXISTS", "root", "secret detail at /private/agent-output.json"
        ),
    ],
)
def test_direct_breakdown_validation_preserves_stable_code_without_detail(error):
    """Collapsing model-attributable validation codes would prevent callers from handling them."""

    result = classify_workflow_error(error)

    assert result.code == "LOCAL_KEY_EXISTS"
    assert result.status_code == 422
    assert "secret" not in result.message
    assert "/private" not in result.message


@pytest.mark.parametrize("wrapper_type", [CommandHandlerFailure, CommandHandlerRejected])
def test_nested_breakdown_validation_preserves_code_without_wrapper_detail(wrapper_type):
    """Ignoring causal decomposition failures would turn a stable client code into a generic one."""

    with pytest.raises(wrapper_type) as caught:
        _nested_failure(wrapper_type)

    result = classify_workflow_error(caught.value)

    assert result.code == "LOCAL_KEY_EXISTS"
    assert result.status_code == 422
    assert "secret" not in result.message
    assert "/tmp" not in result.message


@pytest.mark.parametrize(
    ("error", "code", "status_code"),
    [
        (AgentOutputError("secret output"), "INVALID_AGENT_RESULT", 422),
        (AgentExecutionError("secret executable path"), "AGENT_UNAVAILABLE", 503),
    ],
)
def test_agent_error_mappings_remain_public_and_stable(error, code, status_code):
    """Preserving decomposition codes must not weaken typed Agent error classification."""

    result = classify_workflow_error(error)

    assert (result.code, result.status_code) == (code, status_code)
    assert "secret" not in result.message


@pytest.mark.parametrize("wrapper_type", [CommandHandlerFailure, CommandHandlerRejected])
def test_explicit_decomposition_audit_code_precedes_nested_breakdown_code(wrapper_type):
    """Ignoring command audit evidence would replace the transport's explicit code with an inner fallback."""

    try:
        raise BreakdownValidationError(
            "LOCAL_KEY_EXISTS", "root", "secret nested detail /private/breakdown.json"
        )
    except BreakdownValidationError as nested:
        wrapper = wrapper_type(
            "secret wrapper detail /tmp/command.log",
            audit_payload={"decomposition_error_code": "SEMANTIC_REVIEW_BLOCKED"},
        )
        try:
            raise wrapper from nested
        except wrapper_type as error:
            result = classify_workflow_error(error)

    assert (result.code, result.status_code) == ("SEMANTIC_REVIEW_BLOCKED", 422)
    assert "secret" not in result.message


@pytest.mark.parametrize(
    ("agent_error", "code", "status_code"),
    [
        (AgentOutputError("secret Agent output /private/agent.json"), "INVALID_AGENT_RESULT", 422),
        (AgentExecutionError("secret executable stderr /tmp/agent.log"), "AGENT_UNAVAILABLE", 503),
    ],
)
def test_nested_agent_error_precedes_audit_and_breakdown_codes(agent_error, code, status_code):
    """Checking audit evidence before Agent causes would misclassify runtime and schema failures."""

    try:
        raise agent_error
    except type(agent_error) as agent_cause:
        breakdown = BreakdownValidationError(
            "LOCAL_KEY_EXISTS", "root", "secret breakdown detail /private/breakdown.json"
        )
        try:
            raise breakdown from agent_cause
        except BreakdownValidationError as nested:
            wrapper = CommandHandlerFailure(
                "secret wrapper detail /tmp/command.log",
                audit_payload={"decomposition_error_code": "SEMANTIC_REVIEW_BLOCKED"},
            )
            try:
                raise wrapper from nested
            except CommandHandlerFailure as error:
                result = classify_workflow_error(error)

    assert (result.code, result.status_code) == (code, status_code)
    assert "secret" not in result.message

