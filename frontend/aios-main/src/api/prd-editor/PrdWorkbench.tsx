import { useEffect, useState } from 'react';
import { X, ArrowRight, RefreshCw } from 'lucide-react';
import { apiClient } from '../client';
import { documentPath, getDocument, getDocumentVersions, mutate } from './api';
import type { DocumentVersion } from './api';
import type { Snapshot } from './types';
import type { PendingChatEdit } from './PRDEditor';
import { DocumentJobs, enqueueDocumentJob } from './DocumentJobs';
import { RecoveryNotice, useDraftRecovery } from './draftRecovery';
import { BlockDiff } from './BlockHistory';
import { PrdPolicies, emptyPolicy } from './PrdPolicies';
import type { Policy } from './PrdPolicies';
import { PrdPerformance } from './PrdPerformance';
import { submitPrdOperation } from './DocumentJobs';
import './workbench.css';

export type WorkbenchTab = 'impact' | 'health' | 'issues' | 'alternatives' | 'meeting' | 'questions' | 'anchors' | 'tasks' | 'policies' | 'performance';
type Tab = WorkbenchTab;
interface Question { id: string; text: string; answer: string; category: string; block_ids: string[]; source: string; status?: string; applied_receipt?: { version: number; job_id: string; answer: string; at: string }; history?: { status: string; at: string }[] }
interface Alternative { id: string; instruction: string; created_at: string; note: string; status: string; target_ids: string[]; plan: PendingChatEdit }
interface Anchor { id: string; block_id: string; title: string; quote: string; kind: string; missing?: boolean }
interface Issue { id: string; description: string; suggestion: string; block_ids: string[]; run_id: string; stale: boolean; status: string; note: string; source_revision: number; changed_blocks: string[]; evidence?: { block_id: string; quote: string }[]; recheck?: { summary: string; run_id: string } | null; history: { status: string; note: string; at: string }[] }
interface Workbench {
  revision: number; document_revision: number; baseline_run: string | null;
  latest_review?: { summary: string; scope: string; stale: boolean } | null;
  changed_ids: string[]; affected_ids: string[];
  nodes: { id: string; title: string; kinds: string[]; refs: string[]; changed: boolean; affected: boolean }[];
  edges: { from: string; to: string; evidence: string }[];
  health: { block_id: string; title: string; reasons: string[] }[];
  issues: Issue[];
  artifacts: { id: string; title: string; kind: string; stale: boolean; source_spec: string }[];
  tasks: { id: string; title: string; block_ids: string[]; refs: string[] }[];
  policies: Policy[];
  merge_suggestions: { left: string; right: string }[];
  inspection_timing?: { seconds: number; index_cache_hit: boolean };
  state: { questions?: Question[]; alternatives?: Alternative[]; anchors?: Anchor[]; linked_groups?: { id: string; created_at: string; selected_ids: string[]; omitted_ids: string[]; job_ids: string[]; artifact_note: string }[] };
}
const labels: Record<Tab, string> = { impact: '需求影响地图', health: '文档健康地图', issues: '问题闭环', alternatives: '方案对照草稿', meeting: '评审会议包', questions: '待确认信息篮', anchors: '稳定位置', tasks: '候选任务', policies: '决定与偏好', performance: '执行耗时' };
const statuses: Record<string, string> = { unresolved: '未解决', decision: '需决策 / 保留', recheck: '已改动待复查', confirmed: '人工确认已解决', draft: '未采纳', applied: '已应用', partially_applied: '部分应用' };
const time = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
const questionStatuses: Record<string, string> = { open: '待答', answered: '已答待应用', applied: '已写入', ignored: '忽略', archived: '归档' };

export function PrdWorkbench({ snapshot, selectedId, onClose, onLocate, onPlan, beforePlan, onReview, initialTab = 'impact', onArtifact, onDocumentChange, onVersion }: {
  snapshot: Snapshot; selectedId: string; onClose: () => void;
  onLocate: (id: string, quote?: string) => void; onPlan: (plan: PendingChatEdit) => void;
  beforePlan: () => void; onReview: () => Promise<void>;
  initialTab?: Tab; onArtifact: (kind: string) => void;
  onDocumentChange?: (snapshot: Snapshot) => void; onVersion?: (version: number) => void;
}) {
  const id = snapshot.document.id;
  const [tab, setTab] = useState<Tab>(initialTab);
  const [data, setData] = useState<Workbench | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [focus, setFocus] = useState(selectedId || snapshot.blocks[0]?.id || '');
  const [instruction, setInstruction] = useState('');
  const [question, setQuestion] = useState('');
  const [answers, setAnswers] = useState<Record<string, Question>>({});
  const [pickedQuestions, setPickedQuestions] = useState<string[]>([]);
  const [issueNotes, setIssueNotes] = useState<Record<string, string>>({});
  const [aText, setAText] = useState('');
  const [bText, setBText] = useState('');
  const [altNotes, setAltNotes] = useState<Record<string, string>>({});
  const [pair, setPair] = useState<string[]>([]);
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [versionPair, setVersionPair] = useState<number[]>([]);
  const [meeting, setMeeting] = useState<{ markdown: string; filename: string } | null>(null);
  const [anchor, setAnchor] = useState('');
  const [linked, setLinked] = useState<string[]>([]);
  const [questionFilter, setQuestionFilter] = useState('pending');
  const [questionQuery, setQuestionQuery] = useState('');
  const [onlyCurrentHealth, setOnlyCurrentHealth] = useState(true);
  const [policySeed, setPolicySeed] = useState<Policy | null>(null);
  const formRecovery = useDraftRecovery(`workbench:${id}`, instruction || question || aText || bText || Object.keys(answers).length || Object.keys(issueNotes).length || Object.keys(altNotes).length
    ? { revision: snapshot.document.revision, focus, instruction, question, aText, bText, answers, issueNotes, altNotes } : null);

  async function load() {
    const [value, list] = await Promise.all([apiClient.request<Workbench>(documentPath(id) + '/workbench'), getDocumentVersions(id)]);
    setData(value); setVersions(list);
    setVersionPair(old => old.length ? old : list.slice(0, 2).map(v => v.version).sort((a, b) => a - b));
    setPair(old => old.length ? old : (value.state.alternatives ?? []).slice(-2).map(a => a.id));
    return value;
  }
  async function run(label: string, task: () => Promise<unknown>) {
    if (busy) return;
    setBusy(label); setError('');
    try { await task(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(''); }
  }
  useEffect(() => { void run('正在读取工作台…', load); }, [id]); // modal is scoped to one document
  async function update(action: string, payload: object, current = data!) {
    const next = await mutate<Workbench>(id, '/workbench', { expected_revision: current.revision, action, payload });
    setData(next); return next;
  }
  async function plan(kind: string, text = instruction, extra: object = {}) {
    beforePlan();
    const job = await enqueueDocumentJob(id, data?.document_revision ?? snapshot.document.revision, { kind, instruction: text, target_ids: focus ? [focus] : [], ...extra });
    setTab('tasks');
    return job;
  }

  function preview(value: PendingChatEdit) { onPlan(value); onClose(); }
  async function savePolicy(payload: Policy) {
    setBusy('保存决定与偏好…'); setError('');
    try { const next = await mutate<Workbench>(id, '/workbench/policies', { expected_revision: data!.revision, payload }); setData(next); onDocumentChange?.(await getDocument(id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); throw reason; }
    finally { setBusy(''); }
  }
  function decisionFrom(kind: 'question' | 'issue', sourceId: string, text: string, blockIds: string[]) {
    setPolicySeed({ ...emptyPolicy(), text, reason: '依据用户已保存的确认记录', source_kind: kind, source_id: sourceId, block_ids: blockIds }); setTab('policies');
  }
  function locate(blockId: string, quote?: string) { onLocate(blockId, quote); onClose(); }
  const title = (blockId: string) => snapshot.blocks.find(b => b.id === blockId)?.title || blockId;
  const connections = data?.edges.filter(e => e.from === focus || e.to === focus) ?? [];
  const selectedAlternatives = data?.state.alternatives?.filter(a => pair.includes(a.id)) ?? [];
  const visibleQuestions = (data?.state.questions ?? []).filter(q => (questionFilter === 'all' || (questionFilter === 'pending' ? ['open', 'answered'].includes(q.status ?? 'open') : q.status === questionFilter))
    && [q.text, (answers[q.id] ?? q).answer, q.source, ...q.block_ids.map(title)].join(' ').toLocaleLowerCase().includes(questionQuery.trim().toLocaleLowerCase()));
  const targetPicker = <label className="prd-workbench-target">当前目标 <select value={focus} onChange={e => { setFocus(e.target.value); setAnchor(''); }}>{snapshot.blocks.map(b => <option key={b.id} value={b.id}>{b.title}</option>)}</select></label>;
  const editAction = <div className="prd-workbench-compose"><textarea aria-label="定向修改要求" placeholder="说明要如何更新关联图、表格或正文…" value={instruction} onChange={e => setInstruction(e.target.value)} /><button disabled={!!busy || !instruction.trim()} onClick={() => void run('Agent 正在生成修改预览…', async () => await plan('update'))}>生成修改预览</button></div>;
  return <div className="prd-modal-backdrop"><section className="prd-workbench" role="dialog" aria-modal="true" aria-label="PRD 优化工作台">
    <header><div><h2>PRD 优化工作台</h2><small>基于已保存正文 · v{snapshot.document.latest_version ?? '—'} · 按需检查，修改先预览</small></div><button aria-label="关闭工作台" onClick={onClose}><X size={20} /></button></header>
    <nav aria-label="工作台功能">{(Object.keys(labels) as Tab[]).map(key => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{labels[key]}</button>)}<button disabled={!!busy} aria-label="刷新工作台" onClick={() => void run('刷新中…', load)}><RefreshCw size={16} /></button></nav>
    {busy && <p role="status" className="prd-workbench-status">{busy}</p>}{error && <p role="alert" className="prd-error">{error}</p>}
    <main><RecoveryNotice recovery={formRecovery} onRestore={v => {
      setFocus(snapshot.blocks.some(b => b.id === v.focus) ? v.focus : focus); setInstruction(v.instruction); setQuestion(v.question);
      setAText(v.aText); setBText(v.bText); setAnswers(v.answers); setIssueNotes(v.issueNotes); setAltNotes(v.altNotes);
      if (v.revision !== snapshot.document.revision) setError('恢复副本来源版本已变化，请核对关联章节和答案后再生成预览。');
    }} />{tab === 'tasks' && <DocumentJobs documentId={id} revision={snapshot.document.revision} beforePreview={beforePlan} onPreview={preview} />}{tab === 'performance' && <PrdPerformance documentId={id} />}{data && <>
      {tab === 'policies' && <PrdPolicies policies={data.policies ?? []} documentId={id} blocks={snapshot.blocks} seed={policySeed} busy={!!busy} onSave={savePolicy} />}
      {tab === 'impact' && <>
        <p>相对上次全文审核：{data.changed_ids.length} 个章节发生变化，{data.affected_ids.length} 个章节需关注。{!data.baseline_run && '尚无审核基线，暂将全部章节列入关注范围。'}</p>
        {targetPicker}
        <div className="prd-impact-map"><article className="prd-workbench-card prd-impact-origin"><h3>{title(focus)}</h3><p>{data.nodes.find(n => n.id === focus)?.refs.join(' · ') || '尚无明确需求编号'}</p><button onClick={() => locate(focus)}>查看正文</button></article><ArrowRight /><div>{connections.map((edge, index) => {
          const node = data.nodes.find(n => n.id === (edge.from === focus ? edge.to : edge.from))!;
          return <article className={`prd-workbench-card ${node.affected ? 'is-affected' : ''}`} key={index}><label><input type="checkbox" checked={linked.includes(node.id)} onChange={e => setLinked(old => e.target.checked ? [...old, node.id] : old.filter(k => k !== node.id))} />纳入联动</label><button onClick={() => setFocus(node.id)}>{node.title}</button><small>{edge.evidence} · {node.kinds.join(' / ') || '正文'}</small><span>{node.changed ? '已改动' : node.affected ? '可能受影响' : '已有关联'}</span><small>建议：核对规则在{node.kinds.join('、') || '正文'}中的对应表达</small><button onClick={() => locate(node.id)}>定位</button></article>;
        })}{!connections.length && <p>未找到显式依赖或共同编号。这里不会猜测未证实的关系。</p>}</div></div>
        <details><summary>全部需求与图表节点</summary><div className="prd-workbench-grid">{data.nodes.map(n => <button className={`prd-workbench-card ${n.affected ? 'is-affected' : ''}`} key={n.id} onClick={() => setFocus(n.id)}>{n.title}<small>{n.kinds.join(' · ')} {n.refs.join(' · ')}</small></button>)}</div></details>
        {data.tasks.filter(t => t.block_ids.includes(focus)).map(t => <p key={t.id}>关联任务：{t.title} · {t.refs.join('、')} · {t.id}</p>)}
        <h3>衍生产物</h3><p>原型和 PPT 按冻结版本关联；源文档变化后需重新确认并生成。</p>{data.artifacts.map(a => <p key={a.id}>{a.kind} · {a.title} · {a.stale ? '来源版本已变化，待重新生成' : '与当前冻结版本一致'} <button disabled={!!busy} onClick={() => { onArtifact(a.kind); onClose(); }}>查看更新入口</button></p>)}{!data.artifacts.length && <p>尚无已生成的原型或 PPT。</p>}
        <h3>勾选联动范围（{linked.length}）</h3><p>只处理勾选章节。超过 8 章自动拆成多份候选，任务有上限并行；应用前逐份核对，后续候选可在最新正文上复用。</p>
        <div className="prd-workbench-actions">{data.nodes.map(n => <label key={n.id}><input type="checkbox" checked={linked.includes(n.id)} onChange={e => setLinked(old => e.target.checked ? [...old, n.id] : old.filter(k => k !== n.id))} />{n.title}</label>)}</div>
        {editAction}<button disabled={!!busy || !instruction.trim() || !linked.length} onClick={() => void run('提交所选联动候选…', async () => {
          beforePlan(); await submitPrdOperation(id, '/workbench/linked-jobs', { instruction, target_ids: linked, origin_id: focus, expected_revision: data.document_revision }); await load(); setTab('tasks');
        })}>仅为勾选章节生成联动预览</button>
        {data.state.linked_groups?.slice().reverse().map(g => <details key={g.id}><summary>{time(g.created_at)} · {g.selected_ids.length} 章 / {g.job_ids.length} 份候选</summary><p>已选择：{g.selected_ids.map(title).join('、')}</p><p>本次未选择：{g.omitted_ids.map(title).join('、') || '无'}</p><p>{g.artifact_note}</p><button onClick={() => setTab('tasks')}>查看各份执行状态与失败重试</button></details>)}
      </>}
      {tab === 'health' && <>{data.latest_review && <p>最近一次{data.latest_review.scope === 'affected' ? '局部复审' : '全文审核'}{data.latest_review.stale ? '（已过期）' : ''}：{data.latest_review.summary}</p>}<p>显示确定性检查与已有审核证据，不自动发起模型审核，也不计算虚拟质量分。</p><button disabled={!!busy || !data.baseline_run} onClick={() => void run('正在提交受影响内容复审…', async () => { beforePlan(); await onReview(); onClose(); })}>手动复审受影响内容</button><small>仅影响范围做质量复审，保留全文编号/术语检查；进入下一阶段仍需完整审核。</small><div className="prd-workbench-grid"><label><input type="checkbox" checked={onlyCurrentHealth} onChange={e => setOnlyCurrentHealth(e.target.checked)} />只看当前待处理提示</label>{data.health.map(h => ({ ...h, reasons: onlyCurrentHealth ? h.reasons.filter(r => !r.includes('旧审核')) : h.reasons })).filter(h => h.reasons.length).map(h => <article className="prd-workbench-card" key={h.block_id}><h3>{h.title}</h3><ul>{h.reasons.map((r, i) => <li key={i}><small>{r.includes('旧审核') ? '旧审核待复查' : /结构化|可能需要同步/.test(r) ? '资料待同步' : '当前提醒'}</small>{r}</li>)}</ul><button onClick={() => locate(h.block_id)}>查看证据位置</button></article>)}</div>{!data.health.length && <p>当前规则未发现提示项，不代表已通过审核。</p>}</>}
      {tab === 'issues' && <><p>人工确认记录与审核门禁独立。正文变动后，已确认的问题会重新提示核对。</p>{data.issues.map(issue => <article className="prd-workbench-card" key={issue.id}>
        <h3>{issue.description}</h3><small>来源 revision {issue.source_revision} · {issue.stale ? '原审核已过期' : '证据版本未变'} · {statuses[issue.status]}</small><p>{issue.suggestion}</p>{issue.recheck && <p>复查记录：{issue.recheck.summary} · {issue.recheck.run_id}</p>}
        {issue.block_ids.map(blockId => <button key={blockId} onClick={() => locate(blockId)}>{title(blockId)}{issue.changed_blocks.includes(blockId) ? ' · 已变化' : ''}</button>)}
        {issue.evidence?.map((e, i) => <blockquote key={i}><button onClick={() => locate(e.block_id, e.quote)}>{e.quote}</button></blockquote>)}
        <textarea aria-label="问题处理依据" placeholder="记录修改证据、确认依据或保留原因" value={issueNotes[issue.id] ?? issue.note} onChange={e => setIssueNotes({ ...issueNotes, [issue.id]: e.target.value })} />
        <div className="prd-workbench-actions">{['unresolved', 'decision', 'recheck', 'confirmed'].map(status => <button key={status} disabled={!!busy} onClick={() => void run('保存问题记录…', () => update('issue', { id: issue.id, run_id: issue.run_id, status, note: issueNotes[issue.id] ?? issue.note }))}>{statuses[status]}</button>)}</div>
        {issue.note.trim() && <button disabled={!!busy} onClick={() => decisionFrom('issue', issue.id, issue.note, issue.block_ids)}>将已保存依据确认为业务决定</button>}
        <details><summary>处理记录（{issue.history.length}）</summary>{issue.history.map((h, i) => <p key={i}>{time(h.at)} · {statuses[h.status]} · {h.note}</p>)}</details>
      </article>)}{!data.issues.length && <p>暂无已记录的审核问题。</p>}</>}
      {tab === 'alternatives' && <>
        {targetPicker}<p>分别描述 A、B 两套思路；复用文档编辑 Skill 生成候选，保存后可反复比较。成本与风险写入讨论记录，未经确认的数字不会自动当作事实。</p>
        <div className="prd-workbench-grid"><textarea aria-label="方案 A 要求" placeholder="方案 A：例如先人工审核，再批量发布…" value={aText} onChange={e => setAText(e.target.value)} /><textarea aria-label="方案 B 要求" placeholder="方案 B：例如自动预检，异常转人工…" value={bText} onChange={e => setBText(e.target.value)} /></div>
        <button disabled={!!busy || !aText.trim() || !bText.trim()} onClick={() => void run('Agent 正在分别生成 A / B 两套候选方案…', async () => {
          beforePlan();
          const results = await Promise.allSettled([plan('alternative', aText), plan('alternative', bText)]);
          await load();
          setTab('tasks');
          const failed = results.flatMap((r, i) => r.status === 'rejected' ? [`方案 ${i === 0 ? 'A' : 'B'}：${r.reason instanceof Error ? r.reason.message : String(r.reason)}`] : []);
          if (failed.length) throw new Error('成功提交的任务会继续执行。' + failed.join('；'));
        })}>生成并保存两套方案</button>
        {[[aText, 'A'], [bText, 'B']].map(([text, label]) => <button key={label} disabled={!!busy || !text.trim()} onClick={() => void run(`正在生成方案 ${label}…`, async () => { await plan('alternative', text); })}>只生成 {label}</button>)}
        <div className="prd-workbench-actions">{data.state.alternatives?.map(a => <label key={a.id}><input type="checkbox" checked={pair.includes(a.id)} disabled={pair.length >= 2 && !pair.includes(a.id)} onChange={e => setPair(e.target.checked ? [...pair, a.id] : pair.filter(k => k !== a.id))} />{a.plan.summary} · {time(a.created_at)}</label>)}</div>
        <div className="prd-workbench-grid">{selectedAlternatives.map((a, index) => <article className="prd-workbench-card" key={a.id}><h3>方案 {index === 0 ? 'A' : 'B'} · {a.plan.summary}</h3><small>{statuses[a.status]} · {time(a.created_at)} · {a.plan.base_revision === data.document_revision ? '基于当前正文' : '基线已变化，需重新生成'}</small><p>{a.instruction}</p><ul>{a.plan.impact_notes?.map((n, i) => <li key={i}>{n}</li>)}</ul>
          {a.plan.preview.map(p => <details key={p.block_id}><summary>{p.title} · 具体改动</summary><BlockDiff before={p.before} after={p.after} /></details>)}
          <textarea aria-label="方案讨论记录" placeholder="用户流程、维护成本、假设、待决策项…" value={altNotes[a.id] ?? a.note} onChange={e => setAltNotes({ ...altNotes, [a.id]: e.target.value })} />
          <button disabled={!!busy} onClick={() => void run('保存讨论记录…', () => update('alternative_note', { id: a.id, note: altNotes[a.id] ?? a.note }))}>保存讨论记录</button>
          <button disabled={!!busy || a.plan.base_revision !== data.document_revision || a.status !== 'draft'} onClick={() => void run('打开方案预览…', async () => { beforePlan(); preview({ ...a.plan, alternative_id: a.id }); })}>选择此方案，预览后应用</button>
          {a.plan.base_revision !== data.document_revision && <button disabled={!!busy} onClick={() => void run('基于当前版本重新生成方案…', async () => { await plan('alternative', a.instruction, { target_ids: a.target_ids }); await load(); })}>基于当前版本再生成</button>}
        </article>)}</div>
      </>}
      {tab === 'questions' && <>
        <p>从正文待确认标记、未解决批注、审核和最近对话收集候选。先核对、分类和填写答案，再勾选生成正文修改预览。</p>
        <button disabled={!!busy} onClick={() => void run('收集待确认信息…', async () => setData(await mutate<Workbench>(id, '/workbench/collect', { expected_revision: data.revision })))}>收集并去重</button>
        {targetPicker}<div className="prd-workbench-compose"><textarea aria-label="新增待确认问题" placeholder="手动加入待确认的问题…" value={question} onChange={e => setQuestion(e.target.value)} /><button disabled={!!busy || !question.trim()} onClick={() => void run('保存待确认问题…', async () => { await update('question', { text: question, block_ids: focus ? [focus] : [] }); setQuestion(''); })}>加入收集篮</button></div>
        <label>问题状态<select value={questionFilter} onChange={e => setQuestionFilter(e.target.value)}><option value="pending">待处理</option><option value="all">全部</option>{Object.entries(questionStatuses).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        <input aria-label="搜索待确认信息" placeholder="搜索问题、答案、来源或章节…" value={questionQuery} onChange={e => setQuestionQuery(e.target.value)} />
        <p>显示 {visibleQuestions.length} / {data.state.questions?.length ?? 0} 条 · 已选 {pickedQuestions.length} 条（包含筛选外的选择）</p>
        <button onClick={() => setPickedQuestions(old => [...new Set([...old, ...visibleQuestions.filter(q => !['ignored', 'archived', 'applied'].includes(q.status ?? '') && (answers[q.id] ?? q).answer.trim()).map(q => q.id)])])}>选中当前结果中有答案的问题</button>
        <button onClick={() => setPickedQuestions([])}>清空选择</button>
        {!visibleQuestions.length && <p>当前筛选下没有问题，可切换“全部”或清空搜索。</p>}
        {(data.merge_suggestions ?? []).map(pair => <p key={`${pair.left}:${pair.right}`}>可能重复：{data.state.questions?.find(q => q.id === pair.left)?.text} / {data.state.questions?.find(q => q.id === pair.right)?.text}<button disabled={!!busy} onClick={() => void run('合并问题…', () => update('question_merge', { id: pair.left, merge_into: pair.right }))}>确认将前者合并到后者</button></p>)}
        {visibleQuestions.map(original => { const q = answers[original.id] ?? original; return <article className="prd-workbench-card" key={q.id}>
          <label><input type="checkbox" checked={pickedQuestions.includes(q.id)} disabled={['ignored', 'archived', 'applied'].includes(q.status ?? '')} onChange={e => setPickedQuestions(e.target.checked ? [...pickedQuestions, q.id] : pickedQuestions.filter(k => k !== q.id))} />{(data.state.questions ?? []).findIndex(item => item.id === q.id) + 1}. {q.text}</label><small>来源：{q.source} · {questionStatuses[q.status ?? 'open']}</small>
          <select aria-label="问题分类" value={q.category} onChange={e => setAnswers({ ...answers, [q.id]: { ...q, category: e.target.value } })}><option value="business">业务决策</option><option value="information">资料缺失</option><option value="implementation">实现选择</option></select>
          <select aria-label="关联章节" value={q.block_ids[0] ?? ''} onChange={e => setAnswers({ ...answers, [q.id]: { ...q, block_ids: e.target.value ? [e.target.value] : [] } })}><option value="">使用当前目标</option>{snapshot.blocks.map(b => <option key={b.id} value={b.id}>{b.title}</option>)}</select>
          <textarea aria-label="确认答案" placeholder="填写已确认答案，未确定的可以保留…" value={q.answer} onChange={e => setAnswers({ ...answers, [q.id]: { ...q, answer: e.target.value } })} />
          <button disabled={!!busy} onClick={() => void run('保存答案…', async () => { await update('question', q); setAnswers(old => { const next = { ...old }; delete next[q.id]; return next; }); })}>保存答案</button>
          <div>{['open', 'ignored', 'archived'].map(status => <button key={status} disabled={!!busy || !!answers[q.id]} title={answers[q.id] ? '请先保存正在编辑的答案，再改变状态' : undefined} onClick={() => void run('更新问题状态…', async () => { await update('question_state', { id: q.id, question_status: status }); setPickedQuestions(old => old.filter(k => k !== q.id)); })}>{status === 'open' ? '重新打开' : questionStatuses[status]}</button>)}</div>
          {original.answer.trim() && <button disabled={!!busy} onClick={() => decisionFrom('question', q.id, original.answer, original.block_ids)}>把已保存答案确认为业务决定</button>}
          {q.applied_receipt && <p>写入记录：<button onClick={() => onVersion?.(q.applied_receipt!.version)}>全文 v{q.applied_receipt.version}</button> · {time(q.applied_receipt.at)} · 采用答案：{q.applied_receipt.answer}</p>}
          <details><summary>状态历史</summary>{q.history?.map((h, i) => <p key={i}>{time(h.at)} · {questionStatuses[h.status]}</p>)}</details>
        </article>; })}
        <button disabled={!!busy || !pickedQuestions.length} onClick={() => void run('保存选中答案并生成修改预览…', async () => {
          beforePlan(); let current = data;
          for (const key of pickedQuestions) if (answers[key]) current = await update('question', answers[key], current);
          setAnswers(old => Object.fromEntries(Object.entries(old).filter(([key]) => !pickedQuestions.includes(key))));
          await plan('answers', '', { item_ids: pickedQuestions });
        })}>将选中答案生成修改预览</button>
      </>}
      {tab === 'meeting' && <>
        <p>选择两个保存版本，整理目录/正文变化、带来源的未解决问题、待拍板信息和备选方案。</p>
        <div className="prd-workbench-actions">{[0, 1].map(i => <label key={i}>{i ? '较新版本' : '较早版本'}<select value={versionPair[i] ?? ''} onChange={e => { setVersionPair(old => { const next = [...old]; next[i] = Number(e.target.value); return next; }); setMeeting(null); }}>{versions.map(v => <option key={v.id} value={v.version}>v{v.version} · {time(v.created_at)}</option>)}</select></label>)}<button disabled={!!busy || versionPair.length !== 2 || versionPair[0] === versionPair[1]} onClick={() => void run('整理会议材料…', async () => setMeeting(await apiClient.request(documentPath(id) + `/workbench/meeting?left=${versionPair[0]}&right=${versionPair[1]}`)))}>生成会议包</button></div>
        {meeting && <><button onClick={() => { const url = URL.createObjectURL(new Blob([meeting.markdown], { type: 'text/markdown;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = meeting.filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>下载 Markdown 会议包</button><pre className="prd-meeting-preview">{meeting.markdown}</pre></>}
      </>}
      {tab === 'anchors' && <>
        {targetPicker}<p>为原文段落、表格、图源和图片保留位置编号。唯一原文移动或章节改名后沿用编号；内容改写、重复位置有歧义时要求重新选择。</p>
        {(data.state.anchors ?? []).filter(a => a.block_id === focus && !a.missing).map(a => <article className="prd-workbench-card" key={a.id}><label><input type="radio" name="anchor" checked={anchor === a.id} onChange={() => setAnchor(a.id)} />{a.kind} · {a.id.slice(-8)}</label><pre>{a.quote.slice(0, 600)}{a.quote.length > 600 ? '…' : ''}</pre><button onClick={() => locate(a.block_id, a.quote)}>定位原文</button></article>)}
        <textarea aria-label="指定位置修改要求" placeholder="只调整选中的这个位置…" value={instruction} onChange={e => setInstruction(e.target.value)} /><button disabled={!!busy || !anchor || !instruction.trim()} onClick={() => void run('Agent 正在修改指定位置…', async () => await plan('anchor', instruction, { anchor_id: anchor }))}>生成指定位置的修改预览</button>
      </>}
    </>}</main>
  </section></div>;
}
