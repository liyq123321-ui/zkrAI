"""Deterministic lifecycle selection and task-DAG gates driven by local YAML.

No YAML expression is evaluated as code. Planning evidence is never an approval.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.sdlc import (
    LifecycleAssessment,
    LifecyclePlan,
    LifecycleRouteDecision,
    LifecycleRouteOption,
    LifecycleRouteStage,
)


RULE_DIR = Path(__file__).resolve().parents[2] / 'rules/sdlc'

ROUTE_CONFIRMATIONS = {
    'strict_waterfall': '生命周期路线已由用户确认：采用纯线性瀑布模型；要求严格阶段门禁，上一阶段完整评审通过后才能进入下一阶段，并保留完整审计追溯。',
    'overlapping_waterfall': '生命周期路线已由用户确认：采用阶段重叠瀑布模型；核心需求先锁定，允许相邻工程阶段在已评审基线下有限重叠，最终以完整交付为主。',
    'iterative_incremental': '生命周期路线已由用户确认：采用迭代增量模型；按2至4周迭代交付可用增量，持续根据用户反馈调整，并允许分批次上线。',
}


def route_confirmation(model: str) -> str:
    try:
        return ROUTE_CONFIRMATIONS[model]
    except KeyError as error:
        fail('SDLC_SELECTION_INVALID', f'unknown lifecycle model: {model}')


class LifecycleError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f'{code}: {message}')


def fail(code, message):
    raise LifecycleError(code, message)


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            fail('SDLC_RULES_INVALID', f'duplicate YAML key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_rules() -> dict:
    files = sorted(RULE_DIR.rglob('*.yaml'))
    documents = {str(p.relative_to(RULE_DIR)).replace('\\', '/'): yaml.load(
        p.read_text(encoding='utf-8'), Loader=UniqueLoader) for p in files}
    expected_files = {'selection.yaml', 'execution.yaml', *(
        f'models/{name}.yaml' for name in ['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental'])}
    if set(documents) != expected_files or any(not isinstance(d, dict) or d.get('version') != 1 for d in documents.values()):
        fail('SDLC_RULES_INVALID', 'missing/unknown YAML document or unsupported rule version')
    policy = documents['selection.yaml']
    execution = documents['execution.yaml']['models']
    models = {v['id']: v for k, v in documents.items() if k.startswith('models/')}
    if policy['version'] != 1 or set(models) != set(execution):
        fail('SDLC_RULES_INVALID', 'unsupported version or inconsistent model catalogue')
    for model_id, model in models.items():
        if execution[model_id]['transition'] != 'previous_exit_gate':
            fail('SDLC_RULES_INVALID', f'unsupported transition semantics: {model_id}')
        stages = model['stages']
        ids = [s['id'] for s in stages]
        expected = execution[model_id]['ordered_stages']
        if model_id == 'iterative_incremental':
            expected = [execution[model_id]['global_stage'], *expected, execution[model_id]['continuous_stage']]
        if ids != expected or any(not 0 < len(s['name']) <= policy['stage_name_max_characters'] for s in stages):
            fail('SDLC_RULES_INVALID', f'invalid stages: {model_id}')
        for stage in stages:
            for section in ['activities', 'deliverables']:
                keys = [v['id'] for v in stage[section]]
                if not keys or len(keys) != len(set(keys)):
                    fail('SDLC_RULES_INVALID', f'invalid {section}: {model_id}/{stage["id"]}')
    canonical = json.dumps(documents, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'version': '1', 'hash': hashlib.sha256(canonical.encode()).hexdigest(),
            'selection': policy, 'models': models, 'execution': execution}


def _candidates(values: dict[str, bool], rules: dict) -> tuple[str, ...]:
    qualified = {route['model'] for route in rules['selection']['routes']
                 if sum(values[q] for q in route['questions']) >= route['minimum_yes']}
    for route in rules['selection']['routes']:
        if route.get('unless_qualified') in qualified:
            qualified.discard(route['model'])
    hard = rules['selection']['hard_constraints']
    if values['q4'] and hard['q4_yes_requires'] not in qualified:
        return ()
    if values['q4']:
        qualified &= {hard['q4_yes_requires']}
    return tuple(sorted(qualified))


def select_model(answers, rules=None) -> str:
    rules = rules or load_rules()
    ids = [answer.question_id for answer in answers]
    expected = {q['id'] for q in rules['selection']['questions']}
    if len(ids) != len(expected) or set(ids) != expected:
        fail('SDLC_SELECTION_INVALID', 'answer q1 through q10 exactly once')
    unknown = [a.question_id for a in answers if a.answer == 'unknown']
    fixed = {a.question_id: a.answer == 'yes' for a in answers if a.answer != 'unknown'}
    outcomes = {_candidates({**fixed, **dict(zip(unknown, combination))}, rules)
                for combination in itertools.product([False, True], repeat=len(unknown))}
    if len(outcomes) != 1 or len(next(iter(outcomes))) != 1:
        unresolved = ', '.join(unknown) or 'conflicting confirmed answers'
        fail('SDLC_NEEDS_CLARIFICATION', f'route is ambiguous or unmatched; clarify {unresolved} against selection.yaml; do not fabricate a default route')
    return next(iter(outcomes))[0]


def recommend_models(assessment: LifecycleAssessment, rules=None) -> LifecycleRouteDecision:
    """Rank two routes from PM evidence while leaving the final choice to the user."""

    rules = rules or load_rules()
    answers = {answer.question_id: answer.answer for answer in assessment.answers}
    scores: list[tuple[float, int, str]] = []
    for order, route in enumerate(rules['selection']['routes']):
        values = [answers[question] for question in route['questions']]
        yes = sum(value == 'yes' for value in values)
        unknown = sum(value == 'unknown' for value in values)
        # Confirmed matches dominate; unknowns contribute only enough to keep a
        # plausible route visible as the alternative.
        score = (yes + unknown * 0.25) / len(values)
        if route['model'] == 'strict_waterfall' and answers['q4'] == 'yes':
            score += 1
        elif route['model'] != 'strict_waterfall' and answers['q4'] == 'yes':
            score = max(0, score - 0.75)
        scores.append((score, -order, route['model']))
    scores.sort(reverse=True)

    options = []
    for index, (score, _, model_id) in enumerate(scores[:2]):
        model = rules['models'][model_id]
        matched = [answer.question_id for answer in assessment.answers if answer.answer == 'yes'
                   and answer.question_id in next(route['questions'] for route in rules['selection']['routes'] if route['model'] == model_id)]
        reason = (
            f"匹配选择项：{', '.join(matched)}" if matched
            else "当前证据较少，作为可由用户确认的备选路线"
        )
        options.append(LifecycleRouteOption(
            model=model_id,
            name=model['name'],
            rank='recommended' if index == 0 else 'alternative',
            score=round(score, 3),
            reason=reason,
            stages=[LifecycleRouteStage(
                id=stage['id'], name=stage['name'], objective=stage['objective'],
                schedule=stage['schedule_guidance'], number=stage['number'],
                scope=stage['scope'], entry_criteria=stage['entry_criteria'],
                exit_criteria=stage['exit_criteria'], owner=stage['owner'],
                deliverables=[dict(item) for item in stage['deliverables']],
            ) for stage in model['stages']],
        ))
    return LifecycleRouteDecision(
        rules_version=rules['version'], rules_hash=rules['hash'],
        assessment=assessment, options=options,
    )


def gate_checks(stage: dict, model_id: str, rules: dict) -> list[str]:
    review = rules['execution'][model_id]['review']
    level = review.get('phase_exit') or review.get(stage['id'], 'direct_stakeholder_confirmation')
    result = [stage['exit_criteria'], f'review:{level}']
    if model_id == 'strict_waterfall':
        result += review['required']
    if model_id == 'iterative_incremental' and stage['id'] == 'release':
        result += rules['execution'][model_id]['release_required']
    return result


def planning_context() -> dict:
    rules = load_rules()
    for mid, model in rules['models'].items():
        for stage in model['stages']:
            stage['required_gate_checks'] = gate_checks(stage, mid, rules)
    return rules


def validate_lifecycle(breakdown, approved_spec: dict, *, required=False) -> None:
    plan = breakdown.lifecycle
    if plan is None:
        if required:
            fail('SDLC_PLAN_REQUIRED', 'new task decomposition requires a lifecycle plan')
        return  # old persisted records remain readable
    try:
        plan = LifecyclePlan.model_validate(plan.model_dump(mode='json'))
    except ValidationError as error:
        fail('SDLC_PLAN_INVALID', str(error))
    if not plan.selection_reason.strip() or not plan.operations_handover.strip() or any(not p.schedule.strip() for p in plan.phases):
        fail('SDLC_PLAN_INVALID', 'selection reason, schedule and operations handover must not be blank')
    rules = load_rules()
    if plan.rules_version != rules['version'] or plan.rules_hash != rules['hash']:
        fail('SDLC_RULES_STALE', 'regenerate the plan with the current YAML rules')
    # Facts must be traceable to an exact approved-spec string. Meaning is checked
    # by the independent Reviewer; quote matching alone cannot establish truth.
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
    evidence = list(strings(approved_spec))
    confirmed = [model_id for model_id, statement in ROUTE_CONFIRMATIONS.items()
                 if any(statement in value for value in evidence)]
    if len(confirmed) > 1:
        fail('SDLC_SELECTION_MISMATCH', 'approved spec contains conflicting confirmed routes')
    if confirmed:
        selected = confirmed[0]
        if plan.model != selected:
            fail('SDLC_SELECTION_MISMATCH', f'user confirmed {selected}, not {plan.model}')
    else:
        selected = select_model(plan.answers, rules)
        if selected != plan.model:
            fail('SDLC_SELECTION_MISMATCH', f'checklist selected {selected}, not {plan.model}')
    for answer in plan.answers:
        if answer.answer != 'unknown' and (not answer.evidence.strip() or not any(
                answer.evidence in value for value in evidence)):
            fail('SDLC_EVIDENCE_MISSING', f'{answer.question_id}: use a verbatim approved-spec quote or unknown')
    model = rules['models'][selected]
    policy = rules['execution'][selected]
    stages = {s['id']: s for s in model['stages']}
    phases = {p.milestone_key: p for p in plan.phases}
    milestones = {m.local_key: m for m in breakdown.milestones}
    tasks = {t.local_key: t for t in breakdown.tasks}
    specs = {s.work_item_key: s for s in breakdown.agent_specs}
    bindings = {t.task_key: t for t in plan.tasks}
    if len(phases) != len(plan.phases) or set(phases) != set(milestones):
        fail('SDLC_PHASE_MAPPING', 'each milestone must map to exactly one lifecycle phase')
    if len(bindings) != len(plan.tasks) or set(bindings) != set(tasks):
        fail('SDLC_TASK_MAPPING', 'each task must have exactly one lifecycle binding')
    instances = {(p.iteration, p.stage_id): p for p in plan.phases}
    if len(instances) != len(plan.phases):
        fail('SDLC_PHASE_DUPLICATE', 'duplicate stage in the same iteration')
    if selected == 'iterative_incremental':
        rounds = sorted({p.iteration for p in plan.phases if p.iteration > 0})
        if not rounds or rounds != list(range(1, max(rounds) + 1)):
            fail('SDLC_ITERATIONS', 'iterations must be contiguous and start at 1')
        expected = {(0, policy['global_stage']), (0, policy['continuous_stage'])}
        expected |= {(i, s) for i in rounds for s in policy['ordered_stages']}
        cadence = policy['iteration_weeks']
        if plan.iteration_weeks is None or plan.iteration_weeks <= 0 or (
                not cadence['minimum'] <= plan.iteration_weeks <= cadence['maximum']
                and not (plan.cadence_rationale or '').strip()):
            fail('SDLC_CADENCE', 'set 2–4 weeks or explain a positive alternative cadence')
    else:
        expected = {(0, s) for s in policy['ordered_stages']}
        if plan.iteration_weeks is not None:
            fail('SDLC_CADENCE', 'waterfall has no iteration cadence')
    if set(instances) != expected:
        fail('SDLC_STAGE_SET', 'preserve every SOP stage; do not merge, omit or invent stages')
    members = {key: [t.local_key for t in tasks.values() if t.parent_key == key] for key in phases}
    ancestors = {}
    pending = set(tasks)
    while pending:
        ready = [k for k in pending if all(d in ancestors for d in tasks[k].dependency_keys)]
        if not ready:
            fail('SDLC_DEPENDENCIES', 'unknown prerequisite or cyclic task graph')
        for key in ready:
            ancestors[key] = set(tasks[key].dependency_keys)
            for dep in tasks[key].dependency_keys:
                ancestors[key] |= ancestors[dep]
            pending.remove(key)

    def needs(key, prerequisite, reason):
        if prerequisite not in ancestors[key]:
            fail('SDLC_GATE_DEPENDENCY', f'{key} must depend on {prerequisite}: {reason}')

    for key, phase in phases.items():
        stage = stages[phase.stage_id]
        if milestones[key].title != stage['name']:
            fail('SDLC_STAGE_NAME', f'{key} must be named {stage["name"]}')
        if milestones[key].objective != stage['objective']:
            fail('SDLC_STAGE_OBJECTIVE', f'{key} must use the objective defined for {stage["name"]}')
        if milestones[key].dependency_keys:
            fail('SDLC_MILESTONE_DEPENDENCY', f'{key} is a phase container and cannot have dependencies')
        gate = phase.exit_gate_key
        if gate not in members[key]:
            fail('SDLC_EXIT_GATE', f'{key} needs an exit review TASK in its own phase')
        for member in members[key]:
            if member != gate:
                needs(gate, member, 'exit review waits for all phase work')
        required_checks = gate_checks(stage, selected, rules)
        if not set(required_checks) <= set(bindings[gate].gate_checks):
            fail('SDLC_GATE_CHECKS', f'{gate} must plan every required exit/review check')
        for field, rule_field in [('activity_ids', 'activities'), ('deliverable_ids', 'deliverables')]:
            expected_ids = {v['id'] for v in stage[rule_field]}
            present = set()
            for task_key in members[key]:
                assigned = set(getattr(bindings[task_key], field))
                if not assigned <= expected_ids:
                    fail('SDLC_COVERAGE', f'{task_key}: unknown {field}')
                present |= assigned
            if present != expected_ids:
                fail('SDLC_COVERAGE', f'{key}: missing {field}: {sorted(expected_ids - present)}')
        for task_key in members[key]:
            binding = bindings[task_key]
            spec = specs[task_key]
            names = {o.name for o in spec.outputs if o.required}
            for output in stage['deliverables']:
                if output['id'] in binding.deliverable_ids and output['name'] not in names:
                    fail('SDLC_OUTPUT', f'{task_key}: required output {output["name"]} is absent')
            acceptance = '\n'.join(c.criterion for c in spec.acceptance_criteria)
            if any(check not in acceptance for check in binding.gate_checks):
                fail('SDLC_GATE_ACCEPTANCE', f'{task_key}: gate checks must appear in task acceptance criteria')
            if binding.special_gate:
                rule = policy.get('special_gates', {}).get(binding.special_gate)
                if not rule or phase.stage_id != rule['stage'] or (rule.get('module_required') and not binding.module_key):
                    fail('SDLC_SPECIAL_GATE', f'{task_key}: invalid special review gate')
                if f'review:{rule["review"]}' not in binding.gate_checks:
                    fail('SDLC_SPECIAL_GATE', f'{task_key}: review level missing')
                inputs = set()
                for dep in ancestors[task_key]:
                    if tasks[dep].parent_key == key and (not rule.get('module_required') or bindings[dep].module_key == binding.module_key):
                        inputs |= set(bindings[dep].deliverable_ids)
                if not set(rule['required_deliverables']) <= inputs:
                    fail('SDLC_SPECIAL_GATE', f'{task_key}: review must wait for its core/module deliverables')
                if rule.get('prerequisite_review') and not any(
                        bindings[dep].special_gate == rule['prerequisite_review'] for dep in ancestors[task_key]):
                    fail('SDLC_SPECIAL_GATE', f'{task_key}: overview architecture review must come first')
                if rule.get('prerequisite_review'):
                    for dep in ancestors[task_key]:
                        if bindings[dep].module_key == binding.module_key and set(bindings[dep].deliverable_ids) & set(rule['required_deliverables']):
                            if not any(bindings[prior].special_gate == rule['prerequisite_review'] for prior in ancestors[dep]):
                                fail('SDLC_SPECIAL_GATE', f'{dep}: module detail work must wait for overview review')

    sequence = policy.get('module_sequence')
    if sequence:
        for key, binding in bindings.items():
            if phases[tasks[key].parent_key].stage_id != sequence['stage'] or not binding.activity_ids:
                continue
            if not (binding.module_key or '').strip():
                fail('SDLC_MODULE_REQUIRED', f'{key}: module work requires a module key')
            if set(binding.activity_ids) & set(sequence['test_activities']) and sequence['code_activity'] not in binding.activity_ids:
                if not any(bindings[dep].module_key == binding.module_key and sequence['code_activity'] in bindings[dep].activity_ids for dep in ancestors[key]):
                    fail('SDLC_MODULE_SEQUENCE', f'{key}: module testing waits for its module coding')

    def exit_gate(iteration, stage_id):
        return instances[(iteration, stage_id)].exit_gate_key

    for key, task in tasks.items():
        phase = phases[task.parent_key]
        sid, iteration = phase.stage_id, phase.iteration
        if selected == 'iterative_incremental':
            if sid == policy['global_stage']:
                continue
            needs(key, exit_gate(0, policy['global_stage']), 'global startup before iteration/operations')
            if sid == policy['continuous_stage']:
                continue
            order = policy['ordered_stages']
            pos = order.index(sid)
            if pos:
                needs(key, exit_gate(iteration, order[pos - 1]), 'within-iteration sequence')
            elif iteration > 1:
                overlap = policy['overlap'][0]
                needs(key, exit_gate(iteration - 1, overlap['previous_iteration_entry_stage']), 'previous round must reach validation')
                if key == phase.exit_gate_key:
                    needs(key, exit_gate(iteration - 1, overlap['commitment_waits_for_previous_iteration_stage']), 'formal commitment waits for prior acceptance')
                if iteration > 2:
                    needs(key, exit_gate(iteration - 2, order[-1]), 'bounded two-iteration pipeline')
            continue
        order = policy['ordered_stages']
        pos = order.index(sid)
        if pos == 0:
            continue
        previous = exit_gate(0, order[pos - 1])
        if previous not in ancestors[key]:
            alternatives = [rule for rule in policy['overlap'] if rule['stage'] == sid]
            admitted = any(
                bindings[dep].special_gate == rule['alternative_entry_gate']
                and phases[tasks[dep].parent_key].stage_id == rule['source_stage']
                and (not rule.get('same_module') or (bindings[key].module_key is not None and bindings[key].module_key == bindings[dep].module_key))
                for rule in alternatives for dep in ancestors[key]
            )
            if not admitted or key == phase.exit_gate_key:
                needs(key, previous, 'upstream exit or explicitly allowed reviewed module/core baseline')
        if selected == 'overlapping_waterfall' and pos >= 2:
            needs(key, exit_gate(0, order[pos - 2]), 'overlap is limited to adjacent phases')


def task_lifecycle_context(plan: LifecyclePlan | None, key: str) -> dict | None:
    if plan is None:
        return None
    rules = load_rules()
    return {'model': plan.model, 'rules_version': plan.rules_version, 'rules_hash': plan.rules_hash,
            'selection_reason': plan.selection_reason,
            'phases': [p.model_dump(mode='json') for p in plan.phases],
            'task': next(t.model_dump(mode='json') for t in plan.tasks if t.task_key == key),
            'model_rules': rules['models'][plan.model], 'execution_rules': rules['execution'][plan.model]}
