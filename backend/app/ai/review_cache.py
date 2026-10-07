"""Bounded read-only review cache, keyed by input, prompt, schema, model and Skills."""
import hashlib
import json
import time
from collections import OrderedDict
from copy import deepcopy


class ReviewCache:
    def __init__(self, capacity=24, ttl=900):
        self.entries = OrderedDict(); self.capacity = capacity; self.ttl = ttl

    def key(self, operation, context, instructions, contract, settings, skills):
        if operation not in ('review', 'document_quality_review', 'document_registry_review') or context.get('prepare_submission') or context.get('auto_resolution_verification'):
            return None
        model = getattr(settings, 'codex_model', None)
        if not model: return None  # An implicit CLI model cannot provide a reliable identity.
        from app.agents.codex import BUNDLED_SKILL_ROOT, _bundled_skill_digest
        fingerprints = []
        for name in skills:
            manifest = json.loads((BUNDLED_SKILL_ROOT / f'{name}-source.json').read_text(encoding='utf-8'))
            actual = _bundled_skill_digest(BUNDLED_SKILL_ROOT / name)
            if manifest.get('name') != name or actual != manifest.get('tree_sha256'): return None
            fingerprints.append((name, actual))
        data = {k: v for k, v in context.items() if k not in ('base_revision', 'base_version', 'operation_timings')}
        raw = json.dumps([operation, data, instructions, contract.model_json_schema(), model,
                          str(getattr(settings, 'codex_binary', '')), fingerprints], ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key):
        item = self.entries.get(key)
        if not item: return None
        if time.monotonic() - item[0] > self.ttl:
            self.entries.pop(key, None); return None
        self.entries.move_to_end(key)
        return deepcopy(item[1])

    def put(self, key, value):
        if not key: return
        self.entries[key] = (time.monotonic(), deepcopy(value))
        self.entries.move_to_end(key)
        while len(self.entries) > self.capacity: self.entries.popitem(last=False)
