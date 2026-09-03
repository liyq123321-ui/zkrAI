"""Deterministic review rules and status merge policy for Project Specs."""

from collections.abc import Iterable
import re

from app.domain.types import (
    ProjectSpecPayload,
    ReviewFinding,
    ReviewVerdict,
    SemanticReview,
    SpecStatus,
)


_PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:tbd|tbc|unknown|unassigned|to\s+be\s+determined)\b", re.IGNORECASE
)
_REQUIRED_SECTIONS = (
    "background_and_goals",
    "users_and_scenarios",
    "functional_requirements",
    "core_objects",
    "main_flows",
    "permissions_and_responsibilities",
    "deliverable_requirements",
    "exclusions",
    "source_refs",
)
_SEMANTIC_REVIEW_BLOCKING_CODES = {
    "MISSING_SECTION",
    "DUPLICATE_REQUIREMENT_ID",
    "MISSING_BOUNDARY",
    "UNKNOWN_SOURCE_REF",
    "UNKNOWN_REQUIREMENT_ID",
}
_ENGLISH_ACTIVE_DUTY_PATTERN = re.compile(
    r"^(?P<subject>.+?)\s+(?:approves?|owns?|reviews?|manages?|assigns?|submits?|"
    r"performs?|maintains?|operates?|decides?|verifies?)\b",
    re.IGNORECASE,
)
_ENGLISH_RESPONSIBLE_FOR_PATTERN = re.compile(
    r"^(?P<subject>.+?)\s+is\s+responsible\s+for\b", re.IGNORECASE
)
_ENGLISH_PASSIVE_BY_PATTERN = re.compile(
    r"\b(?:approved|owned|reviewed|managed|assigned|submitted|performed|maintained|"
    r"operated|decided|verified)\b.*?\bby\s+(?P<actor>[A-Za-z][A-Za-z0-9 _-]*[A-Za-z0-9])"
    r"(?:[.,;:]|$)",
    re.IGNORECASE,
)
_NOMINAL_DUTY_SUBJECT_PATTERN = re.compile(
    r"\b(?:approval|review|sign-off|authorization|verification|responsibility|ownership)\b$",
    re.IGNORECASE,
)
_CHINESE_RELATION_PATTERN = re.compile(r"(?:负责|承担)")
_CHINESE_PASSIVE_ACTOR_PATTERN = re.compile(
    r"由\s*(?P<actor>[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9_-]*)\s*"
    r"(?:审批|审核|批准|负责|承担)"
)


def _finding(code: str, path: str, message: str, resolution: str) -> ReviewFinding:
    return ReviewFinding(
        code=code,
        severity="BLOCKER",
        spec_path=path,
        message=message,
        suggested_resolution=resolution,
        blocks_progress=True,
    )


def _contains_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_PATTERN.search(value))


def _is_blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _section_is_missing(value: object) -> bool:
    if not value:
        return True
    if isinstance(value, list):
        return any(_is_blank(item) for item in value if isinstance(item, str))
    return _is_blank(value)


def _has_responsible_actor(value: str) -> bool:
    """Accept explicit actor-to-duty grammar, never an obligation noun by itself.

    English needs an actor subject with an active duty verb (or a passive ``by``
    actor); Chinese needs ``负责``/``承担`` or ``由<actor>审批``-style wording.
    """

    text = value.strip()
    if not text or _contains_placeholder(text):
        return False

    passive_actor = _CHINESE_PASSIVE_ACTOR_PATTERN.search(text)
    if passive_actor is not None and passive_actor.group("actor").strip():
        return True
    chinese_relation = _CHINESE_RELATION_PATTERN.search(text)
    if chinese_relation is not None:
        subject = text[: chinese_relation.start()].strip(" ：:，,。；;")
        if subject.startswith("由"):
            subject = subject[1:].strip()
        return bool(subject)

    passive = _ENGLISH_PASSIVE_BY_PATTERN.search(text)
    if passive is not None and passive.group("actor").strip():
        return True
    active = _ENGLISH_ACTIVE_DUTY_PATTERN.search(text)
    responsible_for = _ENGLISH_RESPONSIBLE_FOR_PATTERN.search(text)
    match = active or responsible_for
    if match is None:
        return False
    subject = re.sub(r"^(?:the|a|an)\s+", "", match.group("subject").strip(), flags=re.IGNORECASE)
    return bool(subject) and _NOMINAL_DUTY_SUBJECT_PATTERN.search(subject) is None


def run_rule_review(
    spec: ProjectSpecPayload, valid_input_refs: set[str]
) -> list[ReviewFinding]:
    """Return stable, ordered structural findings for one immutable Spec payload."""

    findings: list[ReviewFinding] = []
    for section in _REQUIRED_SECTIONS:
        if _section_is_missing(getattr(spec, section, None)):
            findings.append(
                _finding(
                    "MISSING_SECTION",
                    f"/{section}",
                    f"Required section {section} is empty.",
                    "Provide content for this required section.",
                )
            )

    requirements = [*spec.functional_requirements, *spec.non_functional_requirements]
    requirement_ids = [item.requirement_id for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        findings.append(
            _finding(
                "DUPLICATE_REQUIREMENT_ID",
                "/functional_requirements",
                "Requirement identifiers must be unique across functional and non-functional requirements.",
                "Assign a unique identifier to every requirement.",
            )
        )

    covered_ids = {
        requirement_id
        for criterion in spec.acceptance_criteria
        for requirement_id in criterion.requirement_ids
    }
    for index, criterion in enumerate(spec.acceptance_criteria):
        unknown_ids = sorted(set(criterion.requirement_ids) - set(requirement_ids))
        if unknown_ids:
            findings.append(_finding(
                "UNKNOWN_REQUIREMENT_ID", f"/acceptance_criteria/{index}/requirement_ids",
                f"Acceptance references undefined requirements: {', '.join(unknown_ids)}.",
                "Link to an existing FR/NFR requirement, or define an evidence-backed requirement and update its references.",
            ))
        if (not criterion.criterion.strip()
                or len(criterion.requirement_ids) != len(set(criterion.requirement_ids))):
            findings.append(_finding(
                "INVALID_ACCEPTANCE", f"/acceptance_criteria/{index}",
                "Acceptance text must be non-blank and requirement references must be unique.",
                "Complete the acceptance text and remove duplicate references.",
            ))
    if not requirements or set(requirement_ids) - covered_ids:
        findings.append(
            _finding(
                "MISSING_ACCEPTANCE_COVERAGE",
                "/acceptance_criteria",
                "Requirements missing acceptance coverage: "
                + ", ".join(sorted(set(requirement_ids) - covered_ids)) + ".",
                "Add verifiable acceptance criteria for each uncovered requirement.",
            )
        )

    if any(
        not criterion.verification_method.strip()
        or not criterion.expected_result.strip()
        or _contains_placeholder(criterion.verification_method)
        or _contains_placeholder(criterion.expected_result)
        for criterion in spec.acceptance_criteria
    ):
        findings.append(
            _finding(
                "UNVERIFIABLE_ACCEPTANCE",
                "/acceptance_criteria",
                "Acceptance criteria must name a concrete verification method and expected result.",
                "Replace placeholder verification details with testable outcomes.",
            )
        )

    unknown_refs = sorted(set(spec.source_refs) - valid_input_refs)
    if unknown_refs:
        findings.append(
            _finding(
                "UNKNOWN_SOURCE_REF",
                "/source_refs",
                f"Spec references unavailable inputs: {', '.join(unknown_refs)}.",
                "Use only project inputs retained by the system.",
            )
        )

    if _section_is_missing(spec.system_boundaries):
        findings.append(
            _finding(
                "MISSING_BOUNDARY",
                "/system_boundaries",
                "System boundaries are required.",
                "Describe the in-scope system boundary.",
            )
        )

    fixed = {item.strip() for item in spec.fixed_parts if item.strip()}
    configurable = {item.strip() for item in spec.configurable_parts if item.strip()}
    extensions = {item.strip() for item in spec.extension_points if item.strip()}
    if fixed & configurable or fixed & extensions or configurable & extensions:
        findings.append(
            _finding(
                "MIXED_CONFIGURATION_BOUNDARY",
                "/fixed_parts",
                "A concern cannot be both fixed, configurable, or an extension point.",
                "Classify each concern in exactly one boundary category.",
            )
        )

    if any(question.blocking for question in spec.open_questions):
        findings.append(
            _finding(
                "NEEDS_HUMAN_DECISION",
                "/open_questions",
                "A blocking decision needs a human answer before progress.",
                "Resolve the blocking open question with the accountable human.",
            )
        )

    if any(not _has_responsible_actor(item) for item in spec.permissions_and_responsibilities):
        findings.append(
            _finding(
                "UNKNOWN_RESPONSIBLE_ACTOR",
                "/permissions_and_responsibilities",
                "A permission or responsibility has no accountable actor.",
                "Name the responsible role or actor.",
            )
        )
    return findings


def merge_review_outcome(
    rule_findings: Iterable[ReviewFinding], semantic_review: SemanticReview | None
) -> SpecStatus:
    """Apply the stable precedence for automatic-review findings."""

    all_findings = list(rule_findings)
    if semantic_review is not None:
        all_findings.extend(semantic_review.findings)
        if semantic_review.verdict is ReviewVerdict.NEED_INFO:
            return SpecStatus.NEED_CLARIFICATION
    if any(item.code == "NEEDS_HUMAN_DECISION" and item.blocks_progress for item in all_findings):
        return SpecStatus.NEED_CLARIFICATION
    if semantic_review is not None and semantic_review.verdict is ReviewVerdict.REJECT:
        return SpecStatus.REWORK
    if any(item.blocks_progress for item in all_findings):
        return SpecStatus.REWORK
    return SpecStatus.HUMAN_REVIEW


def prevents_semantic_review(findings: Iterable[ReviewFinding]) -> bool:
    """Whether structural defects leave the Reviewer Agent without a valid Spec."""

    return any(
        finding.blocks_progress and finding.code in _SEMANTIC_REVIEW_BLOCKING_CODES
        for finding in findings
    )
