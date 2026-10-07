"""Conservative reuse: exact anchors and business keys, never fuzzy overwrite."""
from copy import deepcopy
from app.ai.provider import DocumentEditPlan, DocumentEditStep
from app.services.block_service import BlockError
from app.services.document_object_target import table_data


def candidate_groups(candidate, blocks):
    steps = candidate['steps']
    if candidate.get('coupled') or any(s['action'] == 'move_block' for s in steps): return [list(range(len(steps)))]
    if candidate.get('bulk_matches'): return [[i] for i in range(len(steps))]
    by_id = {b['id']: b for b in blocks}
    def independent(a, b):
        if a['action'] == b['action'] == 'replace_text':
            x, y = a.get('old_text', ''), b.get('old_text', '')
            return x and y and x not in y and y not in x and x not in b.get('new_text', '') and y not in a.get('new_text', '')
        if a['action'] == b['action'] == 'table_set' and a.get('row', 0) > 0 and b.get('row', 0) > 0 and not a.get('row_match') and not b.get('row_match'):
            return any(a.get(k) != b.get(k) for k in ('table_index', 'row', 'column'))
        return False
    groups = []
    for i, step in enumerate(steps):
        def related(j):
            other = steps[j]
            return ((step['block_id'] == other['block_id'] and not independent(step, other))
                or step['block_id'] in by_id.get(other['block_id'], {}).get('dependencies', [])
                or other['block_id'] in by_id.get(step['block_id'], {}).get('dependencies', []))
        joined = [g for g in groups if any(related(j) for j in g)]
        groups = [g for g in groups if g not in joined] + [[i, *(j for g in joined for j in g)]]
    return groups


def remap_step(step, baseline, current, object_target=None, take_candidate=False):
    from app.services.document_edit_service import _MERMAID_FENCE
    result = step.model_copy(deep=True)
    if step.action == 'move_block': raise BlockError('目录操作需要整体重新核对', 409)
    if baseline['content'] == current['content'] and baseline.get('fragment') == current.get('fragment'):
        return result
    if step.action in {'replace_text', 'move_text'}:
        if not step.old_text or current['content'].count(step.old_text) != 1:
            raise BlockError('目标原文已改动或不唯一；请保留当前内容或重新生成该项', 409)
        return result
    if step.action == 'table_set' and step.row > 0:
        old_tables, new_tables = table_data(baseline['content']), table_data(current['content'])
        if step.table_index >= len(old_tables): raise BlockError('基线表格不存在', 409)
        old = old_tables[step.table_index]
        matches = [(i, t) for i, t in enumerate(new_tables) if t[0] == old[0]]
        if len(matches) != 1: raise BlockError('表格身份不唯一或表头变化，请重新选择', 409)
        table_index, new = matches[0]
        if step.column not in old[0] or old[0].count(step.column) != 1: raise BlockError('目标列不唯一', 409)
        col = old[0].index(step.column)
        if step.row_match:
            rows = [r for r in old[1:] if any(step.row_match in c for c in r)]
            if len(rows) != 1: raise BlockError('基线行定位不唯一', 409)
            row = rows[0]
        else:
            if step.row >= len(old): raise BlockError('基线行不存在', 409)
            row = old[step.row]
        selected_key = (object_target or {}).get('key_column')
        keys = [selected_key] if selected_key else [h for h in old[0] if h.casefold() in {'id', '编号', '需求编号', '业务编号', '名称', '功能', '功能名称'}]
        key_cols = [old[0].index(k) for k in keys if k in old[0] and old[0].count(k) == 1 and k != step.column]
        mapped = []
        for k in key_cols:
            a, b = [r[k] for r in old[1:]], [r[k] for r in new[1:]]
            if all(a) and all(b) and len(set(a)) == len(a) and len(set(b)) == len(b) and row[k] in b:
                mapped.append(b.index(row[k]) + 1)
        if not mapped or len(set(mapped)) != 1: raise BlockError('缺少可靠业务定位列或定位列冲突，请重新选择行', 409)
        new_row = mapped[0]
        if new[new_row][col] != row[col] and not take_candidate: raise BlockError('同一单元格已被修改；可保留当前值或明确采用候选值', 409)
        result.table_index, result.row, result.row_match = table_index, new_row, ''
        return result
    if step.action == 'mermaid_replace':
        old = list(_MERMAID_FENCE.finditer(baseline['content']))
        new = list(_MERMAID_FENCE.finditer(current['content']))
        if step.diagram_index >= len(old): raise BlockError('原图不存在', 409)
        matches = [i for i, match in enumerate(new) if match.group(0) == old[step.diagram_index].group(0)]
        if len(matches) != 1: raise BlockError('图源已修改或不能唯一定位，请核对图源后重新生成', 409)
        result.diagram_index = matches[0]
        return result
    if step.action == 'er_upsert':
        old = [x for x in (baseline.get('fragment') or {}).get('er_diagrams', []) if x.get('diagram_id') == step.diagram_id]
        new = [x for x in (current.get('fragment') or {}).get('er_diagrams', []) if x.get('diagram_id') == step.diagram_id]
        if len(old) == len(new) == 1 and old == new: return result
    raise BlockError('此操作的相关内容已变化，请保留当前内容或重新生成该项', 409)


def reuse(edits, document_id, actor, job_id, indices, take_indices=()):
    from app.models import DocumentJob
    from app.services.block_service import uid
    with edits.blocks.write() as db:
        doc = edits.blocks.document(db, document_id, actor)
        job = db.get(DocumentJob, job_id)
        if not job or job.document_id != doc.id or job.status != 'ready': raise BlockError('请选择待应用的已保存候选', 409)
        candidate = deepcopy(job.result or {})
        if candidate.get('context_revision') != doc.context_revision:
            raise BlockError('业务决定或文档规则已变化，或此历史候选缺少规则基线；请依据当前规则重新生成', 409)
        original = candidate.get('source_blocks')
        if not original: raise BlockError('此历史候选未保存定位基线，请重新生成一次', 422)
        if not indices or len(set(indices)) != len(indices) or any(i < 0 or i >= len(candidate['steps']) for i in indices): raise BlockError('选择的修改项无效', 422)
        if any(i not in indices for i in take_indices): raise BlockError('冲突选择必须包含在所选修改中', 422)
        indices = sorted(indices)
        steps = [DocumentEditStep.model_validate(candidate['steps'][i]) for i in indices]
        source = [{key: getattr(b, key) for key in ('id', 'title', 'parent_id', 'order', 'version', 'content', 'instruction', 'dependencies', 'fragment')}
                  for b in edits.blocks.blocks(db, doc)]
        groups = candidate_groups(candidate, original)
        if any(set(g) & set(indices) and not set(g) <= set(indices) for g in groups):
            raise BlockError('有依赖的操作需要共同选择', 422)
        baseline = deepcopy(original)
        current = deepcopy(source)
        accepted, accepted_indices, conflicts = [], [], []
        coupled = candidate.get('coupled') or any(s.action == 'move_block' for s in steps)
        if coupled and len(indices) != len(candidate['steps']): raise BlockError('联动和目录操作需要一起核对，不能拆分', 422)
        for i, step in zip(indices, steps):
            old = next((b for b in baseline if b['id'] == step.block_id), None)
            now = next((b for b in current if b['id'] == step.block_id), None)
            try:
                if not old or not now: raise BlockError('原章节已删除', 409)
                if step.action == 'move_block' and doc.revision == candidate['base_revision']: mapped = step
                else: mapped = remap_step(step, old, now, candidate.get('object_target'), i in take_indices)
                changed = edits._simulate(current, DocumentEditPlan(summary='复用候选', steps=[mapped]))
                current = list(changed.values()); accepted.append(mapped); accepted_indices.append(i)
            except BlockError as error:
                conflicts.append({'step_index': i, 'block_id': step.block_id, 'reason': str(error),
                                  'can_take': step.action == 'table_set' and '同一单元格' in str(error)})
            # Advance the original baseline even if the current operation conflicts.
            baseline = list(edits._simulate(baseline, DocumentEditPlan(summary='原候选基线', steps=[step])).values())
        if conflicts and coupled:
            accepted, current = [], source
            conflicts.append({'step_index': -1, 'block_id': '', 'reason': '联动修改中存在冲突，已保留整组；请重新生成整组候选。', 'can_take': False})
        elif conflicts:
            failed = {c['step_index'] for c in conflicts}
            blocked = {i for g in groups if set(g) & failed for i in g}
            for i in accepted_indices:
                if i in blocked: conflicts.append({'step_index': i, 'block_id': candidate['steps'][i]['block_id'], 'reason': '依赖操作有冲突，已一起保留当前内容', 'can_take': False})
            accepted = [s for i, s in zip(accepted_indices, accepted) if i not in blocked]
            current = list(edits._simulate(source, DocumentEditPlan(summary='独立候选', steps=accepted)).values()) if accepted else source
        result = {'summary': '已在最新正文上复用可安全定位的修改', 'steps': [s.model_dump(mode='json') for s in accepted],
                  'base_revision': doc.revision, 'context_revision': doc.context_revision, 'preview': edits._preview(source, {b['id']: b for b in current}),
                  'coupled': bool(coupled), 'source_blocks': source, 'reuse_conflicts': conflicts,
                  'impact_notes': ['未调用模型重新生成。冲突项未写入，当前正文保持不变；请核对后应用。']}
        if candidate.get('linkage_receipt'): result['linkage_receipt'] = candidate['linkage_receipt']
        if candidate.get('answer_receipt') and not conflicts and len(accepted) == len(candidate['steps']):
            result['answer_receipt'] = candidate['answer_receipt']
        if accepted:
            new_job = DocumentJob(id=uid('prdjob'), document_id=doc.id, actor_id=actor, request_id=uid('reuse'),
                kind='chat', status='ready', base_revision=doc.revision,
                payload={'kind': 'chat', 'instruction': result['summary'], 'target_ids': list({s.block_id for s in accepted}), 'reused_from': job.id,
                         '_answers': job.payload.get('_answers'), '_group': job.payload.get('_group')}, events=[])
            result['job_id'] = new_job.id; new_job.result = result; db.add(new_job)
        return result
