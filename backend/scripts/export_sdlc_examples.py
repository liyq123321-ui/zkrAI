from pathlib import Path
import json
import sys
import yaml

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'backend'))
from app.domain.sdlc import LifecyclePlan
from app.services.sdlc_rules import load_rules
from tests.helpers.sdlc import lifecycle_fixture

docs = root / 'docs/sdlc'
(docs / 'examples').mkdir(exist_ok=True)
(docs / 'lifecycle.schema.json').write_text(json.dumps(LifecyclePlan.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
rules = load_rules()
template = {'note': '先完成需求澄清；unknown 不是 no，不要把此模板直接当作可执行计划。',
            'answers': [{'question_id': q['id'], 'answer': 'unknown', 'evidence': ''} for q in rules['selection']['questions']]}
(docs / 'selection.template.yaml').write_text(yaml.safe_dump(template, allow_unicode=True, sort_keys=False), encoding='utf-8')
for model in rules['models']:
    approved, plan = lifecycle_fixture(model, iterations=2 if model == 'iterative_incremental' else 1)
    sample = {'notice': '合成测试数据，仅示范规则格式，不表示真实选型、证据或已通过评审。',
              'approved_spec': approved, 'breakdown': plan.model_dump(mode='json')}
    (docs / 'examples' / f'{model}.json').write_text(json.dumps(sample, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
