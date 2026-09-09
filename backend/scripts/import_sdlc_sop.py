"""One-time, reproducible import of the user supplied SOP (no network)."""
from pathlib import Path
import hashlib
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]


def main(source: Path) -> None:
    raw = source.read_bytes()
    text = raw.decode('utf-8-sig')
    target = ROOT / 'backend/rules/sdlc'
    (target / 'models').mkdir(parents=True, exist_ok=True)
    docs = ROOT / 'docs/sdlc'
    docs.mkdir(parents=True, exist_ok=True)
    (docs / 'source.md').write_bytes(raw)
    headings = list(re.finditer(r'^## 模式[一二三]：(.+)$', text, re.M))
    names = ['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental']
    stage_ids = [
        ['requirements', 'design', 'implementation', 'acceptance', 'delivery', 'operations'],
        ['requirements', 'design', 'implementation', 'acceptance', 'operations'],
        ['backlog', 'planning', 'development', 'verification', 'release', 'operations'],
    ]
    def write(path, data):
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding='utf-8')
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index < 2 else text.index('## 模型自动选择')
        body = text[heading.end():end]
        sections = {}
        for match in re.finditer(r'^### (.+)\n([\s\S]*?)(?=^### |\Z)', body, re.M):
            sections[match[1]] = match[2].strip().removesuffix('---').strip()
        rows = []
        for line in body.splitlines():
            if not line.startswith('|'):
                continue
            cells = [v.strip() for v in line.strip('|').split('|')]
            if cells[0] in ['阶段编号', '阶段名称'] or cells[0].startswith('-'):
                continue
            if cells[0].isdigit():
                cells = cells[1:]
            rows.append(cells)
        stages = []
        for number, (sid, row) in enumerate(zip(stage_ids[index], rows, strict=True), 1):
            if len(row) == 3:  # continuous operations
                row = [row[0], '贯穿全周期保障运行与文档同步', '待办池建立', row[1], row[2],
                       '交接时文档与运行状态同步；长期服务不阻塞迭代', '运维 + 产品负责人', '持续']
            elif len(row) == 7:
                row.append('仅一次，不计入单轮迭代比例')
            stage = dict(zip(['name', 'objective', 'entry_criteria', 'work', 'deliverables',
                              'exit_criteria', 'owner', 'schedule_guidance'], row, strict=True))
            stage.update(id=sid, number=(number - 1 if index == 2 and sid not in ['backlog', 'operations'] else
                         0 if index == 2 else number), scope=('global' if sid == 'backlog' else
                         'continuous' if index == 2 and sid == 'operations' else
                         'iteration' if index == 2 else 'project'))
            stage['activities'] = [{'id': f'{sid}.a{n}', 'name': value} for n, value in
                                   enumerate(stage.pop('work').split('、'), 1)]
            outputs = re.findall(r'《[^》]+》(?:（[^）]+）)?|[^、《》]+', stage.pop('deliverables'))
            stage['deliverables'] = [{'id': f'{sid}.d{n}', 'name': value.strip()} for n, value in
                                     enumerate(outputs, 1) if value.strip()]
            stages.append(stage)
        model = {'version': 1, 'id': names[index], 'name': heading[1], 'stages': stages,
                 'source_sections': sections}
        write(target / 'models' / f'{names[index]}.yaml', model)
    questions = re.findall(r'^\d+\. \[ \] (.+)$', text, re.M)
    write(target / 'selection.yaml', {
        'version': 1,
        'source': {'path': 'docs/sdlc/source.md', 'sha256': hashlib.sha256(raw).hexdigest()},
        'trigger': 'after_clarification_before_decomposition',
        'answers': {'values': ['yes', 'no', 'unknown'], 'evidence': 'verbatim_approved_spec_quote',
                    'unknown_policy': 'evaluate_all_completions_require_same_result'},
        'questions': [{'id': f'q{i}', 'question': q} for i, q in enumerate(questions, 1)],
        'routes': [
            {'model': names[0], 'questions': ['q1','q2','q3','q4'], 'minimum_yes': 3},
            {'model': names[1], 'questions': ['q5','q6','q7'], 'minimum_yes': 2,
             'unless_qualified': names[0]},
            {'model': names[2], 'questions': ['q8','q9','q10'], 'minimum_yes': 2},
        ],
        'conflict_policy': 'block_for_clarification', 'no_match_policy': 'block_for_clarification',
        'hard_constraints': {'q4_yes_requires': names[0]},
        'stage_name_max_characters': 4,
        'planning': {'require_all_stages': True, 'require_all_activities': True,
                     'require_all_deliverables': True, 'require_exit_gate_task': True,
                     'forbid_fabricated_approvals': True, 'schedule_percentages': 'guidance_not_additive',
                     'continuous_operations': 'bounded_handover_plus_service_obligations'},
    })


if __name__ == '__main__':
    main(Path(sys.argv[1]))
