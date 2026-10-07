"""User-confirmed decisions and scoped writing preferences, with explicit history."""
from copy import deepcopy
import re
from app.models import Document, DocumentWorkspace
from app.services.block_service import BlockError, uid


def policies(db, doc):
    rows = db.query(DocumentWorkspace).join(Document, Document.id == DocumentWorkspace.document_id).filter(Document.project_id == doc.project_id).all()
    return [(row, item) for row in rows for item in (row.state or {}).get('policies', [])
            if row.document_id == doc.id or item.get('scope') == 'project']


def knowledge_context(db, doc, blocks, consume=False):
    ids = {b['id'] for b in blocks}
    refs = set(re.findall(r'(?:NFR|FR|AC)[-_]\d+', '\n'.join(b.get('content', '') for b in blocks), re.I))
    selected = []
    for row, item in policies(db, doc):
        if item.get('status') != 'active': continue
        if item.get('block_ids') and not ids.intersection(item['block_ids']): continue
        if item.get('requirement_ids') and not {x.upper() for x in refs}.intersection(x.upper() for x in item['requirement_ids']): continue
        selected.append({k: item.get(k) for k in ('id', 'kind', 'text', 'reason', 'source', 'scope', 'block_ids', 'requirement_ids', 'actor')})
        if consume and item.get('scope') == 'once':
            state = deepcopy(row.state)
            next(p for p in state['policies'] if p['id'] == item['id'])['status'] = 'used'
            row.state, row.revision = state, row.revision + 1
    if sum(len(x.get('text') or '') + len(x.get('reason') or '') for x in selected) > 24000:
        raise BlockError('有效决定与偏好超出单次上下文容量，请缩小适用范围或撤销旧项', 422)
    return {'decisions': [x for x in selected if x['kind'] == 'decision'],
            'preferences': [x for x in selected if x['kind'] == 'preference'],
            'instruction': '遵守相关有效业务决定。若本轮指令或候选与决定冲突，返回 decision_conflicts 并说明决定 ID 和原因，等待用户更新或撤销决定；不得偷偷覆盖。审核时把矛盾列入审核问题。偏好只控制表达，不覆盖事实、数字或本轮明确要求。'}


def update_policy(workbench, document_id, actor, expected, payload):
    from app.services.document_workbench import now
    with workbench.blocks.write() as db:
        doc = workbench.blocks.document(db, document_id, actor)
        row, state = workbench._state(db, doc)
        if row.revision != expected: raise BlockError('工作台已更新，请刷新后重试')
        items = state.setdefault('policies', [])
        old = next((p for p in items if p['id'] == payload.get('id')), None)
        if payload.get('id') and not old: raise BlockError('请在决定的来源文档编辑此卡片', 422)
        if payload['kind'] == 'decision' and payload['scope'] == 'once': raise BlockError('业务决定需要文档或项目范围', 422)
        text, reason, source = payload['text'].strip(), payload['reason'].strip(), '用户确认'
        if payload.get('source_id') and not old:
            if payload.get('source_kind') == 'question':
                item = next((q for q in state.get('questions', []) if q['id'] == payload['source_id']), None)
                if not item or not item.get('answer', '').strip(): raise BlockError('请先保存已确认答案', 422)
                text, source = item['answer'], '问题 ' + item['id'] + '：' + item['text'][:200]
                if not reason: reason = '依据用户已保存的确认答案'
            else:
                item = state.get('issues', {}).get(payload['source_id'])
                if not item or not item.get('note', '').strip(): raise BlockError('请先保存问题处理依据', 422)
                text, source = item['note'], '处理依据 ' + payload['source_id']
                if not reason: reason = '依据用户已保存的问题处理记录'
        if not text or (payload['kind'] == 'decision' and not reason): raise BlockError('请填写内容；业务决定还需要确认理由', 422)
        known = {b.id for b in workbench.blocks.blocks(db, doc)}
        if set(payload.get('block_ids', [])) - known: raise BlockError('关联章节不存在', 422)
        value = {**payload, 'id': old['id'] if old else uid('policy'), 'text': text, 'reason': reason,
                 'source': old.get('source', source) if old else source, 'actor': actor, 'updated_at': now(), 'document_id': doc.id}
        value['history'] = ((old or {}).get('history', []) + [{k: value.get(k) for k in ('text', 'reason', 'status', 'scope', 'actor', 'updated_at')}])[-50:]
        if old: items[items.index(old)] = value
        elif len(items) < 300: items.append(value)
        else: raise BlockError('决定卡历史数量达到容量，请先整理', 422)
        row.state, row.revision = state, row.revision + 1
        # Changing a confirmed rule invalidates in-flight candidates and review evidence.
        targets = db.query(Document).filter_by(project_id=doc.project_id).all() if value['scope'] == 'project' or (old or {}).get('scope') == 'project' else [doc]
        for target in targets:
            target.context_revision += 1; target.revision += 1
    return workbench.inspect(document_id, actor)
