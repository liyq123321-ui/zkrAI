import hashlib
from copy import deepcopy

import pytest

from app.domain.sdlc import SelectionAnswer
from app.services.sdlc_rules import LifecycleError, load_rules, select_model, validate_lifecycle, gate_checks
from app.services.decomposition_service import BreakdownValidationError, validate_breakdown
from tests.helpers.sdlc import lifecycle_fixture


@pytest.mark.parametrize('model', ['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental'])
def test_complete_models_pass_both_validators(model):
    approved, plan = lifecycle_fixture(model, iterations=2)
    validate_breakdown(plan, approved, require_implementation_plan=False, require_lifecycle=True)


def test_original_sop_hash_and_iteration_numbering():
    from app.services.sdlc_rules import RULE_DIR
    rules = load_rules()
    source = RULE_DIR.parents[2] / rules['selection']['source']['path']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == rules['selection']['source']['sha256']
    assert [s['number'] for s in rules['models']['iterative_incremental']['stages']] == [0, 1, 2, 3, 4, 0]


def answer_set(yes=(), unknown=()):
    return [SelectionAnswer(question_id=f'q{i}', answer='yes' if i in yes else 'unknown' if i in unknown else 'no',
                            evidence='synthetic') for i in range(1, 11)]


@pytest.mark.parametrize('answers, expected', [
    (answer_set([1, 2, 3]), 'strict_waterfall'),
    (answer_set([5, 6]), 'overlapping_waterfall'),
    (answer_set([8, 9]), 'iterative_incremental'),
    (answer_set([1, 2, 4], [3]), 'strict_waterfall'),
    (answer_set([5, 6], [3]), 'overlapping_waterfall'),
])
def test_deterministic_selection_and_non_decisive_unknowns(answers, expected):
    assert select_model(answers) == expected


@pytest.mark.parametrize('answers', [answer_set(), answer_set([1, 2], [3]), answer_set([1, 2, 3, 8, 9]),
                                    answer_set([5, 6, 8, 9]), answer_set([4, 8, 9]), answer_set([], range(1, 11))])
def test_unknown_ambiguous_no_match_and_hard_sequence_fail_closed(answers):
    with pytest.raises(LifecycleError, match='SDLC_NEEDS_CLARIFICATION'):
        select_model(answers)


@pytest.mark.parametrize('mutation, code', [
    (lambda b: setattr(b, 'lifecycle', None), 'SDLC_PLAN_REQUIRED'),
    (lambda b: setattr(b.lifecycle, 'rules_hash', 'stale'), 'SDLC_RULES_STALE'),
    (lambda b: setattr(b.lifecycle.answers[0], 'evidence', 'not in PRD'), 'SDLC_EVIDENCE_MISSING'),
    (lambda b: setattr(b.milestones[0], 'title', '自造阶段'), 'SDLC_STAGE_NAME'),
    (lambda b: b.lifecycle.phases.pop(), 'SDLC_PHASE_MAPPING'),
    (lambda b: b.lifecycle.tasks.pop(), 'SDLC_TASK_MAPPING'),
    (lambda b: b.lifecycle.tasks[0].activity_ids.clear(), 'SDLC_COVERAGE'),
    (lambda b: b.agent_specs[0].outputs.clear(), 'SDLC_OUTPUT'),
    (lambda b: b.lifecycle.tasks[1].gate_checks.clear(), 'SDLC_GATE_CHECKS'),
    (lambda b: setattr(b.agent_specs[1].acceptance_criteria[0], 'criterion', 'skip'), 'SDLC_GATE_ACCEPTANCE'),
    (lambda b: b.tasks[2].dependency_keys.clear(), 'SDLC_GATE_DEPENDENCY'),
    (lambda b: b.tasks[1].dependency_keys.clear(), 'SDLC_GATE_DEPENDENCY'),
])
def test_unsafe_plans_are_rejected(mutation, code):
    approved, plan = lifecycle_fixture()
    mutation(plan)
    with pytest.raises(LifecycleError, match=code):
        validate_lifecycle(plan, approved, required=True)


def test_duplicate_question_cannot_bias_vote():
    answers = answer_set([1, 2, 3])
    answers[9] = answers[0]
    with pytest.raises(LifecycleError, match='SDLC_SELECTION_INVALID'):
        select_model(answers)


def test_iterative_planning_can_overlap_previous_verification_but_commitment_waits():
    approved, plan = lifecycle_fixture('iterative_incremental', iterations=2)
    by_key = {t.local_key: t for t in plan.tasks}
    by_key['i2-planning-work'].dependency_keys = ['i1-development-gate']
    by_key['i2-planning-gate'].dependency_keys.append('i1-verification-gate')
    validate_lifecycle(plan, approved, required=True)
    by_key['i2-planning-gate'].dependency_keys.remove('i1-verification-gate')
    with pytest.raises(LifecycleError, match='SDLC_GATE_DEPENDENCY'):
        validate_lifecycle(plan, approved, required=True)


def test_nonadjacent_waterfall_overlap_is_rejected():
    approved, plan = lifecycle_fixture('overlapping_waterfall')
    task = next(t for t in plan.tasks if t.local_key == 'i0-acceptance-work')
    task.dependency_keys = ['i0-design-gate']
    with pytest.raises(LifecycleError, match='SDLC_GATE_DEPENDENCY'):
        validate_lifecycle(plan, approved, required=True)


def test_no_module_testing_before_coding():
    approved, plan = lifecycle_fixture('overlapping_waterfall')
    binding = next(t for t in plan.lifecycle.tasks if t.task_key == 'i0-implementation-work')
    binding.activity_ids.remove('implementation.a1')
    gate_binding = next(t for t in plan.lifecycle.tasks if t.task_key == 'i0-implementation-gate')
    gate_binding.activity_ids.append('implementation.a1')
    gate_binding.module_key = 'main'
    with pytest.raises(LifecycleError, match='SDLC_MODULE_SEQUENCE'):
        validate_lifecycle(plan, approved, required=True)


def test_legacy_record_readable_but_new_planning_requires_lifecycle():
    approved, plan = lifecycle_fixture()
    plan.lifecycle = None
    validate_lifecycle(plan, approved)
    with pytest.raises(BreakdownValidationError, match='SDLC_PLAN_REQUIRED'):
        validate_breakdown(plan, approved, require_implementation_plan=False, require_lifecycle=True)


def test_reviewed_core_baseline_allows_only_adjacent_design_overlap():
    approved, plan = lifecycle_fixture('overlapping_waterfall')
    gate = plan.tasks[1].model_copy(deep=True)
    gate.local_key = 'core-review'
    spec = plan.agent_specs[1].model_copy(deep=True)
    spec.work_item_key = gate.local_key
    binding = plan.lifecycle.tasks[1].model_copy(deep=True)
    binding.task_key, binding.special_gate = gate.local_key, 'core_requirements_review'
    plan.tasks.append(gate)
    plan.agent_specs.append(spec)
    plan.lifecycle.tasks.append(binding)
    plan.tasks[1].dependency_keys.append(gate.local_key)
    plan.tasks[2].dependency_keys = [gate.local_key]
    plan.tasks[3].dependency_keys.append('i0-requirements-gate')
    validate_lifecycle(plan, approved, required=True)
    gate.dependency_keys.clear()
    with pytest.raises(LifecycleError, match='SDLC_SPECIAL_GATE'):
        validate_lifecycle(plan, approved, required=True)


def test_reviewed_module_can_code_before_other_designs_finish():
    approved, plan = lifecycle_fixture('overlapping_waterfall')
    by_key = {t.local_key: t for t in plan.tasks}
    bindings = {t.task_key: t for t in plan.lifecycle.tasks}
    design = by_key['i0-design-work']
    # Split architecture from the module detail, making both review boundaries real.
    overview_binding = bindings[design.local_key]
    overview_binding.deliverable_ids = ['design.d1']
    overview_binding.activity_ids = ['design.a1', 'design.a2', 'design.a3']
    def extra(key, deps, special=None, module=None, outputs=(), activities=()):
        task = design.model_copy(deep=True)
        task.local_key, task.dependency_keys = key, deps
        spec = plan.agent_specs[2].model_copy(deep=True)
        spec.work_item_key, spec.dependency_keys = key, deps
        binding = overview_binding.model_copy(deep=True)
        binding.task_key, binding.special_gate, binding.module_key = key, special, module
        binding.deliverable_ids, binding.activity_ids = list(outputs), list(activities)
        binding.gate_checks = ['review:technical_lead_decision' if special == 'module_design_review' else 'review:formal_unanimous'] if special else []
        spec.acceptance_criteria[0].criterion += '\n' + '\n'.join(binding.gate_checks)
        plan.tasks.append(task)
        plan.agent_specs.append(spec)
        plan.lifecycle.tasks.append(binding)
        return task, binding
    extra('architecture-review', [design.local_key], special='architecture_review')
    extra('module-detail', ['architecture-review'], module='main', outputs=['design.d2'], activities=['design.a4', 'design.a5'])
    module_review, module_binding = extra('module-review', ['module-detail'], special='module_design_review', module='main')
    by_key['i0-design-gate'].dependency_keys.append(module_review.local_key)
    by_key['i0-implementation-work'].dependency_keys = [module_review.local_key]
    by_key['i0-implementation-gate'].dependency_keys.append('i0-design-gate')
    validate_lifecycle(plan, approved, required=True)
    module_binding.module_key = 'different-module'
    with pytest.raises(LifecycleError, match='SDLC_SPECIAL_GATE'):
        validate_lifecycle(plan, approved, required=True)
