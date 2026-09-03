"""Output checks shared by automatic repair and the persistence review gates."""

from collections.abc import Mapping

from pydantic import BaseModel

from app.domain.types import PrdRewriteOutput, ProjectSpecPayload, WorkBreakdown, WorkBreakdownRevision
from app.services.spec_review import run_rule_review


class OutputConsistencyError(ValueError):
    def __init__(self, findings: list[dict[str, object]]) -> None:
        self.findings = findings
        super().__init__("; ".join(f"{item['code']}: {item['message']}" for item in findings))


def merge_breakdown_revision(revision: WorkBreakdownRevision, payload: dict[str, object]) -> WorkBreakdown:
    previous = WorkBreakdown.model_validate(payload.get("previous_breakdown"))
    merged = previous.model_dump(mode="json")
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
    if isinstance(result, WorkBreakdownRevision):
        result = merge_breakdown_revision(result, payload)
    if isinstance(result, (ProjectSpecPayload, PrdRewriteOutput)):
        spec = result.spec if isinstance(result, PrdRewriteOutput) else result
        source = payload.get("spec", {})
        source_content = source.get("content", {}) if isinstance(source, Mapping) else {}
        refs = payload.get("input_refs", source_content.get("source_refs", spec.source_refs))
        findings = [
            item.model_dump(mode="json") for item in run_rule_review(spec, set(refs))
            if item.code not in {"NEEDS_HUMAN_DECISION", "UNKNOWN_RESPONSIBLE_ACTOR"}
        ]
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
    elif isinstance(result, WorkBreakdown):
        # Services also import the gateway package; load their checks at call time.
        from app.services.decomposition_service import BreakdownValidationError, validate_breakdown

        approved = payload.get("approved_spec")
        if not isinstance(approved, Mapping):
            raise ValueError("decomposition requires an approved_spec snapshot")
        snapshot = {**approved, "source_refs": payload.get("input_refs", approved.get("source_refs", []))}
        try:
            validate_breakdown(result, snapshot)
        except BreakdownValidationError as error:
            # A genuinely unresolved decision still fails through the service gate.
            if error.code in {"BLOCKING_OPEN_QUESTION", "UNACCEPTED_RISK"}:
                return
            raise OutputConsistencyError([{
                "code": error.code, "path": error.local_key, "message": str(error),
            }]) from error
