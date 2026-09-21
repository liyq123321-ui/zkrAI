import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, FileText, Network, Download, Settings2, ImagePlus, Upload, Maximize2, X, Eye, Pencil } from 'lucide-react';
import { apiClient } from '../client';
import { appConfig } from '../config';
import { documentPath, getDocument, mutate, openDocument } from './api';
import { fromBlock, mergeDrafts, statusNames } from './types';
import type { AgentRun, Block, Comment, DocumentReviewIssue, Draft, Snapshot } from './types';
import { BlockPlanMap, BlockTree } from './BlockTree';
import { BlockProperties } from './BlockProperties';
import { BlockToolbar } from './BlockToolbar';
import { FullDocumentReport, FullDocumentView, fullCommentTargets, fullSnapshotChanged } from './FullDocumentView';
import { BlockCommentPanel } from './BlockCommentPanel';
import { GenerationStatus, runTitle, stageText, statusText } from './GenerationStatus';
import { BlockHistory, BlockDiff } from './BlockHistory';
import type { Version } from './BlockHistory';
import { UniverEditor } from './univer/UniverEditor';
import { locateReadingSelection, RenderedPrdDocument } from './RenderedPrdDocument';
import type { ReadingAnnotationAction, ReadingSelection } from './RenderedPrdDocument';
import { conciseProjectTitle } from '../projectTitle';
import type { BlockTextSelection, UniverEditorHandle } from './univer/types';
import './editor.css';

const message = (error: unknown) => error instanceof Error ? error.message : String(error);

function activitySnippet(value: string): string {
  const compact = value
    .replace(/```(?:mermaid|[\w-]+)?/gi, ' ')
    .replace(/[`#>*_|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!compact) return '';
  return compact.length > 150 ? `…${compact.slice(-150)}` : compact;
}

export interface PRDEditorHandle {
  requestClose: (onConfirm: () => void) => void;
}

export interface PrdAgentProgress {
  runId: string;
  status: AgentRun['status'];
  title: string;
  stage: string;
  detail: string;
  completed?: number;
  total?: number;
  currentBlockId?: string;
  currentBlockTitle?: string;
  activityText?: string;
}

interface PRDEditorProps {
  sessionId: string;
  embedded?: boolean;
  onClose?: () => void;
  onAgentProgress?: (progress: PrdAgentProgress | null) => void;
}

type LeaveRequest = { kind: 'url'; target: string } | { kind: 'callback'; onConfirm: () => void };
type ReviewResolution = {
  issue: DocumentReviewIssue;
  selection: ReadingSelection;
  choice: 'primary' | 'alternative' | 'custom';
  customText: string;
};

function locateReviewIssue(issue: DocumentReviewIssue, blocks: Block[]) {
  for (const evidence of issue.evidence ?? []) {
    const block = blocks.find(item => item.id === evidence.block_id);
    const located = block && locateReadingSelection(block, evidence.quote);
    if (located) return located;
  }
  const ids = issue.block_ids.length ? issue.block_ids : blocks.map(block => block.id);
  for (const id of ids) {
    const block = blocks.find(item => item.id === id);
    if (!block) continue;
    for (const line of block.content.replace(/\r\n?/g, '\n').split('\n')) {
      const raw = line.trim();
      if (/^#{1,6}\s+/.test(raw) || /^```/.test(raw)) continue;
      const visible = raw.replace(/^[-*+]\s+/, '')
        .replace(/^\d+[.)]\s+/, '').replace(/\*\*|__|`/g, '').trim();
      if (visible.length < 4) continue;
      const located = locateReadingSelection(block, visible);
      if (located) return located;
    }
  }
  return null;
}

export const PRDEditor = forwardRef<PRDEditorHandle, PRDEditorProps>(function PRDEditor({ sessionId, embedded = false, onClose, onAgentProgress }, ref) {
  const [data, setData] = useState<Snapshot | null>(null);
  const dataRef = useRef<Snapshot | null>(null);
  const univerRef = useRef<UniverEditorHandle>(null);
  const pendingSourceSelectionRef = useRef<ReadingSelection | null>(null);
  const launchLocksRef = useRef(new Set<string>());
  const assetInputRef = useRef<HTMLInputElement>(null);
  const columnsRef = useRef<HTMLDivElement>(null);
  const directoryRef = useRef<HTMLDivElement>(null);
  const allowLeaveRef = useRef(false);
  const agentProgressCallbackRef = useRef(onAgentProgress);
  const lastReportedActiveRunRef = useRef<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const draftRef = useRef<Record<string, Draft>>({});
  const [selected, setSelected] = useState('');
  const selectedRef = useRef('');
  const [editorSelection, setEditorSelection] = useState<BlockTextSelection[]>([]);
  const [checked, setChecked] = useState(new Set<string>());
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [feedback, setFeedback] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [wholeDocumentComment, setWholeDocumentComment] = useState(false);
  const [saving, setSaving] = useState(new Set<string>());
  const saves = useRef(new Set<string>());
  const autoApplied = useRef(new Set<string>());
  const [diffRun, setDiffRun] = useState<AgentRun | null>(null);
  const [history, setHistory] = useState<{ blockId: string; versions: Version[] } | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [leaveRequest, setLeaveRequest] = useState<LeaveRequest | null>(null);
  const [outline, setOutline] = useState('');
  const [template, setTemplate] = useState('alibaba');
  const [settings, setSettings] = useState<{ background: string; global_rules: string } | null>(null);
  const [connected, setConnected] = useState(false);
  const connectedRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [fullSnapshot, setFullSnapshot] = useState<Snapshot | null>(null);
  const [resolutionRunId, setResolutionRunId] = useState<string | null>(null);
  const [mindMapOpen, setMindMapOpen] = useState(false);
  const [documentMode, setDocumentMode] = useState<'read' | 'edit'>('read');
  const [focusedCommentId, setFocusedCommentId] = useState<string | null>(null);
  const [reviewResolution, setReviewResolution] = useState<ReviewResolution | null>(null);
  const [reviewCommentBusy, setReviewCommentBusy] = useState(false);
  const [launchLocks, setLaunchLocks] = useState(new Set<string>());
  selectedRef.current = selected;
  agentProgressCallbackRef.current = onAgentProgress;

  // Leaving the embedded editor is a plain view switch, so a routine close must
  // not cost the user a confirmation step: when nothing is unsaved we hand the
  // view straight back. Only dirty drafts still get the save prompt, which is
  // the one case where a silent exit would actually lose typed content.
  useImperativeHandle(ref, () => ({
    requestClose: (onConfirm) => {
      if (Object.values(draftRef.current).some(draft => draft?.dirty)) {
        setLeaveRequest({ kind: 'callback', onConfirm });
        return;
      }
      allowLeaveRef.current = true;
      onConfirm();
    },
  }), []);

  const updateDrafts = useCallback((next: Record<string, Draft>) => { draftRef.current = next; setDrafts(next); }, []);
  const ingest = useCallback((next: Snapshot) => {
    if (dataRef.current && next.document.revision < dataRef.current.document.revision) return;
    dataRef.current = next;
    setData(next);
    updateDrafts(mergeDrafts(draftRef.current, next.blocks));
    setSelected(id => next.blocks.some(b => b.id === id) ? id : next.blocks[0]?.id ?? '');
  }, [updateDrafts]);
  const refresh = useCallback(async () => { const id = dataRef.current?.document.id; if (id) ingest(await getDocument(id)); }, [ingest]);
  const attempt = async (fn: () => Promise<unknown>) => {
    setError('');
    try { await fn(); } catch (e) {
      const detail = message(e);
      if (/已有运行中的作业|正在处理全文|请等待当前生成或审核任务完成/.test(detail)) {
        setNotice('已关联到正在运行的任务，可在生成队列查看进度。');
        await refresh().catch(() => undefined);
        return;
      }
      setError(detail);
    }
  };
  const launchOnce = async (key: string, fn: () => Promise<unknown>) => {
    if (launchLocksRef.current.has(key)) return;
    launchLocksRef.current.add(key); setLaunchLocks(new Set(launchLocksRef.current));
    try { await fn(); } finally {
      launchLocksRef.current.delete(key); setLaunchLocks(new Set(launchLocksRef.current));
    }
  };

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
      source.addEventListener('snapshot', event => { try {
        ingest(JSON.parse((event as MessageEvent).data)); connectedRef.current = true; setConnected(true);
      } catch { connectedRef.current = false; setConnected(false); } });
      source.onerror = () => { connectedRef.current = false; setConnected(false); };
    }
    const timer = setInterval(() => {
      if (connectedRef.current) return;
      void refresh().catch(() => { connectedRef.current = false; setConnected(false); });
    }, 5000);
    return () => { connectedRef.current = false; source?.close(); clearInterval(timer); };
  }, [data?.document.id, ingest, refresh]);
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => {
      if (!allowLeaveRef.current && Object.values(draftRef.current).some(d => d.dirty)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', before);
    return () => window.removeEventListener('beforeunload', before);
  }, []);
  useEffect(() => () => agentProgressCallbackRef.current?.(null), []);
  useLayoutEffect(() => {
    const columns = columnsRef.current, directory = directoryRef.current;
    const host = columns?.querySelector<HTMLElement>('.prd-univer-host, .prd-rendered-document');
    if (!columns || !directory || !host) return;
    const align = () => {
      const thumbnail = columns.querySelector<HTMLElement>('.prd-map-thumbnail');
      if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 700px)').matches) {
        if (thumbnail) thumbnail.style.marginTop = '0px';
        directory.style.marginTop = '0px';
        directory.style.height = '';
        return;
      }
      const columnsRect = columns.getBoundingClientRect(), hostRect = host.getBoundingClientRect();
      const thumbnailHeight = thumbnail?.getBoundingClientRect().height ?? 0;
      if (thumbnail) thumbnail.style.marginTop = `${Math.max(0, hostRect.top - columnsRect.top - thumbnailHeight)}px`;
      directory.style.marginTop = '0px';
      if (hostRect.height > 0) directory.style.height = `${hostRect.height}px`;
    };
    align();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(align);
    observer?.observe(columns);
    observer?.observe(host);
    const main = columns.querySelector<HTMLElement>('.prd-main-editor');
    if (main) observer?.observe(main);
    window.addEventListener('resize', align);
    return () => {
      observer?.disconnect(); window.removeEventListener('resize', align);
      const thumbnail = columns.querySelector<HTMLElement>('.prd-map-thumbnail');
      if (thumbnail) thumbnail.style.marginTop = '';
      directory.style.marginTop = '';
      directory.style.height = '';
    };
  }, [data?.document.id, mindMapOpen, documentMode]);
  useEffect(() => {
    const pending = pendingSourceSelectionRef.current;
    if (documentMode !== 'edit' || !pending || pending.blockId !== selected) return;
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => {
      univerRef.current?.focusBlock(pending.blockId, pending.startOffset, pending.endOffset);
      pendingSourceSelectionRef.current = null;
    }));
    return () => cancelAnimationFrame(frame);
  }, [documentMode, selected]);
  useEffect(() => {
    if (!focusedCommentId) return;
    requestAnimationFrame(() => document.getElementById(`prd-comment-${focusedCommentId}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }, [focusedCommentId, data?.comments]);
  useEffect(() => {
    if (!resolutionRunId || !data) return;
    const run = data.runs.find(item => item.id === resolutionRunId);
    if (!run || run.status === 'queued' || run.status === 'running') return;
    setResolutionRunId(null);
    if (run.status === 'completed' && run.apply_status === 'applied') {
      setFullSnapshot(data);
      setNotice('AI 已生成解决审核问题的新版本全文；原审核意见已归档。');
    } else if (run.status === 'failed') setError(run.error || '自动解决审核问题失败，请重试。');
    else if (run.apply_status === 'conflict') setError('自动修订期间正文发生变化，未覆盖你的修改。请重新审核后再解决。');
  }, [data, resolutionRunId]);

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
  const saveAll = useCallback(async () => {
    const ids = Object.entries(draftRef.current).filter(([, draft]) => draft.dirty).map(([id]) => id);
    for (const id of ids) await save(id);
    if (Object.values(draftRef.current).some(draft => draft.dirty)) throw new Error('保存期间又有新的输入，请再次点击保存。');
  }, [save]);

  function requireSaved(action: string, ids?: string[]) {
    const targets = ids ? ids.map(id => draftRef.current[id]) : Object.values(draftRef.current);
    if (targets.some(draft => draft?.dirty)) throw new Error(`存在未保存内容，请先点击“保存”，再${action}。`);
  }

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

  function editBlock(id: string, patch: Partial<Draft>) {
    updateDrafts({ ...draftRef.current, [id]: { ...draftRef.current[id], ...patch, dirty: true, error: undefined } });
  }
  function edit(patch: Partial<Draft>) { editBlock(selected, patch); }
  function select(id: string) {
    if (selectedRef.current === id) { univerRef.current?.focusBlock(id); return; }
    setSelected(id); setEditorSelection([]); setStart(''); setEnd(''); setFeedback(''); setHistory(null); setDiffRun(null); setFocusedCommentId(null);
  }
  function followVisibleBlock(id: string) {
    if (selectedRef.current === id) return;
    selectedRef.current = id;
    setSelected(id); setEditorSelection([]); setStart(''); setEnd('');
  }
  async function refreshFull() {
    setBusy(true);
    try {
      requireSaved('刷新全文');
      const current = await getDocument(dataRef.current!.document.id);
      ingest(current); setFullSnapshot(current); setEditorSelection([]); setStart(''); setEnd(''); setHistory(null); setDiffRun(null);
    } finally { setBusy(false); }
  }
  async function reviewFull() {
    await launchOnce('document', async () => {
      setBusy(true);
      try {
        requireSaved('审核全文');
        const current = await getDocument(dataRef.current!.document.id);
        ingest(current); setFullSnapshot(current);
        await mutate(current.document.id, '/review', { request_id: crypto.randomUUID(), expected_revision: current.document.revision });
        await refresh();
      } finally { setBusy(false); }
    });
  }
  async function resolveFullReview() {
    if (!wholeReport) return;
    await launchOnce('document', async () => {
      setBusy(true);
      try {
        requireSaved('自动解决审核问题');
        const current = await getDocument(dataRef.current!.document.id);
        if (wholeReport.stale || wholeReport.apply_status === 'conflict') {
          ingest(current); throw new Error('审核意见已过期，请先更新并重新审核全文。');
        }
        const run = await mutate<AgentRun>(current.document.id, `/review/${wholeReport.id}/resolve`, {
          request_id: crypto.randomUUID(), expected_revision: current.document.revision,
        });
        setResolutionRunId(run.id);
        await refresh();
      } finally { setBusy(false); }
    });
  }
  async function run(kind: 'generate' | 'revise' | 'review', blockId = selected, commentId?: string) {
    requireSaved('运行 AI', [blockId]);
    if (kind === 'revise' && !commentId && !feedback.trim()) throw new Error('请填写右侧修改意见。');
    if ((start && !end) || (!start && end)) throw new Error('请同时填写起始行和结束行。');
    await launchOnce(`block:${blockId}`, async () => {
      const docId = dataRef.current!.document.id;
      await mutate(docId, `/blocks/${blockId}/runs`, { type: kind, request_id: crypto.randomUUID(), feedback,
        ...(kind === 'revise' && !commentId && start ? { start_line: Number(start), end_line: Number(end) } : {}), comment_id: commentId });
      await refresh();
    });
  }
  async function planDocument() {
    await launchOnce('plan', async () => {
      await mutate(dataRef.current!.document.id, '/plan', { request_id: crypto.randomUUID() });
      await refresh();
    });
  }
  async function addComment() {
    const ids = wholeDocumentComment ? dataRef.current!.blocks.map(block => block.id)
      : editorSelection.length ? [...new Set(editorSelection.map(selection => selection.blockId))]
      : checked.size ? [...checked] : [selected];
    requireSaved('添加批注', ids);
    if (!wholeDocumentComment && ids.length > 1 && (start || end) && !editorSelection.length) throw new Error('多块批注请使用整块范围；起止行只用于当前 Block。');
    if (!wholeDocumentComment && ((start && !end) || (!start && end))) throw new Error('请同时填写起始行和结束行。');
    const current = await getDocument(dataRef.current!.document.id);
    const targets = wholeDocumentComment ? fullCommentTargets(current.blocks)
      : editorSelection.length ? editorSelection.map(selection => ({ block_id: selection.blockId,
      base_version: current.blocks.find(block => block.id === selection.blockId)!.version,
      start_line: selection.startLine, end_line: selection.endLine })) : ids.map(id => ({ block_id: id, base_version: current.blocks.find(b => b.id === id)!.version,
        ...(ids.length === 1 && start && end ? { start_line: Number(start), end_line: Number(end) } : {}) }));
    ingest(await mutate(current.document.id, '/comments', { text: feedback, targets }));
    setFeedback(''); setStart(''); setEnd(''); setEditorSelection([]);
  }
  async function addReadingComment(selection: ReadingSelection, text: string, action: ReadingAnnotationAction) {
    requireSaved('添加批注', [selection.blockId]);
    const current = await getDocument(dataRef.current!.document.id);
    const target = current.blocks.find(item => item.id === selection.blockId);
    if (!target) throw new Error('批注对应的 Block 已不存在，请刷新后重试。');
    const knownIds = new Set(current.comments.map(comment => comment.id));
    const next = await mutate<Snapshot>(current.document.id, '/comments', { text, targets: [{
      block_id: selection.blockId, base_version: target.version,
      start_line: selection.startLine, end_line: selection.endLine,
      selected_text: selection.quote, action,
    }] });
    ingest(next);
    setSelected(selection.blockId);
    setFocusedCommentId(next.comments.find(comment => !knownIds.has(comment.id))?.id ?? null);
  }
  function openReviewIssue(issue: DocumentReviewIssue) {
    const currentBlocks = dataRef.current?.blocks.map(block => ({ ...block, content: draftRef.current[block.id]?.content ?? block.content })) ?? [];
    const selection = locateReviewIssue(issue, currentBlocks);
    if (!selection) { setError('该审核意见没有可定位的正文，请重新审核全文后再试。'); return; }
    setError('');
    setDocumentMode('read');
    select(selection.blockId);
    setChecked(new Set());
    setReviewResolution({ issue, selection, choice: 'primary', customText: '' });
  }
  async function createReviewComment() {
    if (!reviewResolution) return;
    const { issue, selection, choice, customText } = reviewResolution;
    const solution = choice === 'primary' ? issue.suggestion
      : choice === 'alternative' ? issue.alternative_suggestion?.trim() ?? '' : customText.trim();
    if (!solution) throw new Error('请填写你的解决方案。');
    setReviewCommentBusy(true);
    try {
      await addReadingComment(selection, `审核意见：${issue.description}\n解决建议：${solution}`, 'comment');
      setReviewResolution(null);
    } finally { setReviewCommentBusy(false); }
  }
  function editReadingSelection(selection: ReadingSelection) {
    select(selection.blockId);
    pendingSourceSelectionRef.current = selection;
    setDocumentMode('edit');
  }
  function openReadingComment(commentId: string, blockId: string) {
    select(blockId);
    setFocusedCommentId(commentId);
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
      requireSaved('导入新大纲');
      const current = dataRef.current!;
      const next = await mutate<Snapshot>(current.document.id, '/outline', { template, outline, expected_revision: current.document.revision }, 'PUT');
      ingest(next); setShowImport(false);
      await mutate(next.document.id, '/plan', { request_id: crypto.randomUUID() });
      await refresh();
    } finally { setBusy(false); }
  }
  async function readFile(file: File) {
    requireSaved('导入文档');
    if (file.name.toLowerCase().endsWith('.docx')) {
      if (file.size > 24 * 1024 * 1024) throw new Error('DOCX 文件不能超过 24 MB');
      const content = await fileBase64(file);
      const current = dataRef.current!;
      const next = await mutate<Snapshot>(current.document.id, '/import/docx', {
        filename: file.name, content_base64: content, expected_revision: current.document.revision,
      });
      ingest(next); setShowImport(false); setNotice('DOCX 修改已按 Block 写回，并保留了各 Block 的版本历史。');
      return;
    }
    if (file.size > 8 * 1024 * 1024) throw new Error('文件不能超过 8 MB');
    const content = await fileBase64(file);
    const result = await mutate<{ outline: string }>(dataRef.current!.document.id, '/import', { filename: file.name, content_base64: content, expected_revision: dataRef.current!.document.revision });
    setTemplate('custom'); setOutline(result.outline); setNotice('已提取文件文字，请在导入前仅保留所需章节标题。');
  }
  async function exportDocx() {
    requireSaved('导出 DOCX');
    const response = await fetch(appConfig.apiBaseUrl + documentPath(dataRef.current!.document.id) + '/export/docx', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    if (!response.ok) throw new Error('导出失败，请重试');
    const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
    link.href = url; link.download = `${conciseProjectTitle(dataRef.current!.document.title, 'PRD')}.docx`; link.click(); URL.revokeObjectURL(url);
  }
  async function uploadAsset(file: File) {
    if (!dataRef.current || !selected) throw new Error('请先选择要插图的 Block');
    if (file.size > 12 * 1024 * 1024) throw new Error('图片不能超过 12 MB');
    const result = await mutate<{ id: string }>(dataRef.current.document.id, '/assets', {
      block_id: selected, filename: file.name, mime_type: file.type || undefined,
      content_base64: await fileBase64(file),
    });
    await refresh();
    setNotice(`图片已加入当前 Block，将自动插入 DOCX（${result.id}）。`);
  }
  async function submit() {
    requireSaved('进入下一阶段');
    const current = await getDocument(dataRef.current!.document.id);
    await mutate(current.document.id, '/submit', { expected_revision: current.document.revision });
    setNotice('已进入 PRD 人工确认阶段。返回项目后可确认当前 PRD 并进入任务拆分。');
    await refresh();
  }
  const block = data?.blocks.find(b => b.id === selected), draft = drafts[selected];
  const unsavedCount = Object.values(drafts).filter(item => item.dirty).length;
  const initialRun = data?.runs.find(r => r.type === 'initial');
  const initialActive = !!initialRun && ['queued', 'running'].includes(initialRun.status);
  const wholeReviewActive = data?.runs.some(r => r.type === 'document_review' && ['queued', 'running'].includes(r.status)) ?? false;
  const wholeRevisionActive = data?.runs.some(r => r.type === 'document_revision' && ['queued', 'running'].includes(r.status)) ?? false;
  const active = initialActive || wholeReviewActive || wholeRevisionActive || launchLocks.has(`block:${selected}`)
    || (data?.runs.some(r => r.block_id === selected && ['queued', 'running'].includes(r.status)) ?? false);
  const anyActive = launchLocks.size > 0 || (data?.runs.some(r => ['queued', 'running'].includes(r.status)) ?? false);
  const latestWholeReview = data?.runs.filter(r => r.type === 'document_review').at(-1);
  const wholeReviewClear = latestWholeReview?.status === 'completed'
    && latestWholeReview.apply_status === 'applied'
    && !latestWholeReview.stale
    && Array.isArray(latestWholeReview.result?.issues)
    && latestWholeReview.result.issues.every(issue => issue.severity === 'info');
  const wholeReport = latestWholeReview?.status === 'completed' && latestWholeReview.apply_status !== 'resolved' ? latestWholeReview : undefined;
  const wholeReviewError = latestWholeReview?.status === 'failed' ? latestWholeReview.error || 'AI 审核失败，请重试。' : undefined;
  const candidates = data?.runs.filter(r => r.block_id === selected && r.status === 'completed' && r.apply_status !== 'applied') ?? [];
  const viewBlocks = data?.blocks.map(b => ({ ...b,
    ...(drafts[b.id] ? { title: drafts[b.id].title, parent_id: drafts[b.id].parent_id, dependencies: drafts[b.id].dependencies } : {}),
    status: drafts[b.id]?.dirty ? 'editing' as const : b.status,
  })) ?? [];
  const streamByBlock = new Map((data?.runs ?? []).flatMap(run => {
    if (run.status !== 'running') return [];
    if (run.block_id && typeof run.result?.stream_content === 'string') return [[run.block_id, run.result.stream_content] as const];
    return Object.entries(run.result?.stream_blocks ?? {});
  }));
  const editorBlocksKey = JSON.stringify([data?.document.id, data?.blocks.map(block => [
    block.id, block.title, block.parent_id, block.order, block.version,
    drafts[block.id]?.dirty ? drafts[block.id].content : streamByBlock.get(block.id) ?? drafts[block.id]?.content ?? block.content,
  ])]);
  const editorBlocks = useMemo(() => data?.blocks.map(b => ({ ...b, content: drafts[b.id]?.dirty
    ? drafts[b.id].content : streamByBlock.get(b.id) ?? drafts[b.id]?.content ?? b.content })) ?? [], [editorBlocksKey]);
  const activeStreamRun = data?.runs.find(run => (run.status === 'queued' || run.status === 'running')
    && (run.result?.current_block_id || run.block_id || Object.keys(run.result?.stream_blocks ?? {}).length));
  const streamingBlockId = activeStreamRun?.result?.current_block_id ?? activeStreamRun?.block_id
    ?? Object.keys(activeStreamRun?.result?.stream_blocks ?? {}).at(-1);
  const streamingBlock = editorBlocks.find(item => item.id === streamingBlockId);
  const streamingIsDiagram = Boolean(streamingBlock && (/```mermaid/i.test(streamingBlock.content) || /图|diagram/i.test(streamingBlock.title)));
  const streamingLabel = streamingIsDiagram ? '正在生成并渲染图表' : '正在流式写入';
  const activeProgressRun = [...(data?.runs ?? [])].reverse().find(run => run.status === 'queued' || run.status === 'running');
  const progressRun = activeProgressRun ?? (lastReportedActiveRunRef.current
    ? data?.runs.find(run => run.id === lastReportedActiveRunRef.current)
    : undefined);
  const progressBlockId = progressRun?.result?.current_block_id ?? progressRun?.block_id
    ?? Object.keys(progressRun?.result?.stream_blocks ?? {}).at(-1);
  const progressBlock = data?.blocks.find(item => item.id === progressBlockId);
  const progressIsDiagram = Boolean(progressBlock && (/```mermaid/i.test(progressBlock.content) || /图|diagram/i.test(progressBlock.title)));
  const progressStream = typeof progressRun?.result?.stream_content === 'string'
    ? progressRun.result.stream_content
    : progressBlockId && typeof progressRun?.result?.stream_blocks?.[progressBlockId] === 'string'
      ? progressRun.result.stream_blocks[progressBlockId]
      : Object.values(progressRun?.result?.stream_blocks ?? {}).at(-1) ?? '';
  const streamedActivity = activitySnippet(progressStream);
  const progressActivityText = streamedActivity
    ? `实时写入：${streamedActivity}`
    : progressIsDiagram
      ? '正在整理图表节点与连线，等待下一批渲染内容…'
      : typeof progressRun?.result?.stream_sentence_count === 'number' && progressRun.result.stream_sentence_count > 0
        ? `已接收 ${progressRun.result.stream_sentence_count} 个完整句子，正在继续生成下一批内容…`
        : progressRun ? `${stageText(progressRun)}，正在等待模型返回下一批内容…` : '';
  const progressKey = progressRun ? JSON.stringify([
    progressRun.id, progressRun.status, progressRun.result?.progress_stage,
    progressRun.result?.completed, progressRun.result?.total, progressBlockId,
    progressRun.result?.stream_sentence_count, progressActivityText,
  ]) : 'none';
  useEffect(() => {
    const callback = agentProgressCallbackRef.current;
    if (!callback) return;
    if (!progressRun) {
      callback(null);
      return;
    }
    if (activeProgressRun) lastReportedActiveRunRef.current = activeProgressRun.id;
    callback({
      runId: progressRun.id,
      status: progressRun.status,
      title: runTitle(progressRun, data?.blocks ?? []),
      stage: progressIsDiagram && progressRun.status === 'running' ? '正在生成并渲染图表' : stageText(progressRun),
      detail: statusText(progressRun),
      completed: progressRun.result?.completed,
      total: progressRun.result?.total,
      currentBlockId: progressBlock?.id,
      currentBlockTitle: progressBlock?.title,
      activityText: progressActivityText,
    });
    if (!activeProgressRun) lastReportedActiveRunRef.current = null;
  }, [progressKey]);
  function openProgressBlock(blockId: string) {
    const target = editorBlocks.find(item => item.id === blockId);
    setDocumentMode(target && (/```mermaid/i.test(target.content) || /图|diagram/i.test(target.title)) ? 'read' : 'edit');
    select(blockId);
  }
  const univerStructureKey = data?.blocks.map(b => `${b.id}:${b.title}:${b.parent_id ?? ''}:${b.order}`).join('|') ?? '';
  const canPlan = Boolean(data && !data.blocks.some(b => b.content || drafts[b.id]?.dirty));
  const finishLeave = () => {
    const request = leaveRequest;
    if (!request) return;
    setLeaveRequest(null);
    allowLeaveRef.current = true;
    if (request.kind === 'url') window.location.assign(request.target);
    else request.onConfirm();
  };
  const requestEmbeddedClose = () => {
    const onConfirm = onClose ?? (() => undefined);
    if (unsavedCount) { setLeaveRequest({ kind: 'callback', onConfirm }); return; }
    allowLeaveRef.current = true;
    onConfirm();
  };
  return <main className={`prd-page${embedded ? ' is-embedded' : ''}`}>
    <header className="prd-header">{embedded
      ? <button type="button" className="prd-back" aria-label="关闭 PRD 编辑器" title="关闭并返回该项目的顶层图" onClick={requestEmbeddedClose}><ArrowLeft size={18} /></button>
      : <a href="/" aria-label="返回项目" onClick={event => { if (unsavedCount) { event.preventDefault(); setLeaveRequest({ kind: 'url', target: '/' }); } }}><ArrowLeft size={18} />返回项目</a>}<span className="prd-header-divider" /><FileText size={22} /><div><h1>{conciseProjectTitle(data?.document.title, 'Block PRD 编辑器')}</h1><small>图文阅读 · Univer 源码编辑 · 手动保存</small></div>
      <div className="prd-header-actions"><span className="prd-live">{connected ? '● 实时同步' : '定时同步'}</span>{canPlan && <button disabled={anyActive || data!.document.plan_status === 'generating'} onClick={() => void attempt(planDocument)}><Network size={15} />{data!.document.plan_status === 'generating' || launchLocks.has('plan') ? '正在规划…' : 'AI 规划'}</button>}<button onClick={() => setShowImport(true)} disabled={!data || initialActive}><Upload size={15} />导入</button><input ref={assetInputRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.svg,image/png,image/jpeg,image/webp,image/svg+xml" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void attempt(() => uploadAsset(file)); }} /><button disabled={!data || !selected} onClick={() => assetInputRef.current?.click()}><ImagePlus size={15} />插入图片</button><button aria-label="文档设置" disabled={!data} onClick={() => setSettings({ background: data!.document.background, global_rules: data!.document.global_rules })}><Settings2 size={16} /></button><button disabled={!data || busy} onClick={() => void attempt(exportDocx)}><Download size={15} />导出 DOCX</button><button className="prd-primary" title={wholeReviewClear ? '全文审核已通过，进入 PRD 人工确认阶段' : '请先完成当前版本的全文审核并解决待处理项'} disabled={!data || busy || anyActive || !wholeReviewClear} onClick={() => void attempt(submit)}>进入下一阶段</button></div>
    </header>
    {error && <div className="prd-alert" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}
    {!data ? <div className="prd-loading">{error ? <button onClick={() => void attempt(async () => ingest(await openDocument(sessionId)))}>重新加载</button> : '正在恢复文档…'}</div> : <>
      <div ref={columnsRef} className={`prd-columns is-full-document${mindMapOpen ? ' is-mindmap-open' : ''}`}><aside className="prd-left-rail">
        {mindMapOpen ? <PrdMindMapPane blocks={viewBlocks} selected={selected} onSelect={select} onClose={() => setMindMapOpen(false)} /> : <>
          <button className="prd-map-thumbnail" aria-label="展开 PRD 思维导图" aria-expanded="false" onClick={() => setMindMapOpen(true)}>
            <BlockPlanMap blocks={viewBlocks} selected={selected} onSelect={select} interactive={false} maxDepth={2} />
            <span className="prd-map-thumbnail-hint"><Maximize2 size={14} />展开思维导图</span>
          </button>
          <div ref={directoryRef} className="prd-directory"><div className="prd-section-heading">文档目录<small>{checked.size ? `${checked.size} 已选` : '定位与批注范围'}</small></div><BlockTree blocks={viewBlocks} selected={selected} checked={checked} onSelect={select} onCheck={id => setChecked(old => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next; })} /></div>
        </>}
      </aside>
        <section className="prd-main-editor"><FullDocumentView snapshot={data}
          changed={fullSnapshot ? fullSnapshotChanged(fullSnapshot, data) : false} active={wholeReviewActive} loading={busy || anyActive} report={wholeReport}
          resolving={wholeRevisionActive} reviewError={wholeReviewError}
          onSelect={id => { select(id); setChecked(new Set()); }}
          onRefresh={() => void attempt(refreshFull)} onReview={() => void attempt(reviewFull)} onResolve={() => void attempt(resolveFullReview)} deferReport />
          {block && draft && <>
          <section className="prd-current-block-tools" aria-label="当前 Block 工具"><div className="prd-save-state"><span className={`prd-dot is-${draft.dirty ? 'editing' : block.status}`} /><strong>当前 Block · {block.title}</strong><span><span>{saving.has(selected) ? '保存中…' : draft.error ? '保存失败，输入已保留' : draft.dirty ? '未保存' : '已保存'}</span> · {statusNames[block.status]} · v{block.version}</span></div>
          <div className="prd-document-mode" role="group" aria-label="文档显示模式"><button className={documentMode === 'read' ? 'is-selected' : ''} onClick={() => setDocumentMode('read')}><Eye size={15} />图文阅读</button><button className={documentMode === 'edit' ? 'is-selected' : ''} onClick={() => setDocumentMode('edit')}><Pencil size={15} />编辑源码</button></div>
          <BlockToolbar dirty={unsavedCount > 0} saving={saving.size > 0} active={active} hasContent={Boolean(draft.content)} onSave={() => void attempt(saveAll)} onRun={kind => void attempt(() => run(kind))} onHistory={() => void attempt(async () => setHistory({ blockId: selected, versions: await apiClient.request<Version[]>(documentPath(data.document.id) + `/blocks/${selected}/history`) }))} />
          <details className="prd-current-block-settings"><summary>Block 设置与图片</summary><BlockProperties block={block} draft={draft} blocks={data.blocks} onEdit={edit} />
          {(data.assets ?? []).some(asset => asset.block_id === block.id) && <section className="prd-assets" aria-label="当前 Block 图片"><strong>DOCX 图片</strong><div>{(data.assets ?? []).filter(asset => asset.block_id === block.id).map(asset => <figure key={asset.id}><img src={`${appConfig.apiBaseUrl}${documentPath(data.document.id)}/assets/${encodeURIComponent(asset.id)}`} alt={asset.file_name} /><figcaption>{asset.file_name}</figcaption></figure>)}</div></section>}</details></section>
          {documentMode === 'read' ? <RenderedPrdDocument blocks={editorBlocks} comments={data.comments} selectedBlockId={selected} streamingBlockId={streamingBlockId} streamingLabel={streamingLabel} reviewSelection={reviewResolution?.selection} onSelectBlock={id => { if (selectedRef.current !== id) select(id); }} onCreateComment={addReadingComment} onEditSource={editReadingSelection} onOpenComment={openReadingComment} /> : <UniverEditor ref={univerRef} key={`${data.document.id}:${univerStructureKey}`} documentId={`prd-${data.document.id}`} title={conciseProjectTitle(data.document.title, 'PRD')}
            blocks={editorBlocks} selectedBlockId={selected} streamingBlockId={streamingBlockId} streamingLabel={streamingLabel}
            onSelectBlock={id => { if (selectedRef.current !== id) select(id); }}
            onViewportBlockChange={followVisibleBlock}
            onBlockContentChange={(id, content) => editBlock(id, { content })}
            onSelection={selections => { const first = selections[0]; if (first) setSelected(first.blockId);
              if (!wholeDocumentComment) { setEditorSelection(selections); setChecked(new Set(selections.map(item => item.blockId)));
                if (first) { setStart(String(first.startLine)); setEnd(String(first.endLine)); } } }}
            onBoundaryViolation={setNotice} />}
          <FullDocumentReport snapshot={data} loading={busy || anyActive} resolving={wholeRevisionActive} report={wholeReport} reviewError={wholeReviewError}
            onSelect={id => { select(id); setChecked(new Set()); }} onOpenIssue={openReviewIssue} onResolve={() => void attempt(resolveFullReview)} />
          {draft.dirty && draft.version !== block.version && <div className="prd-conflict"><strong>保存版本已变化，人工输入已保留</strong><BlockDiff before={block.content} after={draft.content} /><button onClick={() => void attempt(() => save(selected, true))}>基于当前 v{block.version} 保存我的草稿</button></div>}
          {candidates.map(r => <div className="prd-conflict" key={r.id}><strong>{r.apply_status === 'conflict' || r.base_version !== block.version ? 'AI 结果基于旧版本生成' : draft.dirty ? 'AI 已完成，等待你保存当前输入' : 'AI 结果待应用'}</strong><p>基线 v{r.base_version} → 当前 v{block.version}</p><div><button onClick={() => setDiffRun(r)}>查看 Diff</button><button onClick={() => void attempt(async () => { requireSaved('重新生成', [r.block_id!]); await mutate(data.document.id, `/runs/${r.id}/retry`, { request_id: crypto.randomUUID() }); await refresh(); })}>基于当前版本重新生成</button><button disabled={draft.dirty} onClick={() => void attempt(async () => { await apply(r, true); setDiffRun(null); })}>仍然应用</button></div></div>)}
          {data.runs.filter(r => r.block_id === selected && r.result?.review_notes?.length).slice(-1).map(r => <div className="prd-review-notes" key={r.id}><strong>本块审核意见</strong><ul>{r.result!.review_notes!.map((note, i) => <li key={i}>{note}</li>)}</ul></div>)}
        </>}</section>
        {!mindMapOpen && <aside className="prd-right"><GenerationStatus key={data.document.id} documentId={data.document.id} runs={data.runs} blocks={data.blocks} notice={notice} onDismissNotice={() => setNotice('')} onSelectBlock={openProgressBlock} onCancel={r => void attempt(async () => { await mutate(data.document.id, `/runs/${r.id}/cancel`, {}); await refresh(); })} /><BlockCommentPanel comments={data.comments} blocks={data.blocks} current={selected} focusedCommentId={focusedCommentId} feedback={feedback} onFeedback={setFeedback} start={start} end={end} onRange={(s, e) => { setStart(s); setEnd(e); }} count={wholeDocumentComment ? data.blocks.length : checked.size || new Set(editorSelection.map(item => item.blockId)).size || 1} wholeDocument={wholeDocumentComment} onWholeDocument={value => { setWholeDocumentComment(value); if (value) { setStart(''); setEnd(''); setEditorSelection([]); } }} onAdd={() => void attempt(addComment)} onRevise={c => void attempt(() => reviseComment(c))} onResolve={c => void attempt(async () => ingest(await mutate(data.document.id, `/comments/${c.id}`, { resolved: !c.resolved }, 'PATCH')))} /></aside>}</div>
    </>}
    {reviewResolution && <section className="prd-review-solution-popover" role="dialog" aria-modal="false" aria-label="选择审核意见解决方案">
      <header><div><strong>为这条审核意见添加批注</strong><small>{reviewResolution.issue.description}</small></div><button aria-label="关闭解决建议" onClick={() => setReviewResolution(null)}><X size={16} /></button></header>
      <div className="prd-review-solution-options">
        <label className={reviewResolution.choice === 'primary' ? 'is-selected' : ''}><input type="radio" name="review-solution" checked={reviewResolution.choice === 'primary'} onChange={() => setReviewResolution({ ...reviewResolution, choice: 'primary' })} /><span><strong>方案 1 · Agent 建议</strong><small>{reviewResolution.issue.suggestion}</small></span></label>
        {reviewResolution.issue.alternative_suggestion?.trim() && <label className={reviewResolution.choice === 'alternative' ? 'is-selected' : ''}><input type="radio" name="review-solution" checked={reviewResolution.choice === 'alternative'} onChange={() => setReviewResolution({ ...reviewResolution, choice: 'alternative' })} /><span><strong>方案 2 · Agent 建议</strong><small>{reviewResolution.issue.alternative_suggestion}</small></span></label>}
        <label className={reviewResolution.choice === 'custom' ? 'is-selected' : ''}><input type="radio" name="review-solution" checked={reviewResolution.choice === 'custom'} onChange={() => setReviewResolution({ ...reviewResolution, choice: 'custom' })} /><span><strong>方案 {reviewResolution.issue.alternative_suggestion?.trim() ? '3' : '2'} · 自定义</strong><textarea rows={3} aria-label="自定义解决方案" value={reviewResolution.customText} onFocus={() => setReviewResolution({ ...reviewResolution, choice: 'custom' })} onChange={event => setReviewResolution({ ...reviewResolution, choice: 'custom', customText: event.target.value })} placeholder="输入你的处理方式…" /></span></label>
      </div>
      <footer><button onClick={() => setReviewResolution(null)}>取消</button><button className="prd-primary" disabled={reviewCommentBusy || (reviewResolution.choice === 'custom' && !reviewResolution.customText.trim())} onClick={() => void attempt(createReviewComment)}>{reviewCommentBusy ? '生成批注中…' : '确认并生成批注'}</button></footer>
    </section>}
    {leaveRequest && <div className="prd-modal-backdrop"><section className="prd-modal prd-unsaved-modal" role="dialog" aria-modal="true" aria-label={unsavedCount ? '未保存更改' : '关闭 PRD'}><h2>{unsavedCount ? '尚未保存，是否保存？' : '关闭 PRD 编辑窗口？'}</h2><p>{unsavedCount ? `当前有 ${unsavedCount} 个 Block 包含未保存的修改。` : '关闭后将返回该项目的顶层图视图。'} 正在生成、审核或修订的 AI 任务不会取消，会继续在后台运行。</p><footer><button onClick={() => setLeaveRequest(null)}>继续编辑</button>{unsavedCount ? <><button onClick={finishLeave}>不保存并离开</button><button className="prd-primary" disabled={saving.size > 0} onClick={() => void attempt(async () => { await saveAll(); finishLeave(); })}>保存并离开</button></> : <button className="prd-primary" onClick={finishLeave}>继续关闭</button>}</footer></section></div>}
    {showImport && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="导入 PRD 文档"><h2>导入 PRD 文档</h2><p>回传系统导出的 DOCX 会把修改写回原 Block 并生成历史版本。Markdown、TXT 或文字型 PDF 仍可用于建立新大纲。</p><select aria-label="选择模板" value={template} onChange={e => setTemplate(e.target.value)}><option value="alibaba">阿里 PRD 模板</option><option value="tencent">鹅厂 PRD 模板</option><option value="custom">自定义大纲</option></select><input aria-label="上传 PRD 文件" type="file" accept=".docx,.md,.txt,.pdf" onChange={e => { if (e.target.files?.[0]) void attempt(() => readFile(e.target.files![0])); }} />{template === 'custom' && <textarea aria-label="大纲内容" rows={14} value={outline} onChange={e => setOutline(e.target.value)} placeholder={'# 产品背景\n# 功能设计\n## 具体功能'} />}<footer><button onClick={() => setShowImport(false)}>关闭</button><button className="prd-primary" disabled={busy} onClick={() => void attempt(importOutline)}>导入大纲并规划</button></footer></section></div>}
    {settings && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="文档背景与规则"><h2>文档背景与全局规则</h2><label className="prd-field">项目背景<textarea rows={10} value={settings.background} onChange={e => setSettings({ ...settings, background: e.target.value })} /></label><label className="prd-field">全局规则<textarea rows={5} value={settings.global_rules} onChange={e => setSettings({ ...settings, global_rules: e.target.value })} /></label><p>更新后已有正文标记为待更新。</p><footer><button onClick={() => setSettings(null)}>关闭</button><button onClick={() => void attempt(async () => { ingest(await mutate(data!.document.id, '/context', { ...settings, expected_revision: dataRef.current!.document.revision }, 'PUT')); setSettings(null); })}>保存设置</button></footer></section></div>}
    {(diffRun || history) && <div className="prd-modal-backdrop"><section className="prd-modal prd-modal-wide" role="dialog" aria-modal="true" aria-label={history ? 'Block 历史版本' : 'AI 结果 Diff'}><header><h2>{history ? '历史版本' : '当前内容 → AI 候选结果'}</h2><button onClick={() => { setDiffRun(null); setHistory(null); }}>关闭</button></header>{diffRun && <BlockDiff before={drafts[diffRun.block_id!]?.content ?? ''} after={diffRun.result?.content ?? ''} />}{history && <BlockHistory versions={history.versions} content={drafts[history.blockId]?.content ?? ''} onRestore={v => void attempt(async () => { requireSaved('恢复历史版本', [history.blockId]); ingest(await mutate(data!.document.id, `/blocks/${history.blockId}/restore`, { version: v, expected_version: draftRef.current[history.blockId].version })); setHistory(null); })} />}</section></div>}
  </main>;
});

function PrdMindMapPane({ blocks, selected, onSelect, onClose }: {
  blocks: Block[]; selected: string; onSelect: (id: string) => void; onClose: () => void;
}) {
  return <section className="prd-mindmap-workspace" role="region" aria-label="PRD 思维导图与原文联动">
    <header><div><h2>PRD 思维导图</h2><small>节点与右侧原文实时对应</small></div><button aria-label="收起思维导图" title="收起思维导图" onClick={onClose}><X size={17} /></button></header>
    <div className="prd-mindmap-canvas"><BlockPlanMap blocks={blocks} selected={selected} onSelect={onSelect} sourceLinked /></div>
    <footer>点击任意节点的“定位原文”，右侧会滚动到对应 Block，并用绿色虚线框高亮。</footer>
  </section>;
}

function fileBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
