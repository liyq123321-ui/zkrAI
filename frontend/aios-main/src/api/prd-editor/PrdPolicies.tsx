import { useEffect, useState } from 'react';
import type { Block } from './types';
import { RecoveryNotice, useDraftRecovery } from './draftRecovery';

export interface Policy { id?: string; kind: 'decision' | 'preference'; scope: 'once' | 'document' | 'project'; status: string;
  text: string; reason: string; block_ids: string[]; requirement_ids?: string[]; source?: string; source_id?: string; source_kind?: 'question' | 'issue';
  actor?: string; updated_at?: string; document_id?: string; history?: { text: string; reason: string; status: string; actor: string; updated_at: string }[] }
export const emptyPolicy = (): Policy => ({ kind: 'decision', scope: 'document', status: 'active', text: '', reason: '', block_ids: [], requirement_ids: [] });

export function PrdPolicies({ policies, documentId, blocks, seed, busy, onSave }: { policies: Policy[]; documentId: string; blocks: Block[]; seed?: Policy | null; busy: boolean; onSave: (p: Policy) => Promise<void> }) {
  const [draft, setDraft] = useState<Policy>(seed ?? emptyPolicy());
  const [query, setQuery] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState('');
  const recovery = useDraftRecovery(`policies:${documentId}`, draft.text || draft.reason ? draft : null);
  useEffect(() => { if (seed) setDraft(seed); }, [seed]);
  function validate(value: Policy) {
    if (value.block_ids.some(id => !blocks.some(b => b.id === id))) return '草稿关联的章节已删除，请下载恢复副本后重新选择章节。';
    if (value.id && policies.find(p => p.id === value.id)?.updated_at !== value.updated_at) return '这条决定已被更新，请下载恢复副本并重新打开当前决定核对。';
    return '';
  }
  async function save() {
    const reason = validate(draft); setError(reason); if (reason) return;
    await onSave({ ...draft, status: 'active' }); setDraft(emptyPolicy()); recovery.clear();
  }
  const visible = policies.filter(p => (showHistory || p.status === 'active') && `${p.text} ${p.reason} ${p.source ?? ''} ${(p.requirement_ids ?? []).join(' ')}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section aria-label="已确认决定与编辑偏好"><p>业务决定约束事实与范围，编辑偏好控制表达。保存、替换或撤销后，旧候选与审核需按新规则核对；不会自动发起审核。</p>
    <RecoveryNotice recovery={recovery} onRestore={value => { const reason = validate(value); setError(reason); if (reason) return false; setDraft(value); }} />
    {error && <p role="alert">{error}</p>}
    <div className="prd-workbench-compose"><label>类型<select value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value as Policy['kind'], scope: 'document' })}><option value="decision">已确认业务决定</option><option value="preference">编辑偏好</option></select></label>
      <label>范围<select value={draft.scope} onChange={e => setDraft({ ...draft, scope: e.target.value as Policy['scope'] })}>{draft.kind === 'preference' && <option value="once">下一次操作</option>}<option value="document">当前文档</option><option value="project">当前项目</option></select></label>
      <textarea aria-label="决定或偏好内容" maxLength={6000} readOnly={!!draft.source_id && !draft.id} value={draft.text} placeholder="例如：本期游客只读；对外介绍少用缩写" onChange={e => setDraft({ ...draft, text: e.target.value })} />
      <textarea aria-label="决定理由" maxLength={2000} value={draft.reason} placeholder="确认理由 / 依据" onChange={e => setDraft({ ...draft, reason: e.target.value })} />
      <label>适用章节<select value={draft.block_ids[0] ?? ''} onChange={e => setDraft({ ...draft, block_ids: e.target.value ? [e.target.value] : [] })}><option value="">全部章节（按所选文档或项目范围）</option>{blocks.map(b => <option key={b.id} value={b.id}>{b.title}</option>)}</select></label>
      <label>适用需求编号<input placeholder="留空表示不按编号限制，多个编号用逗号分开" value={(draft.requirement_ids ?? []).join(', ')} onChange={e => setDraft({ ...draft, requirement_ids: e.target.value.split(/[,，\s]+/).filter(Boolean) })} /></label>
      {draft.source_id && <p>将依据已保存的{draft.source_kind === 'question' ? '确认答案' : '处理记录'}创建决定，来源内容由服务端核对。</p>}
      <button disabled={busy || !draft.text.trim() || (draft.kind === 'decision' && !draft.reason.trim())} onClick={() => void save().catch(() => undefined)}>{draft.id ? '替换并保留历史' : '确认保存'}</button><button disabled={busy} onClick={() => { setDraft(emptyPolicy()); setError(''); recovery.clear(); }}>清空表单</button>
    </div>
    <input aria-label="搜索决定与偏好" placeholder="搜索内容、理由、来源或需求编号…" value={query} onChange={e => setQuery(e.target.value)} />
    <label><input type="checkbox" checked={showHistory} onChange={e => setShowHistory(e.target.checked)} />包括已撤销和已使用记录</label>
    <p>显示 {visible.length} / {policies.length} 条</p>
    {!visible.length && <p>当前没有匹配的决定或偏好。</p>}
    {visible.map(p => <article className="prd-workbench-card" key={p.id}><h3>{p.kind === 'decision' ? '决定' : '偏好'} · {p.text}</h3><small>{({ active: '有效', revoked: '已撤销', used: '已用于一次操作' } as Record<string, string>)[p.status]} · {p.scope === 'project' ? '项目' : p.scope === 'once' ? '一次' : '文档'} · {p.actor} · {p.updated_at && new Date(p.updated_at).toLocaleString('zh-CN', { hour12: false })}</small>
      <p>{p.reason}</p><p>来源：{p.source}</p><small>{p.block_ids.length ? p.block_ids.map(id => blocks.find(b => b.id === id)?.title ?? id).join('、') : '全部适用章节'} · {(p.requirement_ids ?? []).join('、')}</small>
      {p.document_id === documentId ? <><button disabled={busy} onClick={() => setDraft({ ...p, source_id: undefined, source_kind: undefined })}>编辑 / 替换</button><button disabled={busy || p.status === 'revoked'} onClick={() => void onSave({ ...p, status: 'revoked' }).catch(() => undefined)}>撤销</button></> : <p>请到来源文档编辑这条项目决定。</p>}
      <details><summary>确认与替换历史</summary>{p.history?.map((h, i) => <p key={i}>{h.updated_at} · {h.actor} · {h.status} · {h.text} · {h.reason}</p>)}</details>
    </article>)}
  </section>;
}
