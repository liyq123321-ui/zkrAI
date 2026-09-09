"""Use the same SDLC validator as firstFlight without a server or an LLM."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from app.domain.sdlc import LifecyclePlan, SelectionAnswer
from app.domain.types import WorkBreakdown
from app.services.sdlc_rules import UniqueLoader, load_rules, select_model
from app.services.decomposition_service import validate_breakdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['catalogue', 'schema', 'select', 'validate'])
    parser.add_argument('file', nargs='?', type=Path)
    args = parser.parse_args()
    if args.command == 'catalogue':
        rules = load_rules()
        result = {'version': rules['version'], 'hash': rules['hash'], 'models': {
            key: [s['name'] for s in value['stages']] for key, value in rules['models'].items()}}
    elif args.command == 'schema':
        result = LifecyclePlan.model_json_schema()
    else:
        if args.file is None:
            parser.error('select/validate requires a JSON or YAML file')
        data = yaml.load(args.file.read_text(encoding='utf-8-sig'), Loader=UniqueLoader)
        if args.command == 'select':
            result = {'model': select_model([SelectionAnswer.model_validate(v) for v in data['answers']])}
        else:
            validate_breakdown(WorkBreakdown.model_validate(data['breakdown']), data['approved_spec'],
                               require_implementation_plan=False, require_lifecycle=True)
            result = {'valid': True, 'model': data['breakdown']['lifecycle']['model']}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, KeyError, OSError, yaml.YAMLError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
