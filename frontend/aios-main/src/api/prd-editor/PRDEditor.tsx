import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, FileText, Network, Download, Settings2, Maximize2, X } from 'lucide-react';
import { apiClient } from '../client';
import { appConfig } from '../config';
import { documentPath, getDocument, mutate, openDocument } from './api';
import { fromBlock, mergeDrafts, statusNames } from './types';
import type { AgentRun, Comment, Draft, Snapshot } from './types';
import { BlockTree, BlockPlanMap } from './BlockTree';
import { BlockEditor } from './BlockEditor';
import { BlockToolbar } from './BlockToolbar';
import { FullDocumentView, fullCommentTargets, fullSnapshotChanged } from './FullDocumentView';
import { BlockCommentPanel } from './BlockCommentPanel';
import { GenerationStatus } from './GenerationStatus';
import { BlockHistory, BlockDiff } from './BlockHistory';
import type { Version } from './BlockHistory';
import './editor.css';

const message = (error: unknown) => error instanceof Error ? error.message : String(error);
export function PRDEditor({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const dataRef = useRef<Snapshot | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const draftRef = useRef<Record<string, Draft>>({});
  const [selected, setSelected] = useState('');
  const [checked, setChecked] = useState(new Set<string>());
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [feedback, setFeedback] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [saving, setSaving] = useState(new Set<string>());
  const saves = useRef(new Set<string>());
  const autoApplied = useRef(new Set<string>());
  const [diffRun, setDiffRun] = useState<AgentRun | null>(null);
  const [history, setHistory] = useState<{ blockId: string; versions: Version[] } | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [outline, setOutline] = useState('');
  const [template, setTemplate] = useState('alibaba');
  const [settings, setSettings] = useState<{ background: string; global_rules: string } | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fullSnapshot, setFullSnapshot] = useState<Snapshot | null>(null);

  const updateDrafts = useCallback((next: Record<string, Draft>) => { draftRef.current = next; setDrafts(next); }, []);
  const ingest = useCallback((next: Snapshot) => {
    if (dataRef.current && next.document.revision < dataRef.current.document.revision) return;
    dataRef.current = next;
    setData(next);
    updateDrafts(mergeDrafts(draftRef.current, next.blocks));
    setSelected(id => next.blocks.some(b => b.id === id) ? id : next.blocks[0]?.id ?? '');
  }, [updateDrafts]);
  const refresh = useCallback(async () => { const id = dataRef.current?.document.id; if (id) ingest(await getDocument(id)); }, [ingest]);
  const attempt = async (fn: () => Promise<unknown>) => { setError(''); try { await fn(); } catch (e) { setError(message(e)); } };

  useEffect(() => {
    let live = true;
    openDocument(sessionId).then(value => { if (live) ingest(value); }).catch(e => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, [sessionId, ingest]);
  useEffect(() => {
    const id = data?.document.id;
    if (!id) return;
    let source: EventSource | null = null;
    if (typeof EventSource !== 'undefined') {
      source = new EventSource(appConfig.apiBaseUrl + documentPath(id) + '/events');
      source.addEventListener('snapshot', event => { try { ingest(JSON.parse((event as MessageEvent).data)); setConnected(true); } catch { setConnected(false); } });
      source.onerror = () => setConnected(false);
    }
    const timer = setInterval(() => { void refresh().catch(() => setConnected(false)); }, 5000);
    return () => { source?.close(); clearInterval(timer); };
  }, [data?.document.id, ingest, refresh]);
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => {
      if (Object.values(draftRef.current).some(d => d.dirty)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', before);
    return () => window.removeEventListener('beforeunload', before);
  }, []);
  useEffect(() => {
    if (!showPlan) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setShowPlan(false); };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [showPlan]);

  const save = useCallback(async (id: string, rebase = false) => {
    const snapshot = dataRef.current, captured = draftRef.current[id];
    if (!snapshot || !captured?.dirty) return;
    if (saves.current.has(id)) throw new Error('该 Block 正在保存，请稍后重试');
    saves.current.add(id); setSaving(new Set(saves.current));
    try {
      const { dirty, error: _, version, ...values } = captured;
      const next = await mutate<Snapshot>(snapshot.document.id, `/blocks/${id}`, {
        ...values, expected_version: rebase ? snapshot.blocks.find(b => b.id === id)!.version : version,
      }, 'PUT');
      const saved = next.blocks.find(b => b.id === id)!;
      const latest = draftRef.current[id];
      // Keep keystrokes typed while the save was in flight, but advance their base.
      updateDrafts({ ...draftRef.current, [id]: latest === captured ? fromBlock(saved) : { ...latest, version: saved.version, error: undefined } });
      ingest(next);
    } catch (e) {
      updateDrafts({ ...draftRef.current, [id]: { ...draftRef.current[id], error: message(e) } });
      throw e;
    } finally {
      saves.current.delete(id); setSaving(new Set(saves.current));
    }
  }, [ingest, updateDrafts]);
  useEffect(() => {
    const timer = setTimeout(() => {
      for (const [id, draft] of Object.entries(draftRef.current)) if (draft.dirty && !draft.error && !saves.current.has(id)) {
        void save(id).catch(e => setError(message(e)));
      }
    }, 900);
    return () => clearTimeout(timer);
  }, [drafts, save]);

  const apply = useCallback(async (run: AgentRun, force = false) => {
    const snapshot = dataRef.current;
    if (!snapshot || !run.block_id) return;
    const local = draftRef.current[run.block_id];
    if (local?.dirty) throw new Error('此块还有未保存输入，请先保存草稿，再比较 AI 结果。');
    const block = snapshot.blocks.find(b => b.id === run.block_id)!;
    await mutate(snapshot.document.id, `/runs/${run.id}/apply`, { expected_version: block.version, force });
    await refresh();
  }, [refresh]);
  useEffect(() => {
    if (!data) return;
    for (const run of data.runs) {
      if (!run.block_id || run.status !== 'completed' || run.apply_status !== 'pending' || autoApplied.current.has(run.id)) continue;
      // The server owns first-draft results, including its stop/conflict decisions.
      if (data.runs.some(parent => parent.type === 'initial' && run.request_id?.startsWith(parent.id + ':'))) continue;
      const draft = draftRef.current[run.block_id], block = data.blocks.find(b => b.id === run.block_id);
      if (!block || draft?.dirty || saves.current.has(run.block_id) || block.version !== run.base_version) continue;
      autoApplied.current.add(run.id);
      void apply(run).catch(e => setError(message(e)));
    }
  }, [data, drafts, apply]);

  function edit(patch: Partial<Draft>) {
    updateDrafts({ ...draftRef.current, [selected]: { ...draftRef.current[selected], ...patch, dirty: true, error: undefined } });
  }
  function select(id: string) { setSelected(id); setStart(''); setEnd(''); setFeedback(''); setHistory(null); setDiffRun(null); }
  async function browseFull() {
    setBusy(true);
    try {
      for (const [id, draft] of Object.entries(draftRef.current)) if (draft.dirty) await save(id);
      if (Object.values(draftRef.current).some(draft => draft.dirty)) throw new Error('请等待输入保存完成后再浏览全文。');
      const current = await getDocument(dataRef.current!.document.id);
      ingest(current); setFullSnapshot(current); setChecked(new Set()); setStart(''); setEnd(''); setHistory(null); setDiffRun(null);
    } finally { setBusy(false); }
  }
  async function reviewFull() {
    if (!fullSnapshot) return;
    setBusy(true);
    try {
      const current = await getDocument(fullSnapshot.document.id);
      if (fullSnapshotChanged(fullSnapshot, current)) { ingest(current); throw new Error('全文已变化，请先更新全文再审核。'); }
      await mutate(current.document.id, '/review', { request_id: crypto.randomUUID(), expected_revision: current.document.revision });
      await refresh();
    } finally { setBusy(false); }
  }
  async function run(kind: 'generate' | 'revise' | 'review', blockId = selected, commentId?: string) {
    await save(blockId);
    if (draftRef.current[blockId]?.dirty) throw new Error('保存期间又有输入，请保存完成后再运行 AI。');
    if (kind === 'revise' && !commentId && !feedback.trim()) throw new Error('请填写右侧修改意见。');
    if ((start && !end) || (!start && end)) throw new Error('请同时填写起始行和结束行。');
    const docId = dataRef.current!.document.id;
    await mutate(docId, `/blocks/${blockId}/runs`, { type: kind, request_id: crypto.randomUUID(), feedback,
      ...(kind === 'revise' && !commentId && start ? { start_line: Number(start), end_line: Number(end) } : {}), comment_id: commentId });
    await refresh();
  }
  async function addComment() {
    if (fullSnapshot) {
      if ((start && !end) || (!start && end)) throw new Error('请同时填写起始行和结束行。');
      const current = await getDocument(fullSnapshot.document.id);
      if (fullSnapshotChanged(fullSnapshot, current)) { ingest(current); throw new Error('全文已变化，请更新全文并重新选择批注范围。'); }
      const targets = start && end ? fullCommentTargets(fullSnapshot.blocks, Number(start), Number(end))
        : fullCommentTargets(fullSnapshot.blocks).filter(target => !checked.size || checked.has(target.block_id));
      ingest(await mutate(current.document.id, '/comments', { text: feedback, targets }));
      setFeedback(''); setStart(''); setEnd('');
      return;
    }
    const ids = checked.size ? [...checked] : [selected];
    for (const id of ids) await save(id);
    if (ids.some(id => draftRef.current[id]?.dirty)) throw new Error('保存期间又有输入，请保存后再添加批注。');
    if (ids.length > 1 && (start || end)) throw new Error('多块批注请先点击「整块」；行范围批注用于当前单块。');
    const current = await getDocument(dataRef.current!.document.id);
    const targets = ids.map(id => ({ block_id: id, base_version: current.blocks.find(b => b.id === id)!.version,
      ...(ids.length === 1 && start && end ? { start_line: Number(start), end_line: Number(end) } : {}) }));
    if ((start && !end) || (!start && end)) throw new Error('请同时填写起始行和结束行。');
    ingest(await mutate(current.document.id, '/comments', { text: feedback, targets }));
    setFeedback(''); setStart(''); setEnd('');
  }
  async function reviseComment(comment: Comment) {
    const failures: string[] = [];
    for (const target of comment.targets) {
      try { await run('revise', target.block_id, comment.id); } catch (e) { failures.push(message(e)); }
    }
    if (failures.length) throw new Error(failures.join('；'));
  }
  async function importOutline() {
    setBusy(true);
    try {
      const current = dataRef.current!;
      const next = await mutate<Snapshot>(current.document.id, '/outline', { template, outline, expected_revision: current.document.revision }, 'PUT');
      ingest(next); setShowImport(false);
      await mutate(next.document.id, '/plan', { request_id: crypto.randomUUID() });
      await refresh();
    } finally { setBusy(false); }
  }
  async function readFile(file: File) {
    if (file.size > 8 * 1024 * 1024) throw new Error('文件不能超过 8 MB');
    const content = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); });
    const result = await mutate<{ outline: string }>(dataRef.current!.document.id, '/import', { filename: file.name, content_base64: content, expected_revision: dataRef.current!.document.revision });
    setTemplate('custom'); setOutline(result.outline); setNotice('已提取文件文字，请在导入前仅保留所需章节标题。');
  }
  async function exportMarkdown() {
    for (const [id, d] of Object.entries(draftRef.current)) if (d.dirty) await save(id);
    if (Object.values(draftRef.current).some(d => d.dirty)) throw new Error('请等待输入保存完成后再导出。');
    const response = await fetch(appConfig.apiBaseUrl + documentPath(dataRef.current!.document.id) + '/markdown');
    if (!response.ok) throw new Error('导出失败，请重试');
    const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
    link.href = url; link.download = 'PRD-Blocks.md'; link.click(); URL.revokeObjectURL(url);
  }
  async function submit() {
    for (const [id, draft] of Object.entries(draftRef.current)) if (draft.dirty) await save(id);
    if (Object.values(draftRef.current).some(d => d.dirty)) throw new Error('请等待输入保存完成后再提交。');
    const current = await getDocument(dataRef.current!.document.id);
    await mutate(current.document.id, '/submit', { expected_revision: current.document.revision });
    setNotice('已创建评审快照。返回项目后可查看并确认最新 PRD。');
    await refresh();
  }
  const block = data?.blocks.find(b => b.id === selected), draft = drafts[selected];
  const initialRun = data?.runs.find(r => r.type === 'initial');
  const initialActive = !!initialRun && ['queued', 'running'].includes(initialRun.status);
  const wholeReviewActive = data?.runs.some(r => r.type === 'document_review' && ['queued', 'running'].includes(r.status)) ?? false;
  const active = initialActive || wholeReviewActive || (data?.runs.some(r => r.block_id === selected && ['queued', 'running'].includes(r.status)) ?? false);
  const anyActive = data?.runs.some(r => ['queued', 'running'].includes(r.status)) ?? false;
  const wholeReport = data?.runs.filter(r => r.type === 'document_review' && r.status === 'completed').at(-1);
  const candidates = data?.runs.filter(r => r.block_id === selected && r.status === 'completed' && r.apply_status !== 'applied') ?? [];
  const viewBlocks = data?.blocks.map(b => ({ ...b, status: drafts[b.id]?.dirty ? 'editing' as const : b.status })) ?? [];
  return <main className="prd-page">
    <header className="prd-header"><a href="/" aria-label="返回项目"><ArrowLeft size={18} />返回项目</a><span className="prd-header-divider" /><FileText size={22} /><div><h1>{data?.document.title ?? 'Block PRD 编辑器'}</h1><small>每次专注一个章节 · 自动保存 · 独立版本</small></div>
      <div className="prd-header-actions"><span className="prd-live">{connected ? '● 实时同步' : '定时同步'}</span><button onClick={() => setShowImport(true)} disabled={!data || initialActive}>导入大纲</button><button aria-label="文档设置" disabled={!data} onClick={() => setSettings({ background: data!.document.background, global_rules: data!.document.global_rules })}><Settings2 size={16} /></button><button disabled={!data} onClick={() => void attempt(exportMarkdown)}><Download size={15} />导出</button><button className="prd-primary" disabled={!data || busy || initialActive} onClick={() => void attempt(submit)}>提交评审</button></div>
    </header>
    {error && <div className="prd-alert" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}
    {notice && <div className="prd-notice" role="status">{notice}<button onClick={() => setNotice('')}>关闭</button></div>}
    {initialRun && <div className="prd-notice" role="status">
      {initialActive ? `首版正在按模块连续生成（${initialRun.result?.completed ?? 0}/${initialRun.result?.total ?? 0}），离开页面后仍会继续。`
        : initialRun.status === 'completed' ? '首版已全部生成，后续修改请按模块操作。'
        : initialRun.status === 'failed' ? initialRun.error : '首版自动生成已停止，已有内容已保留，可按模块手动继续。'}
      {initialActive && <button onClick={() => void attempt(async () => { await mutate(data!.document.id, `/runs/${initialRun.id}/cancel`, {}); await refresh(); })}>停止自动生成</button>}
    </div>}
    {!data ? <div className="prd-loading">{error ? <button onClick={() => void attempt(async () => ingest(await openDocument(sessionId)))}>重新加载</button> : '正在恢复文档…'}</div> : <>
      <section className="prd-map-panel"><div className="prd-section-heading"><span><Network size={16} />文档规划 <small>{data.blocks.length} 个 Block · {data.blocks.filter(b => b.content).length} 个已有内容</small></span><button disabled={initialActive || data.document.plan_status === 'generating' || data.blocks.some(b => b.content || drafts[b.id]?.dirty)} onClick={() => void attempt(async () => { await mutate(data.document.id, '/plan', { request_id: crypto.randomUUID() }); await refresh(); })}>{data.document.plan_status === 'generating' ? '正在规划…' : 'AI 规划大纲'}</button></div>
        <button className="prd-map-thumbnail" aria-label="展开文档规划图" onClick={() => setShowPlan(true)}>
          <BlockPlanMap blocks={viewBlocks} selected={selected} onSelect={() => {}} interactive={false} maxDepth={1} />
          <span className="prd-map-thumbnail-hint"><Maximize2 size={14} />点击展开</span>
        </button>
      </section>
      <div className="prd-columns"><aside className="prd-directory"><div className="prd-section-heading">文档目录<small>{checked.size ? `${checked.size} 已选` : '可多选批注'}</small></div><BlockTree blocks={viewBlocks} selected={selected} checked={checked} onSelect={select} onCheck={id => setChecked(old => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next; })} /></aside>
        <section className="prd-main-editor">{fullSnapshot ? <FullDocumentView snapshot={fullSnapshot} selected={selected}
          changed={fullSnapshotChanged(fullSnapshot, data)} active={wholeReviewActive} loading={busy || anyActive} report={wholeReport}
          onRange={(s, e) => { setStart(String(s)); setEnd(String(e)); setChecked(new Set()); }} onSelect={select}
          onRefresh={() => void attempt(browseFull)} onBack={() => { setFullSnapshot(null); setChecked(new Set()); setStart(''); setEnd(''); }} onReview={() => void attempt(reviewFull)} /> : block && draft && <>
          <div className="prd-save-state"><span className={`prd-dot is-${draft.dirty ? 'editing' : block.status}`} />{saving.has(selected) ? '保存中…' : draft.error ? '保存失败，输入已保留' : draft.dirty ? '未保存' : '已保存'}<span>{statusNames[block.status]} · {block.fragment_version === block.version ? '结构已同步' : '提交前需审核本块'}</span></div>
          <BlockEditor key={selected} block={block} draft={draft} blocks={data.blocks} onEdit={edit} onRange={(s, e) => { setStart(String(s)); setEnd(String(e)); }} />
          <BlockToolbar onBrowse={() => void attempt(browseFull)} dirty={draft.dirty} saving={saving.has(selected)} active={active} hasContent={Boolean(draft.content)} onSave={() => void attempt(() => save(selected))} onRun={kind => void attempt(() => run(kind))} onHistory={() => void attempt(async () => setHistory({ blockId: selected, versions: await apiClient.request<Version[]>(documentPath(data.document.id) + `/blocks/${selected}/history`) }))} />
          {draft.dirty && draft.version !== block.version && <div className="prd-conflict"><strong>保存版本已变化，人工输入已保留</strong><BlockDiff before={block.content} after={draft.content} /><button onClick={() => void attempt(() => save(selected, true))}>基于当前 v{block.version} 保存我的草稿</button></div>}
          {candidates.map(r => <div className="prd-conflict" key={r.id}><strong>{r.apply_status === 'conflict' || r.base_version !== block.version ? 'AI 结果基于旧版本生成' : draft.dirty ? 'AI 已完成，等待你保存当前输入' : 'AI 结果待应用'}</strong><p>基线 v{r.base_version} → 当前 v{block.version}</p><div><button onClick={() => setDiffRun(r)}>查看 Diff</button><button onClick={() => void attempt(async () => { await save(r.block_id!); if (draftRef.current[r.block_id!]?.dirty) throw new Error('请先保存当前输入'); await mutate(data.document.id, `/runs/${r.id}/retry`, { request_id: crypto.randomUUID() }); await refresh(); })}>基于当前版本重新生成</button><button disabled={draft.dirty} onClick={() => void attempt(async () => { await apply(r, true); setDiffRun(null); })}>仍然应用</button></div></div>)}
          {data.runs.filter(r => r.block_id === selected && r.result?.review_notes?.length).slice(-1).map(r => <div className="prd-review-notes" key={r.id}><strong>本块审核意见</strong><ul>{r.result!.review_notes!.map((note, i) => <li key={i}>{note}</li>)}</ul></div>)}
        </>}</section>
        <aside className="prd-right"><GenerationStatus key={data.document.id} documentId={data.document.id} runs={data.runs} blocks={data.blocks} onCancel={r => void attempt(async () => { await mutate(data.document.id, `/runs/${r.id}/cancel`, {}); await refresh(); })} /><BlockCommentPanel fullMode={!!fullSnapshot} comments={data.comments} blocks={data.blocks} current={selected} feedback={feedback} onFeedback={setFeedback} start={start} end={end} onRange={(s, e) => { setStart(s); setEnd(e); if (fullSnapshot) setChecked(new Set()); }} count={checked.size || (fullSnapshot ? fullSnapshot.blocks.length : 0)} onAdd={() => void attempt(addComment)} onRevise={c => void attempt(() => reviseComment(c))} onResolve={c => void attempt(async () => ingest(await mutate(data.document.id, `/comments/${c.id}`, { resolved: !c.resolved }, 'PATCH')))} /></aside></div>
    </>}
    {showPlan && data && <div className="prd-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setShowPlan(false); }}><section className="prd-modal prd-plan-modal" role="dialog" aria-modal="true" aria-label="文档规划图"><header><div><h2>文档规划</h2><small>点击节点可跳转到对应 Block</small></div><button aria-label="关闭文档规划图" onClick={() => setShowPlan(false)}><X size={17} /></button></header><BlockPlanMap blocks={viewBlocks} selected={selected} onSelect={id => { select(id); setShowPlan(false); }} /></section></div>}
    {showImport && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="导入 PRD 大纲"><h2>模板与大纲</h2><p>内置模板来自提供的阿里、鹅厂附件；教程和示例业务不作为项目需求。有正文或批注时不会覆盖现有大纲。</p><select aria-label="选择模板" value={template} onChange={e => setTemplate(e.target.value)}><option value="alibaba">阿里 PRD 模板</option><option value="tencent">鹅厂 PRD 模板</option><option value="custom">自定义大纲</option></select><input aria-label="上传大纲文件" type="file" accept=".md,.txt,.pdf" onChange={e => { if (e.target.files?.[0]) void attempt(() => readFile(e.target.files![0])); }} />{template === 'custom' && <textarea aria-label="大纲内容" rows={14} value={outline} onChange={e => setOutline(e.target.value)} placeholder={'# 产品背景\n# 功能设计\n## 具体功能'} />}<footer><button onClick={() => setShowImport(false)}>关闭</button><button className="prd-primary" disabled={busy} onClick={() => void attempt(importOutline)}>导入并规划</button></footer></section></div>}
    {settings && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="文档背景与规则"><h2>文档背景与全局规则</h2><label className="prd-field">项目背景<textarea rows={10} value={settings.background} onChange={e => setSettings({ ...settings, background: e.target.value })} /></label><label className="prd-field">全局规则<textarea rows={5} value={settings.global_rules} onChange={e => setSettings({ ...settings, global_rules: e.target.value })} /></label><p>更新后已有正文标记为待更新。</p><footer><button onClick={() => setSettings(null)}>关闭</button><button onClick={() => void attempt(async () => { ingest(await mutate(data!.document.id, '/context', { ...settings, expected_revision: dataRef.current!.document.revision }, 'PUT')); setSettings(null); })}>保存设置</button></footer></section></div>}
    {(diffRun || history) && <div className="prd-modal-backdrop"><section className="prd-modal prd-modal-wide" role="dialog" aria-modal="true" aria-label={history ? 'Block 历史版本' : 'AI 结果 Diff'}><header><h2>{history ? '历史版本' : '当前内容 → AI 候选结果'}</h2><button onClick={() => { setDiffRun(null); setHistory(null); }}>关闭</button></header>{diffRun && <BlockDiff before={drafts[diffRun.block_id!]?.content ?? ''} after={diffRun.result?.content ?? ''} />}{history && <BlockHistory versions={history.versions} content={drafts[history.blockId]?.content ?? ''} onRestore={v => void attempt(async () => { await save(history.blockId); if (draftRef.current[history.blockId].dirty) throw new Error('请先保存当前输入'); ingest(await mutate(data!.document.id, `/blocks/${history.blockId}/restore`, { version: v, expected_version: draftRef.current[history.blockId].version })); setHistory(null); })} />}</section></div>}
  </main>;
}
