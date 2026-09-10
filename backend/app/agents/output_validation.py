"""Output checks shared by automatic repair and the persistence review gates."""

from collections.abc import Mapping

from pydantic import BaseModel

from app.domain.types import (
    BaseWorkBreakdown,
    PrdRewriteOutput,
    ProjectSpecPayload,
    WorkBreakdown,
    WorkBreakdownRevision,
)
from app.domain.types import AgentSpecProposal
from app.domain.implementation_plan import ImplementationPlan
from app.services.task_specifications import ImplementationPlanError, validate_implementation_plan
from app.services.spec_review import run_rule_review
from app.services.drawio_diagrams import (
    diagram_entity_labels,
    normalize_agent_table_layout,
)


class OutputConsistencyError(ValueError):
    def __init__(self, findings: list[dict[str, object]]) -> None:
        self.findings = findings
        super().__init__("; ".join(f"{item['code']}: {item['message']}" for item in findings))


def _one_edit_apart(left: str, right: str) -> bool:
    """Return whether identifiers differ by one edit or adjacent transposition."""

    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        differences = [
            index
            for index, (a, b) in enumerate(zip(left, right, strict=True))
            if a != b
        ]
        if len(differences) == 1:
            return True
        return (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = long_index = differences = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        differences += 1
        if differences > 1:
            return False
        long_index += 1
    return True


def _repair_context_refs(
    result: WorkBreakdown | WorkBreakdownRevision, allowed_refs: set[str]
) -> None:
    """Correct only unambiguous one-character mistakes in typed Agent output."""

    for agent_spec in result.agent_specs:
        repaired: list[str] = []
        for reference in agent_spec.context_refs:
            if reference in allowed_refs:
                repaired.append(reference)
                continue
            namespace, separator, _ = reference.partition(":")
            candidates = [
                allowed
                for allowed in allowed_refs
                if separator
                and allowed.partition(":")[:2] == (namespace, separator)
                and _one_edit_apart(reference, allowed)
            ]
            repaired.append(candidates[0] if len(candidates) == 1 else reference)
        agent_spec.context_refs = repaired


def _repair_required_outputs(
    result: WorkBreakdown | WorkBreakdownRevision,
) -> None:
    """Promote one existing deliverable when the Agent marked all as optional."""

    for agent_spec in result.agent_specs:
        if agent_spec.outputs and not any(output.required for output in agent_spec.outputs):
            agent_spec.outputs[0].required = True


def _normalize_base_breakdown(
    result: BaseWorkBreakdown | WorkBreakdown | WorkBreakdownRevision,
) -> None:
    """Keep the structural stage small even if the Agent emitted full plans."""

    for agent_spec in result.agent_specs:
        if hasattr(agent_spec, "implementation_plan"):
            agent_spec.implementation_plan = None


def _normalize_waterfall_iterations(
    result: BaseWorkBreakdown | WorkBreakdown | WorkBreakdownRevision,
    selected_model: str,
) -> None:
    """Treat a waterfall phase's iteration as its cycle index, never stage order.

    The ordered stage number is already defined by the selected SDLC YAML.  Models
    sometimes copy that number into ``iteration`` (1, 2, 3, ...), but waterfall
    has no delivery rounds, so the only valid and unambiguous cycle index is 0.
    """

    if selected_model not in {"strict_waterfall", "overlapping_waterfall"}:
        return
    lifecycle = result.lifecycle
    if lifecycle is None or lifecycle.model != selected_model:
        return
    for phase in lifecycle.phases:
        phase.iteration = 0


def merge_breakdown_revision(revision: WorkBreakdownRevision, payload: dict[str, object]) -> WorkBreakdown:
    previous = WorkBreakdown.model_validate(payload.get("previous_breakdown"))
    merged = previous.model_dump(mode="json")
    if revision.lifecycle is not None:
        if previous.lifecycle is not None and revision.lifecycle.model != previous.lifecycle.model:
            raise OutputConsistencyError([{
                "code": "SDLC_ROUTE_LOCKED", "path": "lifecycle.model",
                "message": "Route changes require renewed clarification and a new approved PRD.",
            }])
        merged["lifecycle"] = revision.lifecycle.model_dump(mode="json")
    for section, key_field in (("milestones", "local_key"), ("tasks", "local_key"), ("agent_specs", "work_item_key")):
        changes = getattr(revision, section)
        allowed = {item[key_field] for item in merged[section]}
        replacements = {}
        for item in changes:
            key = getattr(item, key_field)
            if key not in allowed or key in replacements:
                raise OutputConsistencyError([{
                    "code": "INVALID_REVISION_KEY", "path": f"{section}[{key}]",
                    "message": "Revisions must target an existing item exactly once; preserve all other items.",
                }])
            replacements[key] = item.model_dump(mode="json")
        merged[section] = [replacements.get(item[key_field], item) for item in merged[section]]
    return WorkBreakdown.model_validate(merged)


def validate_node_output(result: BaseModel, payload: dict[str, object]) -> None:
    """Repair structural omissions; human decisions remain in the review workflow."""
    if isinstance(result, (BaseWorkBreakdown, WorkBreakdown, WorkBreakdownRevision)):
        if payload.get("decomposition_stage") == "base":
            _normalize_base_breakdown(result)
        _repair_required_outputs(result)
        allowed_refs = {
            reference
            for reference in payload.get("input_refs", [])
            if isinstance(reference, str)
        }
        _repair_context_refs(result, allowed_refs)
        if payload.get("selected_sdlc_model"):
            _normalize_waterfall_iterations(
                result, str(payload["selected_sdlc_model"])
            )
    if isinstance(result, WorkBreakdownRevision):
        result = merge_breakdown_revision(result, payload)
    if isinstance(result, (BaseWorkBreakdown, WorkBreakdown)) and payload.get("selected_sdlc_model"):
        selected = str(payload["selected_sdlc_model"])
        if result.lifecycle is None or result.lifecycle.model != selected:
            raise OutputConsistencyError([{
                "code": "SDLC_ROUTE_MISMATCH",
                "path": "lifecycle.model",
                "message": f"lifecycle model must match the user-confirmed route {selected}",
            }])
    if isinstance(result, ImplementationPlan):
        task = AgentSpecProposal.model_validate(payload["task_spec"])
        approved = payload["approved_spec"]
        ids = {item["requirement_id"] for section in ("functional_requirements", "non_functional_requirements")
               for item in approved.get(section, [])}
        try:
            validate_implementation_plan(result, task, ids)
        except ImplementationPlanError as error:
            raise OutputConsistencyError([{"code": error.code, "path": task.work_item_key, "message": str(error)}]) from error
        return
    if isinstance(result, (ProjectSpecPayload, PrdRewriteOutput)):
        spec = result.spec if isinstance(result, PrdRewriteOutput) else result
        source = payload.get("spec", {})
        source_content = source.get("content", {}) if isinstance(source, Mapping) else {}
        refs = payload.get("input_refs", source_content.get("source_refs", spec.source_refs))
        deferred_review_codes = {"NEEDS_HUMAN_DECISION"}
        if not payload.get("auto_resolve_review_findings"):
            deferred_review_codes.add("UNKNOWN_RESPONSIBLE_ACTOR")
        findings = [
            item.model_dump(mode="json") for item in run_rule_review(spec, set(refs))
            if item.code not in deferred_review_codes
        ]
        declared_objects = "\n".join(spec.core_objects).casefold()
        for index, diagram in enumerate(spec.er_diagrams):
            normalized_xml = normalize_agent_table_layout(diagram.drawio_xml)
            if normalized_xml != diagram.drawio_xml:
                spec.er_diagrams[index] = diagram.model_copy(update={"drawio_xml": normalized_xml})
                diagram = spec.er_diagrams[index]
            unknown_entities = sorted(
                label
                for label in diagram_entity_labels(diagram.drawio_xml)
                if label.casefold() not in declared_objects
            )
            if unknown_entities:
                findings.append({
                    "code": "ER_ENTITY_NOT_IN_CORE_OBJECTS",
                    "path": f"/er_diagrams/{index}/drawio_xml",
                    "message": (
                        "ER diagram entities must be named in core_objects: "
                        + ", ".join(unknown_entities)
                    ),
                })
        if isinstance(result, PrdRewriteOutput):
            from app.services.pm_agent import RewriteCoverageError, validate_rewrite

            expected_ids = set(payload.get("comment_ids", [item["id"] for item in payload.get("comments", [])]))
            try:
                validate_rewrite(result, expected_ids)
            except RewriteCoverageError as error:
                findings.append({
                    "code": "REWRITE_COMMENT_COVERAGE", "path": "/responses",
                    "message": str(error), "expected_comment_ids": sorted(expected_ids),
                    "actual_comment_ids": [item.comment_id for item in result.responses],
                })
        if findings:
            raise OutputConsistencyError(findings)
    elif isinstance(result, (BaseWorkBreakdown, WorkBreakdown)):
        # Services also import the gateway package; load their checks at call time.
        from app.services.decomposition_service import BreakdownValidationError, validate_breakdown

        approved = payload.get("approved_spec")
        if not isinstance(approved, Mapping):
            raise ValueError("decomposition requires an approved_spec snapshot")
        snapshot = {**approved, "source_refs": payload.get("input_refs", approved.get("source_refs", []))}
        breakdown = (
            result
            if isinstance(result, WorkBreakdown)
            else WorkBreakdown.model_validate(result.model_dump(mode="json"))
        )
        try:
            validate_breakdown(
                breakdown,
                snapshot,
                require_implementation_plan=(
                    payload.get("decomposition_stage") != "base"
                ),
                require_lifecycle="sdlc_rules" in payload,
            )
        except BreakdownValidationError as error:
            # Decomposition runs only after an approved Spec and a confirmed
            # lifecycle route exist.  A task plan must not create a new
            # blocking decision or leave a newly introduced risk unaccepted;
            # send those outputs through the bounded Agent repair loop instead
            # of letting them fail minutes later at the persistence gate.
            if error.code == "SDLC_NEEDS_CLARIFICATION":
                return
            raise OutputConsistencyError([{
                "code": error.code, "path": error.local_key, "message": str(error),
            }]) from error
