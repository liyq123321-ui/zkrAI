"""Synthetic lifecycle fixtures, never evidence for a real project."""
from app.domain.sdlc import LifecyclePlan
from app.domain.types import WorkBreakdown, WorkItemProposal, WorkItemKind
from app.services.sdlc_rules import load_rules, gate_checks
from tests.helpers.factories import make_valid_breakdown, make_valid_spec


def lifecycle_fixture(model='strict_waterfall', iterations=1):
    rules = load_rules()
    selected = rules['models'][model]
    yes = {'strict_waterfall': {1, 2, 3, 4}, 'overlapping_waterfall': {5, 6, 7},
           'iterative_incremental': {8, 9, 10}}[model]
    answers = [{'question_id': f'q{i}', 'answer': 'yes' if i in yes else 'no',
                'evidence': f'Synthetic q{i}={"yes" if i in yes else "no"}'} for i in range(1, 11)]
    spec = make_valid_spec().model_dump(mode='json')
    spec['background_and_goals'] += [a['evidence'] for a in answers]
    # The demo is deliberately a full-lifecycle scope; legacy fixtures exclude deployment.
    spec['exclusions'] = []
    base = make_valid_breakdown()
    template = base.agent_specs[0]
    milestones, tasks, agent_specs, phases, bindings = [], [], [], [], []
    sequence = []
    for stage in selected['stages']:
        if stage['scope'] != 'iteration':
            sequence.append((0, stage))
        elif stage['id'] == 'planning':
            for i in range(1, iterations + 1):
                sequence.extend((i, s) for s in selected['stages'] if s['scope'] == 'iteration')
    previous = None
    for iteration, stage in sequence:
        prefix = f'i{iteration}-{stage["id"]}'
        work, gate = prefix + '-work', prefix + '-gate'
        checks = gate_checks(stage, model, rules)
        phases.append({'milestone_key': prefix, 'stage_id': stage['id'], 'iteration': iteration,
                       'exit_gate_key': gate, 'schedule': '按团队评估排期；示例不承诺具体日期'})
        milestones.append(WorkItemProposal(local_key=prefix, parent_key=None, kind=WorkItemKind.MILESTONE,
                                          title=stage['name'], objective=stage['objective'], dependency_keys=[]))
        prior = [previous] if previous else []
        if model == 'iterative_incremental' and stage['id'] == 'operations':
            prior = ['i0-backlog-gate']
        for key, dependencies, is_gate in [(work, prior, False), (gate, [work], True)]:
            tasks.append(WorkItemProposal(local_key=key, parent_key=prefix, kind=WorkItemKind.TASK,
                                         title=('准出评审：' if is_gate else '完成：') + stage['name'],
                                         objective=stage['objective'], dependency_keys=dependencies))
            child = template.model_dump(mode='json')
            child.update(work_item_key=key, objective=stage['objective'], dependency_keys=dependencies,
                         responsible_role=stage['owner'], scope=[stage['objective']], implementation_plan=None,
                         outputs=[{'name': '评审记录', 'format': 'Markdown', 'required': True}] if is_gate else
                         [{'name': d['name'], 'format': '按交付物类型', 'required': True} for d in stage['deliverables']],
                         acceptance_criteria=[{'requirement_ids': ['FR-001', 'NFR-001'],
                                               'criterion': '\n'.join(checks) if is_gate else stage['exit_criteria'],
                                               'verification_method': '检查交付物和真实评审证据',
                                               'expected_result': '满足全部验收条件后由责任人确认'}])
            agent_specs.append(child)
            bindings.append({'task_key': key, 'activity_ids': [] if is_gate else [a['id'] for a in stage['activities']],
                             'deliverable_ids': [] if is_gate else [d['id'] for d in stage['deliverables']],
                             'gate_checks': checks if is_gate else [],
                             'module_key': 'main' if model == 'overlapping_waterfall' and stage['id'] == 'implementation' and not is_gate else None,
                             'special_gate': None})
        previous = gate
    plan = LifecyclePlan(rules_version=rules['version'], rules_hash=rules['hash'], model=model,
                         answers=answers, selection_reason='Synthetic fixture; not real project approval',
                         phases=phases, tasks=bindings, iteration_weeks=2 if model == 'iterative_incremental' else None,
                         cadence_rationale=None, operations_handover='约定稳定观察期后交接，持续服务另行排期')
    return spec, WorkBreakdown(lifecycle=plan, milestones=milestones, tasks=tasks, agent_specs=agent_specs)
