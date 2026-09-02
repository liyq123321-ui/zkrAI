from app.domain.types import ReviewFinding, ReviewVerdict, SemanticReview, SpecStatus
from app.services.spec_review import merge_review_outcome, run_rule_review


def test_missing_acceptance_coverage_requires_rework(valid_spec):
    """Removing requirement coverage must prevent a spec reaching human review."""
    broken = valid_spec.model_copy(update={"acceptance_criteria": []})

    findings = run_rule_review(broken, set(broken.source_refs))

    assert [item.code for item in findings] == ["MISSING_ACCEPTANCE_COVERAGE"]
    assert merge_review_outcome(findings, None) is SpecStatus.REWORK


def test_blocking_human_decision_requests_clarification(valid_spec, passing_semantic_review):
    """A blocking decision must route the workflow back to a human, not rework."""
    payload = valid_spec.model_dump()
    payload["open_questions"] = [
        {"question": "Which region owns the data?", "blocking": True}
    ]
    broken = type(valid_spec).model_validate(payload)

    findings = run_rule_review(broken, set(broken.source_refs))

    assert [item.code for item in findings] == ["NEEDS_HUMAN_DECISION"]
    assert merge_review_outcome(findings, passing_semantic_review) is SpecStatus.NEED_CLARIFICATION


def test_rule_review_emits_each_stable_code_without_incidental_findings(valid_spec):
    """Changing an individual structural rule must retain the public rule-code contract."""
    duplicate = valid_spec.functional_requirements[0].model_copy()
    cases = [
        (valid_spec.model_copy(update={"background_and_goals": []}), "MISSING_SECTION"),
        (
            valid_spec.model_copy(update={"functional_requirements": [duplicate, duplicate]}),
            "DUPLICATE_REQUIREMENT_ID",
        ),
        (valid_spec.model_copy(update={"acceptance_criteria": []}), "MISSING_ACCEPTANCE_COVERAGE"),
        (
            valid_spec.model_copy(
                update={
                    "acceptance_criteria": [
                        valid_spec.acceptance_criteria[0].model_copy(
                            update={"verification_method": "TBD"}
                        ),
                        valid_spec.acceptance_criteria[1],
                    ]
                }
            ),
            "UNVERIFIABLE_ACCEPTANCE",
        ),
        (valid_spec.model_copy(update={"source_refs": ["artifact:missing"]}), "UNKNOWN_SOURCE_REF"),
        (valid_spec.model_copy(update={"system_boundaries": []}), "MISSING_BOUNDARY"),
        (
            valid_spec.model_copy(update={"configurable_parts": [valid_spec.fixed_parts[0]]}),
            "MIXED_CONFIGURATION_BOUNDARY",
        ),
        (
            type(valid_spec).model_validate(
                {**valid_spec.model_dump(), "open_questions": [{"question": "Choose owner", "blocking": True}]}
            ),
            "NEEDS_HUMAN_DECISION",
        ),
        (
            valid_spec.model_copy(update={"permissions_and_responsibilities": ["TBD"]}),
            "UNKNOWN_RESPONSIBLE_ACTOR",
        ),
    ]

    for broken, expected_code in cases:
        assert [item.code for item in run_rule_review(broken, set(valid_spec.source_refs))] == [expected_code]


def test_blocking_human_decision_precedes_semantic_rework(valid_spec):
    """Reordering merge precedence would incorrectly send a human decision to rework."""
    question_spec = type(valid_spec).model_validate(
        {**valid_spec.model_dump(), "open_questions": [{"question": "Choose owner", "blocking": True}]}
    )
    semantic_rework = SemanticReview(
        verdict=ReviewVerdict.REJECT,
        findings=[
            ReviewFinding(
                code="SEMANTIC_GAP",
                severity="BLOCKER",
                spec_path="/main_flows",
                message="A flow is incomplete.",
                suggested_resolution="Complete the flow.",
                blocks_progress=True,
            )
        ],
    )

    outcome = merge_review_outcome(
        run_rule_review(question_spec, set(question_spec.source_refs)), semantic_rework
    )

    assert outcome is SpecStatus.NEED_CLARIFICATION


def test_semantic_verdicts_fail_closed_even_without_findings():
    """Dropping an empty verdict would silently promote a rejected Spec to human review."""
    rejected = SemanticReview.model_construct(verdict=ReviewVerdict.REJECT, findings=[])
    needs_info = SemanticReview.model_construct(verdict=ReviewVerdict.NEED_INFO, findings=[])

    assert merge_review_outcome([], rejected) is SpecStatus.REWORK
    assert merge_review_outcome([], needs_info) is SpecStatus.NEED_CLARIFICATION


def test_placeholder_matching_uses_token_boundaries_and_accepts_unknown_qualified_values(valid_spec):
    """Substring matching would reject concrete values such as 'unknownable' by accident."""
    harmless = valid_spec.model_copy(
        update={
            "acceptance_criteria": [
                valid_spec.acceptance_criteria[0].model_copy(
                    update={"verification_method": "unknownable integration suite"}
                ),
                valid_spec.acceptance_criteria[1],
            ]
        }
    )
    unknown = valid_spec.model_copy(
        update={
            "acceptance_criteria": [
                valid_spec.acceptance_criteria[0].model_copy(
                    update={"verification_method": "unknown-method"}
                ),
                valid_spec.acceptance_criteria[1],
            ]
        }
    )

    assert run_rule_review(harmless, set(harmless.source_refs)) == []
    assert [item.code for item in run_rule_review(unknown, set(unknown.source_refs))] == [
        "UNVERIFIABLE_ACCEPTANCE"
    ]


def test_rule_review_rejects_whitespace_sections_missing_exclusions_and_actorless_responsibility(valid_spec):
    """Whitespace and an action without an actor must not satisfy a required Spec section."""
    whitespace_background = valid_spec.model_copy(update={"background_and_goals": ["  "]})
    whitespace_boundary = valid_spec.model_copy(update={"system_boundaries": ["\t"]})
    missing_exclusions = valid_spec.model_copy(update={"exclusions": []})
    actorless = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Approve project specifications"]}
    )

    assert [item.code for item in run_rule_review(whitespace_background, set(valid_spec.source_refs))] == [
        "MISSING_SECTION"
    ]
    assert [item.code for item in run_rule_review(whitespace_boundary, set(valid_spec.source_refs))] == [
        "MISSING_BOUNDARY"
    ]
    assert [item.code for item in run_rule_review(missing_exclusions, set(valid_spec.source_refs))] == [
        "MISSING_SECTION"
    ]
    assert [item.code for item in run_rule_review(actorless, set(valid_spec.source_refs))] == [
        "UNKNOWN_RESPONSIBLE_ACTOR"
    ]


def test_rule_review_rejects_whitespace_acceptance_and_actorless_english_or_chinese_variants(valid_spec):
    """An empty verification or an unnamed passive responsibility is not actionable."""
    blank_method = valid_spec.model_copy(
        update={
            "acceptance_criteria": [
                valid_spec.acceptance_criteria[0].model_copy(update={"verification_method": "  "}),
                valid_spec.acceptance_criteria[1],
            ]
        }
    )
    blank_result = valid_spec.model_copy(
        update={
            "acceptance_criteria": [
                valid_spec.acceptance_criteria[0],
                valid_spec.acceptance_criteria[1].model_copy(update={"expected_result": "\t"}),
            ]
        }
    )
    actorless_english = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Must be approved before release"]}
    )
    actorless_chinese = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["负责审批发布申请"]}
    )
    named_english = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Release manager approves release requests"]}
    )
    named_passive_english = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Release requests are approved by the release manager"]}
    )
    named_chinese = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["项目经理负责审批发布申请"]}
    )

    assert [item.code for item in run_rule_review(blank_method, set(valid_spec.source_refs))] == [
        "UNVERIFIABLE_ACCEPTANCE"
    ]
    assert [item.code for item in run_rule_review(blank_result, set(valid_spec.source_refs))] == [
        "UNVERIFIABLE_ACCEPTANCE"
    ]
    assert [item.code for item in run_rule_review(actorless_english, set(valid_spec.source_refs))] == [
        "UNKNOWN_RESPONSIBLE_ACTOR"
    ]
    assert [item.code for item in run_rule_review(actorless_chinese, set(valid_spec.source_refs))] == [
        "UNKNOWN_RESPONSIBLE_ACTOR"
    ]
    assert run_rule_review(named_english, set(valid_spec.source_refs)) == []
    assert run_rule_review(named_passive_english, set(valid_spec.source_refs)) == []
    assert run_rule_review(named_chinese, set(valid_spec.source_refs)) == []


def test_responsibility_validation_requires_an_explicit_subject_not_a_nominal_requirement(valid_spec):
    """Role-name allowlists would accept nominal statements while rejecting unfamiliar valid subjects."""
    nominal_approval = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Approval is required before release"]}
    )
    nominal_review = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["Review is mandatory before release"]}
    )
    qa_subject = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["QA approves release requests"]}
    )
    sre_subject = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["SRE owns incident response"]}
    )
    legal_subject = valid_spec.model_copy(
        update={"permissions_and_responsibilities": ["法务负责审批发布申请"]}
    )

    assert [item.code for item in run_rule_review(nominal_approval, set(valid_spec.source_refs))] == [
        "UNKNOWN_RESPONSIBLE_ACTOR"
    ]
    assert [item.code for item in run_rule_review(nominal_review, set(valid_spec.source_refs))] == [
        "UNKNOWN_RESPONSIBLE_ACTOR"
    ]
    assert run_rule_review(qa_subject, set(valid_spec.source_refs)) == []
    assert run_rule_review(sre_subject, set(valid_spec.source_refs)) == []
    assert run_rule_review(legal_subject, set(valid_spec.source_refs)) == []


def test_responsibility_validation_uses_explicit_actor_to_duty_grammar(valid_spec):
    """Actorless obligation nouns cannot stand in for an accountable role."""
    actorless = [
        "Release approval requires sign-off",
        "Production data approval is required",
        "审批发布申请",
    ]
    valid = [
        "team is responsible for release approval",
        "QA approves release requests",
        "SRE owns incident response",
        "Release requests are approved by QA",
        "审批人负责审批发布申请",
        "审核员负责审核发布申请",
    ]

    for responsibility in actorless:
        spec = valid_spec.model_copy(
            update={"permissions_and_responsibilities": [responsibility]}
        )
        assert [item.code for item in run_rule_review(spec, set(spec.source_refs))] == [
            "UNKNOWN_RESPONSIBLE_ACTOR"
        ]

    for responsibility in valid:
        spec = valid_spec.model_copy(
            update={"permissions_and_responsibilities": [responsibility]}
        )
        assert run_rule_review(spec, set(spec.source_refs)) == []

