import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, FileText, Network, Download, Settings2, ImagePlus, Upload, Maximize2, X, Eye, Pencil, Info, MessageSquareText, ClipboardCheck, History, Presentation, Loader2, RefreshCw } from 'lucide-react';
import { apiClient } from '../client';
import { appConfig } from '../config';
import { currentActorId } from '../../auth/session';
import { generatePrdPresentation, prdPresentationPreviewUrl } from '../sessions';
import type { PrdPresentationAudience } from '../sessions';
import { documentPath, draftPresentationPreviewUrl, findPreviousVersion, generateDraftPresentation, getDraftPresentation, getDocument, getDocumentVersion, getDocumentVersions, mutate, openDocument, structureSource } from './api';
import type { DocumentVersion, DocumentVersionDetail, VersionBaseline } from './api';
import { fromBlock, mergeDrafts, statusNames } from './types';
import type { AgentRun, Block, Comment, DocumentReviewIssue, Draft, Snapshot } from './types';
import { BlockPlanMap, BlockTree } from './BlockTree';
import { BlockProperties } from './BlockProperties';
import { BlockToolbar } from './BlockToolbar';
import { FullDocumentReport, FullDocumentView, fullCommentTargets, fullSnapshotChanged } from './FullDocumentView';
import { BlockCommentPanel } from './BlockCommentPanel';
import { GenerationStatus, runTitle, stageText, statusText } from './GenerationStatus';
import { BlockHistory, BlockDiff } from './BlockHistory';
import { VersionCompare } from './VersionCompare';
import { enqueueDocumentJob, listDocumentJobs, jobNames, submitPrdOperation } from './DocumentJobs';
import type { DocumentJob } from './DocumentJobs';
import { RecoveryNotice, useDraftRecovery } from './draftRecovery';
import { TableDiff } from './TableDiff';
import { DocumentAssetImage } from './DocumentAssetImage';
import { useDiscussion, discussionIssue } from './discussionContext';
import type { ObjectTarget } from './discussionContext';
import { ObjectTargetPicker } from './ObjectTargetPicker';
import type { WorkbenchTab } from './PrdWorkbench';
import { PrdWorkbench } from './PrdWorkbench';
import { editGroups } from './editSelection';
import { DrawioErDiagram } from '../DrawioErDiagram';
import type { PrdErDiagramDto } from '../dto';
import type { Version } from './BlockHistory';
import { UniverEditor } from './univer/UniverEditor';
import { locateReadingSelection, PrdMermaidPreview, RenderedPrdDocument } from './RenderedPrdDocument';
import { locateScreenshotInPrd, namedPrdTargets, prdEditScope, prdWorkbenchIntent, isWorkbenchCommand } from './instructionRouting';
import type { ScreenshotAnchor } from './instructionRouting';
import type { ReadingAnnotationAction, ReadingSelection } from './RenderedPrdDocument';
import { conciseProjectTitle } from '../projectTitle';
import type { BlockTextSelection, UniverEditorHandle } from './univer/types';
import './editor.css';

const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const versionTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} (北京时间)`;
};

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
  requestRevision: (instruction: string, screenshot?: ScreenshotAnchor) => Promise<string>;
  focusChapter: (id: string) => void;
  openTasks: () => void;
  openSavedAction: (id: string) => void;
  updateDiscussion: (action: 'pin' | 'clear' | 'constraints', value?: string) => void;
  chooseObject: () => void;
  /** Cancels every queued/running Agent run of the open document and returns
   *  how many cancel requests the backend accepted. Used by the workspace chat
   *  to honour an explicit "run this instruction now" without racing the job. */
  cancelActiveRuns: () => Promise<number>;
}

export interface PrdChatContext {
  title: string; version?: number | null; selectedId: string; selection: string;
  chapters: { id: string; title: string }[];
  discussion?: { label: string; pinned: boolean; constraints: string; issue: string; version: number };
}

export interface PrdEditReceipt {
  summary: string; version?: number | null; chapters: { id: string; title: string }[]; skipped: number;
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
  activitySteps?: string[];
  elapsedSeconds?: number;
}

interface PRDEditorProps {
  sessionId: string;
  embedded?: boolean;
  onClose?: () => void;
  onAgentProgress?: (progress: PrdAgentProgress | null) => void;
  onChatEditApplied?: (summary: string, receipt?: PrdEditReceipt) => void;
  onChatContext?: (context: PrdChatContext | null) => void;
}

export interface PendingChatEdit {
  summary: string;
  base_revision: number;
  steps: Record<string, unknown>[];
  coupled?: boolean;
  restore_version?: number;
  alternative_id?: string;
  job_id?: string;
  reuse_conflicts?: { step_index: number; block_id: string; reason: string; can_take: boolean }[];
  linkage_receipt?: { selected_ids: string[]; omitted_ids: string[]; artifact_note: string };
  impact_notes?: string[];
  bulk_matches?: { step_index: number; block_id: string; title: string; line: number; context: string }[];
  excluded_matches?: { block_id: string; title: string; line: number; context: string; reason: string }[];
  excluded_count?: number;
  image_previews?: { step_index: number; data_url: string; alt: string }[];
  preview: { block_id: string; title: string; before: string; after: string;
    metadata_changes?: { field: string; before: unknown; after: unknown }[];
    old_parent_id: string | null; parent_id: string | null; old_order: number; order: number;
    diagram_previews?: Array<PrdErDiagramDto & { added_entities: string[]; removed_entities: string[] }> }[];
}

function selectedBulkContent(before: string, blockId: string, edit: PendingChatEdit, selected: Set<number>): string {
  if (!edit.bulk_matches) return edit.preview.find(item => item.block_id === blockId)?.after ?? before;
  const characters = Array.from(before);
  for (const match of edit.bulk_matches) {
    if (match.block_id !== blockId || !selected.has(match.step_index)) continue;
    const step = edit.steps[match.step_index];
    const start = Number(step.match_start), end = Number(step.match_end);
    characters.splice(start, end - start, ...Array.from(String(step.new_text ?? '')));
  }
  return characters.join('');
}

function mermaidSources(content: string): string[] {
  return [...content.matchAll(/^```mermaid[^\n]*\n([\s\S]*?)^```[ \t]*$/gm)].map(match => match[1].trim());
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

function outlineFromBlocks(blocks: Block[]) {
  const byId = new Map(blocks.map(block => [block.id, block]));
  return blocks.map(block => {
    let depth = 1;
    let parentId = block.parent_id;
    const seen = new Set<string>();
    while (parentId && byId.has(parentId) && !seen.has(parentId)) {
      seen.add(parentId);
      depth += 1;
      parentId = byId.get(parentId)?.parent_id ?? null;
    }
    return `${'#'.repeat(Math.min(depth, 6))} ${block.title}`;
  }).join('\n');
}

export const PRDEditor = forwardRef<PRDEditorHandle, PRDEditorProps>(function PRDEditor({ sessionId, embedded = false, onClose, onAgentProgress, onChatEditApplied, onChatContext }, ref) {
  const [data, setData] = useState<Snapshot | null>(null);
  const dataRef = useRef<Snapshot | null>(null);
  const sessionRef = useRef(sessionId); sessionRef.current = sessionId;
  const discussion = useDiscussion(data);
  const discussionRef = useRef(discussion); discussionRef.current = discussion;
  const [objectPicker, setObjectPicker] = useState(false);
  const univerRef = useRef<UniverEditorHandle>(null);
  const pendingSourceSelectionRef = useRef<ReadingSelection | null>(null);
  const launchLocksRef = useRef(new Set<string>());
  const assetInputRef = useRef<HTMLInputElement>(null);
  const columnsRef = useRef<HTMLDivElement>(null);
  const directoryRef = useRef<HTMLDivElement>(null);
  const allowLeaveRef = useRef(false);
  const agentProgressCallbackRef = useRef(onAgentProgress);
  const chatEditAppliedRef = useRef(onChatEditApplied);
  const chatContextRef = useRef(onChatContext);
  const lastChatRef = useRef<{ instruction: string; ids: string[]; summary?: string } | null>(null);
  const pendingChatRef = useRef<PendingChatEdit | null>(null);
  const [selectionLabel, setSelectionLabel] = useState('');
  const [nextStageDialog, setNextStageDialog] = useState(false);
  const [stageReviewId, setStageReviewId] = useState<string | null>(null);
  const [lastReceipt, setLastReceipt] = useState<PrdEditReceipt | null>(null);
  const lastReportedActiveRunRef = useRef<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const draftRef = useRef<Record<string, Draft>>({});
  const [selected, setSelected] = useState('');
  const selectedRef = useRef('');
  /** 目录里「再点一次」只取消高亮：正文保持原位，也不回落到第一章。 */
  const [highlightCleared, setHighlightCleared] = useState(false);
  /** 显式导航计数：只有它变化时才把目标章节滚入视野，正文内点选不改变阅读位置。 */
  const [navSeq, setNavSeq] = useState(0);
  const [editorSelection, setEditorSelection] = useState<BlockTextSelection[]>([]);
  const editorSelectionRef = useRef<BlockTextSelection[]>([]);
  const readingSelectionRef = useRef<ReadingSelection | null>(null);
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
  const [history, setHistory] = useState<{ blockId: string; versions: Version[]; initialVersion?: number } | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [pptDialog, setPptDialog] = useState(false);
  const [pptBusy, setPptBusy] = useState(false);
  const [leaveRequest, setLeaveRequest] = useState<LeaveRequest | null>(null);
  const [outline, setOutline] = useState('');
  const [template, setTemplate] = useState('alibaba');
  const [firstGenerationTemplate, setFirstGenerationTemplate] = useState<'alibaba' | 'tencent' | 'none' | 'custom' | 'source'>('alibaba');
  const [firstGenerationOutline, setFirstGenerationOutline] = useState('');
  const [firstGenerationBusy, setFirstGenerationBusy] = useState(false);
  const [firstGenerationError, setFirstGenerationError] = useState('');
  const [firstGenerationDismissed, setFirstGenerationDismissed] = useState(false);
  const [sourceOrganizing, setSourceOrganizing] = useState(false);
  const [sourceOrganizeNotice, setSourceOrganizeNotice] = useState('');
  const sourceOrganizeBusy = useRef(false);
  const [settings, setSettings] = useState<{ background: string; global_rules: string } | null>(null);
  const [connected, setConnected] = useState(false);
  const connectedRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [fullSnapshot, setFullSnapshot] = useState<Snapshot | null>(null);
  const [resolutionRunId, setResolutionRunId] = useState<string | null>(null);
  const [mindMapOpen, setMindMapOpen] = useState(false);
  const [documentMode, setDocumentMode] = useState<'read' | 'edit'>('read');
  // 「对比上一版」：勾选后在图文阅读里高亮与上一版的差异，不勾选则完全按普通状态渲染。
  const [compareWithPrevious, setCompareWithPrevious] = useState(false);
  const [compareBaseline, setCompareBaseline] = useState<VersionBaseline | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const [focusedCommentId, setFocusedCommentId] = useState<string | null>(null);
  const [dockPanel, setDockPanel] = useState<'activity' | 'comments' | 'review' | 'versions' | null>(null);
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [documentJobs, setDocumentJobs] = useState<DocumentJob[]>([]);
  const [commandReceipt, setCommandReceipt] = useState<{ receipt_id?: string; summary: string; type: string; meeting?: { markdown: string; filename: string } } | null>(null);
  const focusedCommentRef = useRef<string | null>(null);

  const [workbenchTab, setWorkbenchTab] = useState<WorkbenchTab>('impact');
  const [workbenchSelection, setWorkbenchSelection] = useState<ReadingSelection | null>(null);
  const [documentVersions, setDocumentVersions] = useState<DocumentVersion[]>([]);
  const [versionPreview, setVersionPreview] = useState<DocumentVersionDetail | null>(null);
  const [versionCompareOpen, setVersionCompareOpen] = useState(false);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState('');
  const [commentFocusOnly, setCommentFocusOnly] = useState(false);
  const [commentAnchor, setCommentAnchor] = useState<{ x: number; y: number } | null>(null);
  const [reviewResolution, setReviewResolution] = useState<ReviewResolution | null>(null);
  const [reviewCommentBusy, setReviewCommentBusy] = useState(false);
  const [pendingChatEdit, setPendingChatEdit] = useState<PendingChatEdit | null>(null);
  const [bulkSelection, setBulkSelection] = useState(new Set<number>());
  const [applyingChatEdit, setApplyingChatEdit] = useState(false);
  const [chatEditError, setChatEditError] = useState('');
  const [chatConflict, setChatConflict] = useState<Snapshot | null>(null);
  const [reusedCandidate, setReusedCandidate] = useState<PendingChatEdit | null>(null);
  const [takeConflicts, setTakeConflicts] = useState(new Set<number>());
  const [selectionPreview, setSelectionPreview] = useState<{ edit: PendingChatEdit; selected: Set<number>; preview: PendingChatEdit['preview'] } | null>(null);
  const [launchLocks, setLaunchLocks] = useState(new Set<string>());
  selectedRef.current = selected;
  editorSelectionRef.current = editorSelection;
  focusedCommentRef.current = focusedCommentId;
  agentProgressCallbackRef.current = onAgentProgress;
  chatEditAppliedRef.current = onChatEditApplied;
  chatContextRef.current = onChatContext;
  if (pendingChatEdit) pendingChatRef.current = pendingChatEdit;

  // Leaving the embedded editor is a plain view switch, so a routine close must
  // not cost the user a confirmation step: when nothing is unsaved we hand the
  // view straight back. Only dirty drafts still get the save prompt, which is
  // the one case where a silent exit would actually lose typed content.
  useImperativeHandle(ref, () => ({
    chooseObject: () => setObjectPicker(true),
    cancelActiveRuns: async () => {
      const snapshot = dataRef.current;
      if (!snapshot) return 0;
      const active = snapshot.runs.filter(run => run.status === 'queued' || run.status === 'running');
      let cancelled = 0;
      for (const run of active) {
        try {
          await mutate(snapshot.document.id, `/runs/${run.id}/cancel`, {});
          cancelled += 1;
        } catch {
          // 单个任务取消失败不应阻断其余任务的取消。
        }
      }
      if (cancelled > 0) await refresh();
      return cancelled;
    },
    updateDiscussion: (action, value) => {
      const ctx = discussionRef.current;
      if (action === 'clear') { ctx.update(null); readingSelectionRef.current = null; setSelectionLabel(''); lastChatRef.current = null; return; }
      const block = dataRef.current?.blocks.find(b => b.id === selectedRef.current);
      const next = ctx.value ?? (block ? { blockId: block.id, version: block.version, quote: '', pinned: false, constraints: '' } : null);
      if (next) ctx.update(action === 'pin' ? { ...next, pinned: !next.pinned } : { ...next, constraints: (value ?? '').slice(0, 800) });
    },
    focusChapter: id => {
      readingSelectionRef.current = null; editorSelectionRef.current = [];
      setSelectionLabel(''); setEditorSelection([]); selectedRef.current = id; setSelected(id);
      const block = dataRef.current?.blocks.find(b => b.id === id);
      if (block) discussionRef.current.update({ blockId: id, version: block.version, quote: '', pinned: false, constraints: discussionRef.current.value?.constraints ?? '' });
    },
    openTasks: () => { setWorkbenchTab('tasks'); setWorkbenchOpen(true); },
    openSavedAction: (id) => {
      const current = dataRef.current; if (!current) return;
      void apiClient.request<NonNullable<typeof commandReceipt>>(documentPath(current.document.id) + `/workbench/receipts/${encodeURIComponent(id)}`)
        .then(result => setCommandReceipt(result)).catch(e => setError(message(e)));
    },
    requestClose: (onConfirm) => {
      if (Object.values(draftRef.current).some(draft => draft?.dirty)) {
        setLeaveRequest({ kind: 'callback', onConfirm });
        return;
      }
      allowLeaveRef.current = true;
      onConfirm();
    },
    requestRevision: async (instruction, screenshot) => {
      const snapshot = dataRef.current;
      if (!snapshot || snapshot.document.session_id !== sessionRef.current) throw new Error('PRD 正在加载，请稍后重试。');
      if (isWorkbenchCommand(instruction)) {
        if (/预览|写入|应用|修改/.test(instruction)) requireSaved('生成工作台修改预览');
        const result = await submitPrdOperation<{ receipt_id: string; type: string; summary: string; meeting?: { markdown: string; filename: string }; job?: DocumentJob }>(snapshot.document.id, '/workbench/command', {
          instruction, target_ids: selectedRef.current ? [selectedRef.current] : [], comment_id: focusedCommentRef.current,
          expected_revision: snapshot.document.revision,
        });
        setCommandReceipt(result);
        if (result.type === 'policies') await refresh();
        if (result.job) setDocumentJobs(old => [result.job!, ...old.filter(j => j.id !== result.job!.id)]);
        return `${result.summary}\n[prd-action:${result.receipt_id}]`;
      }
      const workbenchIntent = prdWorkbenchIntent(instruction);
      if (workbenchIntent) {
        setWorkbenchTab(workbenchIntent); setWorkbenchOpen(true); setDockPanel(null);
        return '已打开 PRD 优化工作台的对应面板，可继续选择范围、填写信息或生成预览。';
      }
      if (/撤销(?:刚才|上次|最近|上一条|这次)?.*(?:修改|改动|操作)|撤回上次修改/.test(instruction)) {
        requireSaved('撤销 Agent 修改');
        const planned = await mutate<PendingChatEdit>(snapshot.document.id, '/chat-edits/restore-plan', {});
        setChatEditError(''); setPendingChatEdit(planned);
        return `待确认：${planned.summary}，请在右侧预览后应用。`;
      }
      const choice = instruction.match(/(?:上一条|刚才|上个方案).*只(?:应用|保留|采纳)第([一二三四五六七八九十]|\d+)项/);
      if (choice && pendingChatRef.current) {
        const edit = pendingChatRef.current;
        const group = edit.bulk_matches ? edit.steps.map((_, index) => [index]) : editGroups(edit.steps, snapshot.blocks, edit.coupled);
        const number = Number(choice[1]) || '一二三四五六七八九十'.indexOf(choice[1]) + 1;
        if (!group[number - 1]) throw new Error('该修改项不存在，请查看右侧方案中的编号。');
        setPendingChatEdit(edit);
        setBulkSelection(new Set(group[number - 1]));
        return `待确认：已只勾选第 ${number} 项，请在右侧应用。`;
      }
      const savedContext = discussionRef.current.value;
      const explicitlyNamed = snapshot.blocks.some(b => b.title && instruction.includes(b.title));
      const useContext = savedContext && !explicitlyNamed && !screenshot;
      if (useContext) {
        const issue = discussionIssue(savedContext, snapshot.blocks); if (issue) throw new Error(issue);
        if (draftRef.current[savedContext.blockId]?.dirty) throw new Error('讨论对象所在章节有未保存草稿，请先保存，再重新选择对象。');
      }
      const objectTarget = useContext ? savedContext.object : undefined;
      const followup = !savedContext && /^(?:再|继续|沿用|保持刚才|按刚才)|上一条|上次修改/.test(instruction) && lastChatRef.current;
      let conversationContext = '';
      if (followup) {
        const last = lastChatRef.current!;
        const previousTitle = snapshot.blocks.find(b => b.id === last.ids[0])?.title;
        if (previousTitle && !snapshot.blocks.some(b => instruction.includes(b.title))) instruction = `在「${previousTitle}」中，${instruction}`;
        conversationContext = `上一次用户要求：${last.instruction.slice(0, 1500)}${last.summary ? `\n上一次处理摘要：${last.summary.slice(0, 600)}` : ''}`;
      }
      if (useContext) conversationContext = `用户约束：${savedContext.constraints}\n本轮明确讨论对象：${(objectTarget?.label ?? savedContext.blockId).slice(0, 250)}，来源章节版本 ${savedContext.version}。${savedContext.quote ? `\n只调整引用原文：${savedContext.quote.slice(0, 1200)}` : ''}`;
      const bulkRequest = /所有|全部|全文|统一|批量|每个/.test(instruction)
        && /替换|改为|改成|换成|统一改|统一换/.test(instruction);
      if (bulkRequest && !screenshot && !objectTarget) {
        if (Object.values(draftRef.current).some(draft => draft?.dirty)) {
          throw new Error('存在未保存的人工草稿，请先保存，再进行批量修改。');
        }
        const job = await enqueueDocumentJob(snapshot.document.id, snapshot.document.revision, { kind: 'bulk', instruction });
        setDocumentJobs(old => [job, ...old]);
        return `批量修改任务已提交。可继续阅读，稍后打开保存的预览。\n[prd-job:${job.id}]`;
      }
      const named = namedPrdTargets(snapshot.blocks, instruction);
      const screenshotLocation = screenshot ? locateScreenshotInPrd(snapshot.blocks, screenshot) : null;
      if (screenshotLocation && named.length && !named.some(block => block.id === screenshotLocation.blockId)) {
        throw new Error('指令中的章节与截图位置不一致，请确认后重新发送。');
      }
      const focusedRange = editorSelectionRef.current.length === 1
        ? editorSelectionRef.current[0] : readingSelectionRef.current;
      // 范围只来自「指令点名 / 目录勾选 / 正文选中」，都没有时默认整篇全文（由 Agent 到全文目录里定位）。
      // ⚠️ 目录里点击高亮不在其中——它只是查看。
      const scope = screenshotLocation
        ? { targets: snapshot.blocks.filter(block => block.id === screenshotLocation.blockId), source: 'focused' as const }
        : useContext ? { targets: snapshot.blocks.filter(block => block.id === savedContext.blockId), source: 'focused' as const }
        : prdEditScope(snapshot.blocks, instruction, checked, focusedRange?.blockId);
      const targets = scope.targets;
      if (!targets.length && scope.source !== 'document') throw new Error('请先在右侧选择要修改的 PRD 章节。');
      if (/(全文|整个\s*PRD|整份\s*PRD|所有章节|全部章节)/i.test(instruction) && !named.length) {
        throw new Error('整篇改写请逐章说明要求，或在文档目录中勾选要修改的章节。');
      }
      const visualRequest = /mermaid|draw\.?io|er\s*图|实体关系|架构图|流程图|状态图|时序图|关系图|数据流图|配图|图示|这张图|重画|画图|画一张|画个|图片|插画|插图/i.test(instruction)
        && !(/图注|配图说明|图片说明/.test(instruction) && !/修改图本身|替换|重画|生成|制作|画一张|画个/.test(instruction));
      const architectureRequest = /逻辑架构|系统架构|分层架构|逻辑层次|模块依赖|数据流|关联章节|相关章节|图文同步/i.test(instruction);
      const structured = !!useContext || scope.source === 'document' || targets.length > 1 || visualRequest || architectureRequest || /表格|表头|单元格|第[一二三四五六七八九十\d]+列|增加列|删除列|列排序|行排序|排序|重排|移动|移到|挪到|放到|排到|调换|交换|升为|降为|子节|上移|下移|前移|后移|提前/i.test(instruction);
      if (structured) {
        if (Object.values(draftRef.current).some(draft => draft?.dirty)) {
          throw new Error('存在未保存的人工草稿，请先点击“保存”，再执行表格或目录修改。');
        }
        // 只有「正文里选中了文字」才算焦点；目录里点击的那一章不再混进目标里。
        const focusId = focusedRange?.blockId ?? null;
        const targetIds = [...new Set([
          ...targets.map(block => block.id),
          ...named.map(block => block.id),
          ...(focusId && named.length < 2 && !screenshotLocation && !useContext ? [focusId] : []),
        ])];
        const documentScope = scope.source === 'document' && !targetIds.length;
        const focusedBlock = snapshot.blocks.find(block => block.id === focusId);
        const selectedText = readingSelectionRef.current?.quote
          ?? (focusedBlock && focusedRange ? focusedBlock.content.slice(focusedRange.startOffset, focusedRange.endOffset) : '');
        const job = await enqueueDocumentJob(snapshot.document.id, snapshot.document.revision, { kind: 'chat', instruction,
          target_ids: targetIds, scope: documentScope ? 'document' : 'chapters',
          selected_text: (useContext ? savedContext.quote : selectedText).slice(0, 2000), screenshot_anchor: screenshotLocation?.anchorText ?? '', conversation_context: conversationContext, object_target: objectTarget });
        setDocumentJobs(old => [job, ...old]);
        lastChatRef.current = { instruction: instruction.slice(0, 1500), ids: targets.map(item => item.id) };
        if (targets[0] && (!savedContext || explicitlyNamed) && dataRef.current?.document.id === snapshot.document.id) discussionRef.current.update({ blockId: targets[0].id, version: targets[0].version, quote: selectedText.slice(0, 2000), pinned: false, constraints: savedContext?.constraints ?? '' });
        if (targets[0]) setSelected(targets[0].id);
        return documentScope
          ? `未指定章节：已按整篇全文提交。Agent 会先在文档目录中定位要修改的章节，再生成方案；如需限定范围，请先在文档目录中勾选章节。\n[prd-job:${job.id}]`
          : `修改任务已提交，可继续阅读。完成后打开预览并选择应用。\n[prd-job:${job.id}]`;
      }
      for (const target of targets) {
        if (draftRef.current[target.id]?.dirty || saves.current.has(target.id)) {
          throw new Error(`「${target.title}」有未保存内容，请先在右侧保存，再发送修改指令。`);
        }
        if (launchLocksRef.current.has(`block:${target.id}`)) {
          throw new Error(`「${target.title}」已有修改任务，请等待完成。`);
        }
      }
      const accepted: string[] = [];
      for (const target of targets) {
        try {
          await launchOnce(`block:${target.id}`, async () => {
            const current = await getDocument(snapshot.document.id);
            ingest(current);
            const latest = current.blocks.find(block => block.id === target.id);
            if (!latest) throw new Error('目标章节已不存在，请刷新 PRD 后重试。');
            if (current.runs.some(run => run.block_id === target.id && (run.status === 'queued' || run.status === 'running'))) {
              throw new Error(`「${target.title}」已有运行中的任务，请等待完成。`);
            }
            if (draftRef.current[target.id]?.dirty || latest.version !== target.version) {
              throw new Error(`「${target.title}」已发生变化，请确认最新内容后重试。`);
            }
            await mutate(current.document.id, `/blocks/${target.id}/runs`, {
              type: latest.content.trim() ? 'revise' : 'generate',
              request_id: crypto.randomUUID(),
              feedback: screenshotLocation
                ? `${instruction}\n截图已与当前 PRD 的原文精确匹配。只修改包含「${screenshotLocation.anchorText}」的段落，保留相邻无关内容。${screenshot?.visual_context ? `截图中可见：${screenshot.visual_context}` : ''}`
                : instruction + (conversationContext ? `\n对话参考（以本次要求和当前正文为准）：\n${conversationContext}` : ''),
              ...(screenshotLocation ? {
                start_line: screenshotLocation.startLine,
                end_line: screenshotLocation.endLine,
              } : {}),
              ...(!screenshotLocation && targets.length === 1 && !named.length && focusedRange?.blockId === target.id && latest.content.trim() ? {
                start_line: focusedRange.startLine,
                end_line: focusedRange.endLine,
              } : {}),
            });
            accepted.push(target.title);
          });
        } catch (error) {
          if (accepted.length) throw new Error(`已提交「${accepted.join('、')}」的修改；其余章节未提交：${message(error)}`);
          throw error;
        }
      }
      editorSelectionRef.current = [];
      readingSelectionRef.current = null;
      setEditorSelection([]);
      setSelected(targets[0].id);
      await refresh();
      lastChatRef.current = { instruction: instruction.slice(0, 1500), ids: targets.map(item => item.id) };
      if ((!savedContext || explicitlyNamed) && dataRef.current?.document.id === snapshot.document.id) discussionRef.current.update({ blockId: targets[0].id, version: targets[0].version, quote: '', pinned: false, constraints: savedContext?.constraints ?? '' });
      return accepted.join('、');
    },
  }), []);

  const updateDrafts = useCallback((next: Record<string, Draft>) => { draftRef.current = next; setDrafts(next); }, []);
  const ingest = useCallback((next: Snapshot) => {
    if (next.document.session_id !== sessionRef.current) return;
    if (dataRef.current && next.document.revision < dataRef.current.document.revision) return;
    dataRef.current = next;
    setData(next);
    updateDrafts(mergeDrafts(draftRef.current, next.blocks));
    setSelected(id => next.blocks.some(b => b.id === id) ? id : next.blocks[0]?.id ?? '');
  }, [updateDrafts]);
  const recoveryValue = useMemo(() => {
    if (!data) return null;
    const dirty = Object.fromEntries(Object.entries(drafts).filter(([id, draft]) => draft.dirty && data.blocks.some(b => b.id === id)));
    return Object.keys(dirty).length ? { revision: data.document.revision, drafts: dirty } : null;
  }, [drafts, data]);
  const bodyRecovery = useDraftRecovery(data ? `body:${data.document.id}` : null, recoveryValue);
  useEffect(() => {
    const id = data?.document.id; if (!id) return;
    let live = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      // 只接受数组：该端点一旦返回别的形状（错误体、代理页…），
      // 下面 `documentJobs.some(...)` 会把整个 PRD 编辑器渲染崩掉。
      // 形状不对就保留上一次已知的任务状态，而不是用一个坏值覆盖它。
      try { const jobs = await listDocumentJobs(id!); if (live && Array.isArray(jobs)) setDocumentJobs(jobs); } catch { /* A failed poll preserves the last known task state. */ }
      finally { if (live) timer = setTimeout(poll, 3000); }
    }
    void poll(); return () => { live = false; clearTimeout(timer); };
  }, [data?.document.id]);
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
    dataRef.current = null; setData(null); draftRef.current = {}; setDrafts({});
    selectedRef.current = ''; setSelected(''); setHighlightCleared(false); setNavSeq(0); setObjectPicker(false);
    setFirstGenerationTemplate('alibaba'); setFirstGenerationOutline(''); setFirstGenerationError(''); setFirstGenerationDismissed(false);
    openDocument(sessionId, false, true).then(async value => {
      if (!live) return;
      if (value.document.template === 'tencent' || value.document.template === 'none' || value.document.template === 'custom') {
        setFirstGenerationTemplate(value.document.template);
        if (value.document.template === 'custom') setFirstGenerationOutline(outlineFromBlocks(value.blocks));
      } else if (value.document.source_prd) {
        // 项目是由导入的现有 PRD 创建的：默认直接采用该文件作为初版正文。
        setFirstGenerationTemplate('source');
      }
      ingest(value);
      if (!value.runs.some(run => run.type === 'initial')
        && value.runs.length > 0
        && value.runs.every(run => run.type === 'plan' && run.status === 'completed' && run.apply_status === 'applied')
        && value.blocks.every(block => !block.content.trim())) {
        const resumed = await openDocument(sessionId, true);
        if (live) ingest(resumed);
      }
    }).catch(e => { if (live) setError(message(e)); });
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
  useEffect(() => {
    chatContextRef.current?.(data ? {
      title: data.document.title, version: data.document.latest_version, selectedId: selected,
      selection: selectionLabel || (editorSelection.length ? `已选 ${editorSelection.length} 段源码` : ''),
      chapters: data.blocks.map(item => ({ id: item.id, title: item.title })),
      discussion: discussion.value ? { label: discussion.value.object?.label || `${data.blocks.find(b => b.id === discussion.value!.blockId)?.title ?? '已删除章节'}${discussion.value.quote ? ` · ${discussion.value.quote.slice(0, 100)}` : ''}`, pinned: discussion.value.pinned, constraints: discussion.value.constraints, issue: discussion.issue || discussion.error, version: discussion.value.version } : undefined,
    } : null);
  }, [data?.document.title, data?.document.latest_version, data?.blocks, selected, selectionLabel, editorSelection, discussion.value, discussion.issue, discussion.error]);
  useEffect(() => () => chatContextRef.current?.(null), []);
  useEffect(() => { lastChatRef.current = null; pendingChatRef.current = null; setPendingChatEdit(null); setLastReceipt(null); setStageReviewId(null); setNextStageDialog(false); setWorkbenchOpen(false); setWorkbenchSelection(null); setDocumentJobs([]); setCommandReceipt(null); }, [sessionId]);
  useEffect(() => { setChatConflict(null); }, [pendingChatEdit]);
  useEffect(() => { setReusedCandidate(null); setTakeConflicts(new Set()); }, [pendingChatEdit]);
  useEffect(() => {
    if (!pendingChatEdit || pendingChatEdit.restore_version || pendingChatEdit.bulk_matches || bulkSelection.size === pendingChatEdit.steps.length || !bulkSelection.size) return;
    let active = true;
    const timer = setTimeout(() => {
      if (!dataRef.current) return;
      void mutate<{ preview: PendingChatEdit['preview'] }>(dataRef.current.document.id, '/chat-edits/selection-preview', {
        expected_revision: pendingChatEdit.base_revision, steps: pendingChatEdit.steps.filter((_, i) => bulkSelection.has(i)),
      }).then(result => { if (active) { setSelectionPreview({ edit: pendingChatEdit, selected: bulkSelection, preview: result.preview }); setChatEditError(''); } })
        .catch(reason => { if (active) setChatEditError(message(reason)); });
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [pendingChatEdit, bulkSelection]);
  useLayoutEffect(() => {
    const columns = columnsRef.current, directory = directoryRef.current;
    const host = columns?.querySelector<HTMLElement>('.prd-univer-host, .prd-rendered-document');
    if (!columns || !directory || !host) return;
    const align = () => {
      if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 700px)').matches) {
        directory.style.height = '';
        return;
      }
      const dirRect = directory.getBoundingClientRect();
      const hostRect = host.getBoundingClientRect();
      if (hostRect.height > 0) {
        const targetBottom = hostRect.bottom;
        const targetHeight = Math.max(400, targetBottom - dirRect.top);
        directory.style.height = `${targetHeight}px`;
      }
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
    if (dockPanel !== 'versions' || !data?.document.id) return;
    let current = true;
    setVersionsLoading(true);
    setVersionsError('');
    void getDocumentVersions(data.document.id).then(versions => {
      if (current) setDocumentVersions(versions);
    }).catch(reason => {
      if (current) setVersionsError(message(reason));
    }).finally(() => {
      if (current) setVersionsLoading(false);
    });
    return () => { current = false; };
  }, [dockPanel, data?.document.id, data?.document.revision]);
  useEffect(() => {
    if (!resolutionRunId || !data) return;
    const run = data.runs.find(item => item.id === resolutionRunId);
    if (!run || run.status === 'queued' || run.status === 'running') return;
    setResolutionRunId(null);
    if (run.status === 'completed' && run.apply_status === 'applied') {
      setFullSnapshot(data);
      setNotice('AI 已生成修订后的新版本；旧意见保留待复核，未自动启动审核。');
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
    if (run.type === 'revise' || run.type === 'generate') {
      const current = dataRef.current;
      const receipt: PrdEditReceipt = { summary: run.result?.summary || '已完成章节修改', version: current?.document.latest_version,
        chapters: [{ id: block.id, title: block.title }], skipped: 0 };
      setLastReceipt(receipt); chatEditAppliedRef.current?.(receipt.summary, receipt);
    }
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
  function applySelection(id: string) {
    readingSelectionRef.current = null;
    setSelectionLabel('');
    setSelected(id); setEditorSelection([]); setStart(''); setEnd(''); setFeedback(''); setHistory(null); setDiffRun(null); setFocusedCommentId(null);
  }
  function select(id: string) {
    // 只切换「在看哪一章」。
    // ⚠️ 不要在这里写 discussion（讨论对象）——讨论对象会被下一条命令当成执行范围，
    // 于是「点目录浏览一下」就会悄悄改掉命令的范围。范围只来自明确动作：指令点名章节、
    // 勾选章节、在正文里选中文字，或在上方「选择 Agent 修改目标」里主动选择。
    // 显式导航（点目录、导图、批注、进度）恢复高亮，并让正文滚到目标章节。
    setHighlightCleared(false);
    setNavSeq(seq => seq + 1);
    if (selectedRef.current === id) { univerRef.current?.focusBlock(id); return; }
    applySelection(id);
  }
  function selectPassive(id: string) {
    // 在正文里点选章节、划选文字只让目录跟着高亮，保持读者当前的浏览位置。
    if (selectedRef.current === id) return;
    setHighlightCleared(false);
    applySelection(id);
  }
  function selectFromDirectory(id: string) {
    // 目录里再点一次已选中的条目只取消高亮：不滚动正文，也不回落到第一章。
    if (!id) { setHighlightCleared(true); return; }
    select(id);
  }
  function followVisibleBlock(id: string) {
    if (selectedRef.current === id) return;
    setHighlightCleared(false);
    readingSelectionRef.current = null;
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
  async function reviewFull(prepareSubmission = true, scope: 'full' | 'affected' = 'full') {
    await launchOnce('document', async () => {
      setBusy(true);
      try {
        requireSaved('审核全文');
        const current = await getDocument(dataRef.current!.document.id);
        ingest(current); setFullSnapshot(current);
        const review = await mutate<AgentRun>(current.document.id, '/review', { request_id: crypto.randomUUID(), expected_revision: current.document.revision, prepare_submission: prepareSubmission, scope });
        if (prepareSubmission) setStageReviewId(review.id);
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
  /**
   * 勾选「对比上一版」。上一版由 `findPreviousVersion` 定位 ——
   * 最新快照常常**就等于**当前内容，所以不能直接取 max(version)。
   */
  async function toggleCompareWithPrevious(next: boolean) {
    setCompareWithPrevious(next);
    setCompareBaseline(null);
    if (!next) return;
    const documentId = dataRef.current?.document.id;
    if (!documentId) return;
    setCompareBusy(true);
    try {
      const baseline = await findPreviousVersion(documentId, dataRef.current!.blocks);
      if (baseline) setCompareBaseline(baseline);
      else { setNotice('没有可对比的上一版'); setCompareWithPrevious(false); }
    } catch (error) {
      setNotice(error instanceof Error ? `读取上一版失败：${error.message}` : '读取上一版失败');
      setCompareWithPrevious(false);
    } finally { setCompareBusy(false); }
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
    setCommentFocusOnly(true);
    setCommentAnchor(null);
    setDockPanel('comments');
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
  function openReadingComment(commentId: string, blockId: string, anchor?: { x: number; y: number }) {
    select(blockId);
    setFocusedCommentId(commentId);
    setCommentFocusOnly(true);
    setCommentAnchor(anchor ?? null);
    setDockPanel('comments');
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
  async function readFirstGenerationTemplate(file: File) {
    if (!dataRef.current) throw new Error('PRD 正在加载，请稍后重试。');
    if (file.size > 8 * 1024 * 1024) throw new Error('模板文件不能超过 8 MB');
    const current = dataRef.current;
    const result = await mutate<{ outline: string }>(current.document.id, '/import', {
      filename: file.name,
      content_base64: await fileBase64(file),
      expected_revision: current.document.revision,
    });
    setFirstGenerationTemplate('custom');
    setFirstGenerationOutline(result.outline);
    setFirstGenerationError('');
  }
  async function startFirstGeneration() {
    const current = dataRef.current;
    if (!current) return;
    const selectedTemplate = firstGenerationTemplate;
    const selectedOutline = selectedTemplate === 'none'
      ? '# 产品需求文档'
      : selectedTemplate === 'custom' ? firstGenerationOutline : '';
    if (selectedTemplate === 'custom' && !selectedOutline.trim()) {
      setFirstGenerationError('请上传模板文件或输入模板目录。');
      return;
    }
    if (selectedTemplate === 'source') {
      // 先把原文按段落整体导入（不建模板大纲），文档立刻可用；若文件本身没有
      // 标题层级，再让 Agent 只决定"哪些整段是章节标题、是什么层级"，正文不改。
      setFirstGenerationBusy(true);
      setFirstGenerationError('');
      try {
        const imported = await mutate<Snapshot>(current.document.id, '/import-source', {
          expected_revision: current.document.revision,
        });
        ingest(imported);
        if (!imported.document.source_prd?.hierarchy?.clear) {
          void organizeImportedSource(imported.document.revision);
        }
      } catch (e) {
        setFirstGenerationError(message(e));
      } finally {
        setFirstGenerationBusy(false);
      }
      return;
    }
    setFirstGenerationBusy(true);
    setFirstGenerationError('');
    try {
      const prepared = await mutate<Snapshot>(current.document.id, '/outline', {
        template: selectedTemplate,
        outline: selectedOutline,
        expected_revision: current.document.revision,
      }, 'PUT');
      ingest(prepared);
      ingest(await openDocument(sessionId, true));
    } catch (e) {
      setFirstGenerationError(message(e));
    } finally {
      setFirstGenerationBusy(false);
    }
  }
  async function organizeImportedSource(expectedRevision?: number) {
    const current = dataRef.current;
    if (!current || sourceOrganizeBusy.current) return;
    sourceOrganizeBusy.current = true;
    setSourceOrganizing(true);
    setSourceOrganizeNotice('');
    try {
      const next = await structureSource(current.document.id, expectedRevision ?? current.document.revision);
      ingest(next);
      setSourceOrganizeNotice(next.organized
        ? `Agent 已按原文重排分层：${next.organized.summary}`
        : 'Agent 已完成分层整理。');
    } catch (reason) {
      setSourceOrganizeNotice(`分层整理未完成，已保留按段落整体导入的原文。${message(reason)}`);
    } finally {
      sourceOrganizeBusy.current = false;
      setSourceOrganizing(false);
    }
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
    setNextStageDialog(false);
  }

  async function previewRestore(version?: number) {
    requireSaved('预览历史恢复');
    const planned = await mutate<PendingChatEdit>(dataRef.current!.document.id, '/chat-edits/restore-plan', { version });
    setChatEditError(''); setPendingChatEdit(planned); setDockPanel(null);
  }

  async function createPresentation(audience: PrdPresentationAudience) {
    const snapshot = dataRef.current;
    if (!snapshot) return;
    const submitted = snapshot.document.submitted_spec_id;
    const frozen = Boolean(submitted) && snapshot.document.submitted_revision === snapshot.document.revision;
    setPptBusy(true);
    setError('');
    try {
      if (frozen) {
        await generatePrdPresentation(sessionId, currentActorId() || 'owner-1', audience, submitted);
        window.open(prdPresentationPreviewUrl(sessionId, audience), '_blank', 'noopener,noreferrer');
        setNotice(audience === 'team' ? '团队讲解 PPT 已生成，已在新标签页打开在线预览页。' : '对外介绍 PPT 已生成，已在新标签页打开在线预览页，请在分享前审阅。');
      } else {
        // 未定稿：基于当前 PRD 草稿生成，走与正式版一致的编排流程；生成后打开在线预览页。
        const existing = await getDraftPresentation(snapshot.document.id, audience);
        if (existing.status !== 'ready') await generateDraftPresentation(snapshot.document.id, audience);
        window.open(draftPresentationPreviewUrl(snapshot.document.id, audience), '_blank', 'noopener,noreferrer');
        setNotice(audience === 'team'
          ? '已基于当前 PRD 草稿生成团队讲解 PPT（草稿版），已在新标签页打开在线预览页。'
          : '已基于当前 PRD 草稿生成对外介绍 PPT（草稿版），已在新标签页打开在线预览页，请在分享前审阅。');
      }
      setPptDialog(false);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setPptBusy(false);
    }
  }
  const block = data?.blocks.find(b => b.id === selected), draft = drafts[selected];
  const unsavedCount = Object.values(drafts).filter(item => item.dirty).length;
  const initialRun = data?.runs.find(r => r.type === 'initial');
  const initialActive = !!initialRun && ['queued', 'running'].includes(initialRun.status);
  const initialGenerationPending = Boolean(data && !initialRun
    && !data.document.submitted_spec_id
    && data.document.template !== 'existing'
    && data.blocks.every(block => !block.content.trim())
    && data.runs.length === 0);
  // "直接使用原始文件" 只在项目是从现有 PRD 导入而来时才有意义。
  const firstTemplateOptions: Array<[typeof firstGenerationTemplate, string, string]> = [
    ...(data?.document.source_prd ? [[
      'source',
      '直接使用原始文件',
      `把「${data.document.source_prd.filename}」的原文直接作为初版正文，不重新生成`,
    ] as [typeof firstGenerationTemplate, string, string]] : []),
    ['alibaba', '阿里 PRD 模板', '使用现有的阿里章节目录'],
    ['tencent', '鹅厂 PRD 模板', '使用现有的鹅厂章节目录'],
    ['none', '不使用模板', '只创建一篇完整正文，由 Agent 自行组织内容'],
    ['custom', '上传自定义模板', '从文件提取目录，确认后生成'],
  ];
  const wholeReviewActive = data?.runs.some(r => r.type === 'document_review' && ['queued', 'running'].includes(r.status)) ?? false;
  const wholeRevisionActive = data?.runs.some(r => r.type === 'document_revision' && ['queued', 'running'].includes(r.status)) ?? false;
  const active = initialActive || wholeReviewActive || wholeRevisionActive || launchLocks.has(`block:${selected}`)
    || (data?.runs.some(r => r.block_id === selected && ['queued', 'running'].includes(r.status)) ?? false);
  const anyActive = launchLocks.size > 0 || (data?.runs.some(r => ['queued', 'running'].includes(r.status)) ?? false);
  const latestWholeReview = data?.runs.filter(r => r.type === 'document_review' && r.review_scope !== 'affected').at(-1);
  const wholeReviewClear = latestWholeReview?.status === 'completed'
    && latestWholeReview.apply_status === 'applied'
    && !latestWholeReview.stale
    && Array.isArray(latestWholeReview.result?.issues)
    && latestWholeReview.result.issues.every(issue => issue.severity === 'info');
  const wholeReport = latestWholeReview?.status === 'completed' && latestWholeReview.apply_status !== 'resolved' ? latestWholeReview : undefined;
  const wholeReviewError = latestWholeReview?.status === 'failed' ? latestWholeReview.error || 'AI 审核失败，请重试。' : undefined;
  const structureReady = Boolean(data && data.blocks.every(b => !b.content.trim() || (b.fragment_version === b.version && b.status !== 'outdated')));
  const stageReview = data?.runs.find(item => item.id === stageReviewId);
  const chatGroups = pendingChatEdit && !pendingChatEdit.bulk_matches
    ? editGroups(pendingChatEdit.steps, data?.blocks ?? [], pendingChatEdit.coupled) : [];
  const needsSelectionPreview = !!pendingChatEdit && !pendingChatEdit.restore_version && !pendingChatEdit.bulk_matches && bulkSelection.size !== pendingChatEdit.steps.length;
  const selectedPreviewReady = !needsSelectionPreview || !bulkSelection.size || (selectionPreview?.edit === pendingChatEdit && selectionPreview.selected === bulkSelection);
  const displayEdit = pendingChatEdit && needsSelectionPreview ? { ...pendingChatEdit, preview: bulkSelection.size && selectedPreviewReady ? selectionPreview!.preview : [] } : pendingChatEdit;
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
    : typeof progressRun?.result?.stream_sentence_count === 'number' && progressRun.result.stream_sentence_count > 0
        ? `已接收 ${progressRun.result.stream_sentence_count} 个完整句子，正在继续生成下一批内容…`
        : '';
  const progressKey = progressRun ? JSON.stringify([
    progressRun.id, progressRun.status, progressRun.result?.progress_stage,
    progressRun.result?.completed, progressRun.result?.total, progressBlockId,
    progressRun.result?.stream_sentence_count, progressActivityText,
    progressRun.result?.activity_message, progressRun.result?.activity_steps, progressRun.result?.elapsed_seconds,
  ]) : 'none';
  useEffect(() => {
    const callback = agentProgressCallbackRef.current;
    if (!callback) return;
    const job = documentJobs.find(j => ['queued', 'running'].includes(j.status)) ?? documentJobs[0];
    if (!activeProgressRun && job) {
      callback({ runId: job.id, status: job.status === 'ready' || job.status === 'applied' ? 'completed' : job.status,
        title: '文档候选任务', stage: jobNames[job.status], detail: job.title,
        activityText: job.events.at(-1)?.message, activitySteps: job.events.slice(-5).map(e => e.message), elapsedSeconds: job.events.at(-1)?.seconds });
      return;
    }
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
      activitySteps: progressRun.result?.activity_steps,
      elapsedSeconds: progressRun.result?.elapsed_seconds,
    });
    if (!activeProgressRun) lastReportedActiveRunRef.current = null;
  }, [progressKey, documentJobs]);
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
      <div className="prd-header-actions"><span className="prd-live">{connected ? '● 实时同步' : '定时同步'}</span>{canPlan && <button disabled={anyActive || data!.document.plan_status === 'generating'} onClick={() => void attempt(planDocument)}><Network size={15} />{data!.document.plan_status === 'generating' || launchLocks.has('plan') ? '正在规划…' : 'AI 规划'}</button>}<button onClick={() => setShowImport(true)} disabled={!data || initialActive}><Upload size={15} />导入</button><input ref={assetInputRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.svg,image/png,image/jpeg,image/webp,image/svg+xml" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void attempt(() => uploadAsset(file)); }} /><button disabled={!data || !selected} onClick={() => assetInputRef.current?.click()}><ImagePlus size={15} />插入图片</button><button aria-label="文档设置" disabled={!data} onClick={() => setSettings({ background: data!.document.background, global_rules: data!.document.global_rules })}><Settings2 size={16} /></button><button disabled={!data || busy} onClick={() => void attempt(exportDocx)}><Download size={15} />导出 DOCX</button><button title="生成可编辑演示文稿；PRD 未定稿时可基于当前草稿生成（草稿版）" disabled={!data || pptBusy} onClick={() => setPptDialog(true)}><Presentation size={15} />生成 PPT</button><button className="prd-primary" title={wholeReviewClear ? '全文审核已通过，进入 PRD 人工确认阶段' : '进入下一阶段前确认并审核当前版本'} disabled={!data || busy || anyActive} onClick={() => { if (wholeReviewClear && structureReady && !unsavedCount) void attempt(submit); else { setError(''); setStageReviewId(null); setNextStageDialog(true); } }}>进入下一阶段</button></div>
    </header>
    <RecoveryNotice recovery={bodyRecovery} onRestore={value => {
      const known = dataRef.current?.blocks ?? [];
      if (!value?.drafts || typeof value.drafts !== 'object') {
        setError('恢复副本格式无法识别，已保留原副本，请下载后核对。'); return false;
      }
      if (Object.keys(value.drafts).some(id => !known.some(b => b.id === id))) {
        setError('恢复副本包含已删除或尚未加载的章节，原副本已保留。请先下载恢复副本，核对并复制需要保留的内容。'); return false;
      }
      const restored = Object.fromEntries(Object.entries(value.drafts).filter(([id]) => known.some(b => b.id === id)));
      updateDrafts({ ...draftRef.current, ...restored });
      setNotice('草稿已恢复到输入区，尚未保存。来源版本不同的章节会显示对比，请核对后保存。');
    }} />
    {documentJobs.some(j => ['queued', 'running', 'ready', 'failed'].includes(j.status)) && <div className="prd-edit-receipt"><span>候选任务：{documentJobs.filter(j => ['queued', 'running'].includes(j.status)).length} 项处理中 · {documentJobs.filter(j => j.status === 'ready').length} 项待预览</span><button onClick={() => { setWorkbenchTab('tasks'); setWorkbenchOpen(true); }}>查看任务与已保存预览</button></div>}
    {commandReceipt && <section className="prd-edit-receipt" aria-label="对话操作结果"><strong>对话操作结果</strong><p style={{ whiteSpace: 'pre-wrap' }}>{commandReceipt.summary}</p><button onClick={() => { setWorkbenchTab(commandReceipt.type === 'job' ? 'tasks' : commandReceipt.type === 'meeting' ? 'meeting' : commandReceipt.type === 'policies' ? 'policies' : 'questions'); setWorkbenchOpen(true); }}>打开对应面板</button><button onClick={() => setCommandReceipt(null)}>收起</button>
      {commandReceipt.meeting && <><button onClick={() => { const m = commandReceipt.meeting!; const url = URL.createObjectURL(new Blob([m.markdown], { type: 'text/markdown;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = m.filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>下载会议包</button><details><summary>预览会议包</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{commandReceipt.meeting.markdown}</pre></details></>}
    </section>}
    {error && <div className="prd-alert" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}
    {initialGenerationPending && firstGenerationDismissed && <div className="prd-notice">首版 PRD 尚未生成。<button onClick={() => setFirstGenerationDismissed(false)}>{data?.document.source_prd ? '选择首版正文来源' : '选择模板并生成'}</button></div>}
    {(() => {
      const source = data?.document.source_prd;
      if (!source) return null;
      if (sourceOrganizing) return <div className="prd-notice" role="status">Agent 正在按原文重排章节层级；文档现在已可阅读和编辑。</div>;
      if (sourceOrganizeNotice) return <div className="prd-notice" role="status">{sourceOrganizeNotice}<button onClick={() => setSourceOrganizeNotice('')}>关闭</button></div>;
      if (source.hierarchy && !source.hierarchy.clear) {
        return <div className="prd-notice" role="status">「{source.filename}」没有自带标题层级，当前按段落整体分块。<button onClick={() => void organizeImportedSource()}>让 Agent 优化分层</button></div>;
      }
      return null;
    })()}
    {!data ? <div className="prd-loading">{error ? <button onClick={() => void attempt(async () => ingest(await openDocument(sessionId)))}>重新加载</button> : '正在恢复文档…'}</div> : <>
      <div ref={columnsRef} className={`prd-columns is-full-document${mindMapOpen ? ' is-mindmap-open' : ''}`}><aside className="prd-left-rail">
        {mindMapOpen ? <PrdMindMapPane blocks={viewBlocks} selected={selected} onSelect={select} onClose={() => setMindMapOpen(false)} /> : <div ref={directoryRef} className="prd-directory">
          <div className="prd-section-heading">文档目录<small>{checked.size ? `已勾选 ${checked.size} 个章节＝命令范围` : '点击仅定位 · 勾选才算范围'}</small></div>
          <div className="prd-directory-actions" role="group" aria-label="全文操作">
            <button className="prd-directory-action prd-directory-action--primary" onClick={() => void attempt(reviewFull)} disabled={wholeReviewActive || busy || anyActive || !data?.blocks.some(b => b.content.trim())} title="基于当前全文内容运行 AI 审核">
              <ClipboardCheck size={14} />{wholeReviewActive ? '审核中…' : '审核全文'}
            </button>
            <button className="prd-directory-action" onClick={() => void attempt(refreshFull)} disabled={busy || anyActive} title="刷新全文快照以匹配最新编辑内容">
              <RefreshCw size={14} />刷新全文
            </button>
          </div>
          <button className="prd-mindmap-trigger" aria-label="展开 PRD 思维导图" aria-expanded={false} onClick={() => setMindMapOpen(true)}><Network size={15} /><span>展开思维导图</span><Maximize2 size={14} className="prd-mindmap-trigger-arrow" /></button>
          <div className="prd-directory-tree-scroll"><BlockTree blocks={viewBlocks} selected={highlightCleared ? '' : selected} checked={checked} onSelect={selectFromDirectory} onCheck={id => setChecked(old => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next; })} /></div>
        </div>}
      </aside>
        <section className="prd-main-editor"><FullDocumentView snapshot={data}
          changed={fullSnapshot ? fullSnapshotChanged(fullSnapshot, data) : false} active={wholeReviewActive} loading={busy || anyActive} report={wholeReport}
          resolving={wholeRevisionActive} reviewError={wholeReviewError}
          onSelect={id => select(id)}
          onRefresh={() => void attempt(refreshFull)} onReview={() => void attempt(reviewFull)} onResolve={() => void attempt(resolveFullReview)} deferReport />
          {block && draft && <>
          <section className="prd-current-block-tools" aria-label="当前 Block 工具"><div className="prd-save-state"><span className={`prd-dot is-${draft.dirty ? 'editing' : block.status}`} /><strong>当前 Block · {block.title}</strong><span><span>{saving.has(selected) ? '保存中…' : draft.error ? '保存失败，输入已保留' : draft.dirty ? '未保存' : '已保存'}</span> · {statusNames[block.status]} · v{block.version}</span></div>
          <div className="prd-document-mode" role="group" aria-label="文档显示模式"><button className={documentMode === 'read' ? 'is-selected' : ''} onClick={() => { editorSelectionRef.current = []; setEditorSelection([]); setDocumentMode('read'); }}><Eye size={15} />图文阅读</button><button className={documentMode === 'edit' ? 'is-selected' : ''} onClick={() => { readingSelectionRef.current = null; setDocumentMode('edit'); }}><Pencil size={15} />编辑源码</button></div>
          <BlockToolbar dirty={unsavedCount > 0} saving={saving.size > 0} active={active} hasContent={Boolean(draft.content)} comparing={compareWithPrevious} compareBusy={compareBusy} onToggleCompare={next => void toggleCompareWithPrevious(next)} onSave={() => void attempt(saveAll)} onRun={kind => void attempt(() => run(kind))} onHistory={() => void attempt(async () => setHistory({ blockId: selected, versions: await apiClient.request<Version[]>(documentPath(data.document.id) + `/blocks/${selected}/history`) }))} />
          <details className="prd-current-block-settings"><summary>Block 设置与图片</summary><BlockProperties block={block} draft={draft} blocks={data.blocks} onEdit={edit} />
          {(data.assets ?? []).some(asset => asset.block_id === block.id && (asset.metadata.source !== 'chat_generated' || block.content.includes(`asset://${asset.id}`))) && <section className="prd-assets" aria-label="当前 Block 图片"><strong>当前图片</strong><div>{(data.assets ?? []).filter(asset => asset.block_id === block.id && (asset.metadata.source !== 'chat_generated' || block.content.includes(`asset://${asset.id}`))).map(asset => <figure key={asset.id}><DocumentAssetImage documentId={data.document.id} assetId={asset.id} alt={asset.file_name} /><figcaption>{asset.file_name}</figcaption></figure>)}</div></section>}</details></section>
          {documentMode === 'read' ? <RenderedPrdDocument blocks={editorBlocks} comments={data.comments} selectedBlockId={highlightCleared ? '' : selected} navSeq={navSeq} streamingBlockId={streamingBlockId} streamingLabel={streamingLabel} reviewSelection={reviewResolution?.selection ?? workbenchSelection} onSelectBlock={id => { if (selectedRef.current !== id) selectPassive(id); }} onSelectionChange={selection => {
            readingSelectionRef.current = selection; setSelectionLabel(selection?.quote.slice(0, 120) ?? '');
            const block = dataRef.current?.blocks.find(b => b.id === selection?.blockId);
            if (selection && block && !draftRef.current[block.id]?.dirty && !discussionRef.current.value?.pinned && selection.quote.length <= 2000)
              discussionRef.current.update({ blockId: block.id, version: block.version, quote: selection.quote, pinned: false, constraints: discussionRef.current.value?.constraints ?? '' });
          }} onCreateComment={addReadingComment} onEditSource={editReadingSelection} onOpenComment={openReadingComment} compareBaseline={compareBaseline} onChooseObject={id => { select(id); setObjectPicker(true); }} /> : <UniverEditor ref={univerRef} key={`${data.document.id}:${univerStructureKey}`} documentId={`prd-${data.document.id}`} title={conciseProjectTitle(data.document.title, 'PRD')}
            blocks={editorBlocks} selectedBlockId={selected} streamingBlockId={streamingBlockId} streamingLabel={streamingLabel}
            onSelectBlock={id => { if (selectedRef.current !== id) select(id); }}
            onViewportBlockChange={followVisibleBlock}
            onBlockContentChange={(id, content) => editBlock(id, { content })}
            onSelection={selections => { const first = selections[0]; if (first) setSelected(first.blockId);
              if (!wholeDocumentComment) { setEditorSelection(selections); setChecked(new Set(selections.map(item => item.blockId)));
                if (first) { setStart(String(first.startLine)); setEnd(String(first.endLine)); } } }}
            onBoundaryViolation={setNotice} />}
          {draft.dirty && draft.version !== block.version && <div className="prd-conflict"><strong>保存版本已变化，人工输入已保留</strong><BlockDiff before={block.content} after={draft.content} /><button onClick={() => void attempt(() => save(selected, true))}>基于当前 v{block.version} 保存我的草稿</button></div>}
          {candidates.map(r => <div className="prd-conflict" key={r.id}><strong>{r.apply_status === 'conflict' || r.base_version !== block.version ? 'AI 结果基于旧版本生成' : draft.dirty ? 'AI 已完成，等待你保存当前输入' : 'AI 结果待应用'}</strong><p>基线 v{r.base_version} → 当前 v{block.version}</p><div><button onClick={() => setDiffRun(r)}>查看 Diff</button><button onClick={() => void attempt(async () => { requireSaved('重新生成', [r.block_id!]); await mutate(data.document.id, `/runs/${r.id}/retry`, { request_id: crypto.randomUUID() }); await refresh(); })}>基于当前版本重新生成</button><button disabled={draft.dirty} onClick={() => void attempt(async () => { await apply(r, true); setDiffRun(null); })}>仍然应用</button></div></div>)}
        </>}</section>
        <aside className="prd-dock" aria-label="PRD 工具栏">
          <button aria-label="PRD 优化工作台" title="需求影响、健康地图、方案与待确认信息" onClick={() => { setWorkbenchTab('impact'); setWorkbenchOpen(true); setDockPanel(null); }}><Network size={20} /></button>
          <button aria-label="生成消息" title="生成消息" aria-expanded={dockPanel === 'activity'} className={dockPanel === 'activity' ? 'is-active' : ''} onClick={() => { setDockPanel(dockPanel === 'activity' ? null : 'activity'); setCommentAnchor(null); }}><Info size={20} />{(notice || data.runs.some(run => run.status === 'failed' || run.status === 'running' || run.status === 'queued')) && <i className="prd-dock-alert" />}</button>
          <button aria-label="全部批注" title="全部批注" aria-expanded={dockPanel === 'comments'} className={dockPanel === 'comments' ? 'is-active' : ''} onClick={() => { setDockPanel(dockPanel === 'comments' && !commentFocusOnly ? null : 'comments'); setCommentFocusOnly(false); setFocusedCommentId(null); setCommentAnchor(null); }}><MessageSquareText size={20} />{data.comments.filter(comment => !comment.resolved).length > 0 && <span className="prd-dock-count">{data.comments.filter(comment => !comment.resolved).length}</span>}</button>
          <button aria-label="全文审核意见" title="全文审核意见" aria-expanded={dockPanel === 'review'} className={dockPanel === 'review' ? 'is-active' : ''} onClick={() => { setDockPanel(dockPanel === 'review' ? null : 'review'); setCommentAnchor(null); }}><ClipboardCheck size={20} />{Boolean(wholeReport?.result?.issues?.length) && <span className="prd-dock-count">{wholeReport!.result!.issues!.length}</span>}</button>
          <button aria-label="修订版本" title="修订版本" aria-expanded={dockPanel === 'versions'} className={dockPanel === 'versions' ? 'is-active' : ''} onClick={() => { setDockPanel(dockPanel === 'versions' ? null : 'versions'); setVersionPreview(null); setCommentAnchor(null); }}><History size={20} /></button>
        </aside></div>
      {dockPanel && <section className={`prd-dock-popover${commentAnchor ? ' is-anchored' : ''}`} role="dialog" aria-modal="false" aria-label={dockPanel === 'activity' ? '生成消息' : dockPanel === 'review' ? '全文审核意见' : dockPanel === 'versions' ? '修订版本' : commentFocusOnly ? '正文批注' : '全部批注'} style={dockPanel === 'comments' && commentAnchor ? { left: Math.max(12, Math.min(commentAnchor.x + 12, window.innerWidth - 410)), top: Math.max(12, Math.min(commentAnchor.y, window.innerHeight - 420)) } : undefined}>
        <header><strong>{dockPanel === 'activity' ? '生成消息' : dockPanel === 'review' ? '全文审核意见' : dockPanel === 'versions' ? '修订版本' : commentFocusOnly ? '正文批注' : '全部批注'}</strong><button aria-label="关闭浮层" onClick={() => setDockPanel(null)}><X size={18} /></button></header>
        <div className="prd-dock-popover-body">{dockPanel === 'activity'
          ? <GenerationStatus key={data.document.id} documentId={data.document.id} runs={data.runs} blocks={data.blocks} notice={notice} onDismissNotice={() => setNotice('')} onSelectBlock={openProgressBlock} onCancel={r => void attempt(async () => { await mutate(data.document.id, `/runs/${r.id}/cancel`, {}); await refresh(); })} />
          : dockPanel === 'comments' ? <BlockCommentPanel comments={data.comments} blocks={data.blocks} current={selected} focusedCommentId={focusedCommentId} focusOnly={commentFocusOnly} showAll feedback={feedback} onFeedback={setFeedback} start={start} end={end} onRange={(s, e) => { setStart(s); setEnd(e); }} count={wholeDocumentComment ? data.blocks.length : checked.size || new Set(editorSelection.map(item => item.blockId)).size || 1} wholeDocument={wholeDocumentComment} onWholeDocument={value => { setWholeDocumentComment(value); if (value) { setStart(''); setEnd(''); setEditorSelection([]); } }} onAdd={() => void attempt(addComment)} onRevise={c => void attempt(() => reviseComment(c))} onResolve={c => void attempt(async () => ingest(await mutate(data.document.id, `/comments/${c.id}`, { resolved: !c.resolved }, 'PATCH')))} />
          : dockPanel === 'review' ? <>{wholeReport?.result || wholeReviewError
            ? <FullDocumentReport snapshot={data} loading={busy || anyActive} resolving={wholeRevisionActive} report={wholeReport} reviewError={wholeReviewError}
                onSelect={id => { select(id); setDockPanel(null); }} onOpenIssue={issue => { setDockPanel(null); openReviewIssue(issue); }} onResolve={() => void attempt(resolveFullReview)} />
            : <p className="prd-dock-empty">{wholeReviewActive ? '全文审核中…' : '尚无全文审核意见。点击正文上方的“审核全文”开始。'}</p>}
            {data.runs.filter(r => r.block_id === selected && r.result?.review_notes?.length).slice(-1).map(r => <div className="prd-review-notes" key={r.id}><strong>当前章节审核意见</strong><ul>{r.result!.review_notes!.map((note, i) => <li key={i}>{note}</li>)}</ul></div>)}
          </>
          : versionPreview ? <div className="prd-version-preview"><button onClick={() => setVersionPreview(null)}>← 返回版本列表</button><h3>全文版本 v{versionPreview.version}</h3><time>{versionTime(versionPreview.created_at)}</time><button disabled={anyActive || busy} onClick={() => void attempt(() => previewRestore(versionPreview.version))}>预览恢复为新版本</button>{versionPreview.snapshot.blocks.map(item => <section key={item.id}><h4>{item.title}</h4><pre>{item.content || '（无正文）'}</pre></section>)}</div>
          : <div className="prd-document-versions"><button disabled={anyActive || busy} onClick={() => void attempt(() => previewRestore())}>撤销最近一次 Agent 修改</button><button className="prd-compare-entry" disabled={versionsLoading || Boolean(versionsError) || documentVersions.length < 2} onClick={() => { setDockPanel(null); setVersionCompareOpen(true); }}>比较版本</button>{versionsLoading ? <p>正在加载版本…</p> : versionsError ? <p role="alert">{versionsError}</p> : documentVersions.length ? documentVersions.map(version => <button key={version.id} onClick={() => void attempt(async () => setVersionPreview(await getDocumentVersion(data.document.id, version.version)))}><strong>全文版本 v{version.version}</strong><time>{versionTime(version.created_at)}</time></button>) : <p className="prd-dock-empty">尚无已保存的全文版本。</p>}</div>}</div>
      </section>}
      {versionCompareOpen && <VersionCompare documentId={data.document.id} versions={documentVersions} formatTime={versionTime} onClose={() => setVersionCompareOpen(false)} />}
    </>}
    {nextStageDialog && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="进入下一阶段前审核">
      <header><h2>进入下一阶段前审核</h2><button aria-label="关闭审核提示" onClick={() => setNextStageDialog(false)}><X size={18} /></button></header>
      <p>修改后的内容需要一次当前版本审核。平时编辑和自动修订完成后不再追加审核。</p>
      {unsavedCount > 0 && <p>有 {unsavedCount} 个章节的草稿尚未保存，开始审核时将先保存。</p>}
      {!structureReady && <p>审核时会同步已修改章节的需求和验收结构，再进行全文审核。</p>}
      {wholeReviewActive && <p role="status">正在审核，完成后会显示结果。关闭弹窗不会取消任务。</p>}
      {stageReview?.status === 'failed' && <p role="alert">{stageReview.error || '审核失败，可以重试。'}</p>}
      {stageReview?.status === 'cancelled' && <p>审核已取消，可以重新开始。</p>}
      {stageReview?.status === 'completed' && <p>{stageReview.stale ? '审核期间正文又有修改，需要基于最新内容重新审核。' : wholeReviewClear ? '当前版本审核通过，可以继续。' : '审核完成，仍有待处理意见。请查看并处理后再继续。'}</p>}
      {error && <p role="alert">{error}</p>}
      <footer><button onClick={() => setNextStageDialog(false)}>继续编辑</button><button onClick={() => { setNextStageDialog(false); setDockPanel('review'); }}>查看审核意见</button>
        {wholeReviewClear && structureReady && !unsavedCount ? <button className="prd-primary" disabled={busy || anyActive} onClick={() => void attempt(submit)}>进入下一阶段</button>
          : <button className="prd-primary" disabled={busy || anyActive} onClick={() => void attempt(async () => { if (Object.values(draftRef.current).some(d => d.dirty)) await saveAll(); await reviewFull(true); })}>{wholeReviewActive ? '审核中…' : unsavedCount ? '保存并开始审核' : '开始审核当前版本'}</button>}
      </footer>
    </section></div>}
    {lastReceipt && !embedded && <section className="prd-edit-receipt" aria-label="最近修改回执"><strong>{lastReceipt.version ? `已保存全文 v${lastReceipt.version}` : '已保存修改'} · 等待手动审核</strong>
      <span>{lastReceipt.summary}</span><div>{lastReceipt.chapters.map(item => <button key={item.id} onClick={() => select(item.id)}>{item.title}</button>)}</div>
      {lastReceipt.skipped > 0 && <small>本次未应用 {lastReceipt.skipped} 项操作</small>}
      <button onClick={() => setLastReceipt(null)}>收起回执</button>
    </section>}
    {workbenchOpen && data && <PrdWorkbench key={data.document.id} snapshot={data} selectedId={selected ?? ''} initialTab={workbenchTab}
      onArtifact={kind => { if (kind === 'PPT') setPptDialog(true); else setNextStageDialog(true); }}
      onClose={() => setWorkbenchOpen(false)} beforePlan={() => requireSaved('生成工作台修改方案')}
      onLocate={(id, quote) => {
        setDocumentMode('read'); select(id);
        const target = data.blocks.find(b => b.id === id);
        setWorkbenchSelection(target && quote ? locateReadingSelection(target, quote) : null);
      }}
      onReview={() => reviewFull(false, 'affected')}
      onDocumentChange={ingest} onVersion={version => { setWorkbenchOpen(false); void attempt(async () => { setVersionPreview(await getDocumentVersion(data.document.id, version)); }); }}
      onPlan={planned => { setChatEditError(''); setBulkSelection(new Set(planned.steps.map((_, index) => index))); setPendingChatEdit(planned); }} />}
    {objectPicker && data?.blocks.find(b => b.id === selected) && <ObjectTargetPicker key={`${selected}:${data.blocks.find(b => b.id === selected)!.version}`} block={data.blocks.find(b => b.id === selected)!} onClose={() => setObjectPicker(false)} onChoose={(object: ObjectTarget) => {
      if (draftRef.current[object.block_id]?.dirty) { setError('请先保存当前章节草稿，再选择对象。'); setObjectPicker(false); return; }
      discussionRef.current.update({ blockId: object.block_id, version: object.block_version, quote: '', pinned: true, constraints: discussionRef.current.value?.constraints ?? '', object });
      readingSelectionRef.current = null; editorSelectionRef.current = []; setSelectionLabel(object.label); setObjectPicker(false);
    }} />}
    {pendingChatEdit && <div className="prd-modal-backdrop"><section className="prd-modal prd-modal-wide" role="dialog" aria-modal="true" aria-label="Agent 文档修改预览">
      <header><h2>Agent 文档修改预览</h2><button aria-label="关闭修改预览" disabled={applyingChatEdit} onClick={() => setPendingChatEdit(null)}><X size={18} /></button></header>
      <p>{pendingChatEdit.summary} · 涉及 {pendingChatEdit.preview.length} 个章节；应用后生成一个新的全文版本。</p>
      {pendingChatEdit.linkage_receipt && <details><summary>本次联动范围与未更新内容</summary><p>已选：{pendingChatEdit.linkage_receipt.selected_ids.map(id => data?.blocks.find(b => b.id === id)?.title ?? id).join('、')}</p><p>未选：{pendingChatEdit.linkage_receipt.omitted_ids.map(id => data?.blocks.find(b => b.id === id)?.title ?? id).join('、')}</p><p>{pendingChatEdit.linkage_receipt.artifact_note}</p></details>}
      {Boolean(pendingChatEdit.impact_notes?.length) && <ul aria-label="关联检查结果">{pendingChatEdit.impact_notes?.map((note, index) => <li key={`${index}:${note}`}>{note}</li>)}</ul>}
      {!pendingChatEdit.bulk_matches && !pendingChatEdit.restore_version && <section className="prd-edit-choices" aria-label="选择独立修改项">
        <h3>选择要应用的修改</h3><p>同章节的连续操作、相互依赖的章节及目录移动会合并选择。</p>
        {chatGroups.map((group, index) => <label key={index}><input type="checkbox" checked={group.every(i => bulkSelection.has(i))} disabled={applyingChatEdit}
          onChange={event => setBulkSelection(current => { const next = new Set(current); group.forEach(i => event.target.checked ? next.add(i) : next.delete(i)); return next; })} />
          第 {index + 1} 项 · {[...new Set(group.map(i => data?.blocks.find(b => b.id === pendingChatEdit.steps[i].block_id)?.title || '目录变更'))].join('、')} · {group.length} 个操作</label>)}
      </section>}
      {Boolean(pendingChatEdit.image_previews?.length) && <div className="prd-image-previews" aria-label="生成配图预览">
        {pendingChatEdit.image_previews?.map(image => <figure key={image.step_index}>
          <img src={image.data_url} alt={image.alt || '生成的产品示意图'} />
          <figcaption>{image.alt || '生成的产品示意图'} · 应用后保存到当前 PRD 图片资产</figcaption>
        </figure>)}
      </div>}
      {pendingChatEdit.bulk_matches && <section className="prd-bulk-matches" aria-label="批量修改命中清单">
        <h3>命中清单 · 已选 {bulkSelection.size}/{pendingChatEdit.bulk_matches.length}</h3>
        <div className="prd-bulk-actions"><button type="button" onClick={() => setBulkSelection(new Set(pendingChatEdit.bulk_matches?.map(match => match.step_index) ?? []))}>全选</button><button type="button" onClick={() => setBulkSelection(new Set())}>全不选</button></div>
        <div>{pendingChatEdit.bulk_matches.map(match => <label key={match.step_index}>
          <input type="checkbox" checked={bulkSelection.has(match.step_index)} onChange={event => setBulkSelection(current => {
            const next = new Set(current);
            if (event.target.checked) next.add(match.step_index); else next.delete(match.step_index);
            return next;
          })} />
          <span><strong>{match.title} · 第 {match.line} 行</strong><small>{match.context}</small></span>
        </label>)}</div>
        {Boolean(pendingChatEdit.excluded_count) && <details><summary>已跳过 {pendingChatEdit.excluded_count} 处（查看原因）</summary>
          {pendingChatEdit.excluded_matches?.map((match, index) => <p key={`${match.block_id}:${match.line}:${index}`}>
            {match.title} · 第 {match.line} 行：{match.reason} · {match.context}
          </p>)}</details>}
      </section>}
      {!selectedPreviewReady && <p role="status">正在核对所选操作的实际差异…</p>}
      <div className="prd-chat-edit-preview">{displayEdit!.preview.map(item => <section key={item.block_id} className={!pendingChatEdit.restore_version && !pendingChatEdit.steps.some((step, i) => step.block_id === item.block_id && bulkSelection.has(i)) && !pendingChatEdit.steps.some(step => step.action === 'move_block') ? 'is-unselected' : ''}>
        <h3>{item.title}</h3>
        {item.metadata_changes?.map(change => <p key={change.field}>{({ title: '章节名称', instruction: '章节要求', dependencies: '上游依赖' } as Record<string, string>)[change.field] ?? change.field}：{String(change.before ?? '')} → {String(change.after ?? '')}</p>)}
        {item.before !== item.after && <TableDiff before={item.before} after={selectedBulkContent(item.before, item.block_id, displayEdit!, bulkSelection)} />}
        {(item.old_order !== item.order || item.old_parent_id !== item.parent_id) && <p>目录位置：{item.old_order + 1} → {item.order + 1}{item.old_parent_id !== item.parent_id ? ' · 层级已调整' : ''}</p>}
        {item.before !== item.after && <BlockDiff before={item.before} after={selectedBulkContent(item.before, item.block_id, displayEdit!, bulkSelection)} />}
        {mermaidSources(item.after).filter(source => !mermaidSources(item.before).includes(source)).map((source, index) =>
          <PrdMermaidPreview key={`${index}:${source}`} source={source} title={`${item.title} · 修改后的图 ${index + 1}`} />)}
        {item.diagram_previews?.map(diagram => <div key={diagram.diagram_id}>
          {(diagram.added_entities.length > 0 || diagram.removed_entities.length > 0) && <p>实体变化：
            {diagram.added_entities.length > 0 && `新增 ${diagram.added_entities.join('、')}`}
            {diagram.added_entities.length > 0 && diagram.removed_entities.length > 0 && '；'}
            {diagram.removed_entities.length > 0 && `移除 ${diagram.removed_entities.join('、')}`}
          </p>}
          <DrawioErDiagram diagram={{ ...diagram, anchor: `preview-${diagram.anchor}` }} />
        </div>)}
      </section>)}</div>
      {chatEditError && <div role="alert" className="prd-alert"><p>{chatEditError}</p>
        <button disabled={applyingChatEdit} onClick={() => void (async () => {
          try { const latest = await getDocument(dataRef.current!.document.id); ingest(latest); setChatConflict(latest); }
          catch (reason) { setChatEditError(message(reason)); }
        })()}>读取最新正文并比较</button>
        {!pendingChatEdit.restore_version && pendingChatEdit.steps.every(step => step.action === 'replace_text' || step.action === 'move_text') && <button disabled={applyingChatEdit} onClick={() => void (async () => {
          setApplyingChatEdit(true);
          try {
            const planned = await mutate<PendingChatEdit>(dataRef.current!.document.id, '/chat-edits/rebase', { steps: pendingChatEdit.steps.filter((_, i) => bulkSelection.has(i)) });
            await refresh();
            setBulkSelection(new Set(planned.steps.map((_, i) => i))); setPendingChatEdit(planned); setChatEditError('');
          } catch (reason) { setChatEditError(message(reason)); }
          finally { setApplyingChatEdit(false); }
        })()}>在最新正文上重新定位文字修改</button>}
      </div>}
      {pendingChatEdit.job_id && <section aria-label="复用候选与处理冲突"><button disabled={applyingChatEdit || !bulkSelection.size} onClick={() => void (async () => {
        setApplyingChatEdit(true); setChatEditError('');
        try {
          requireSaved('复用候选');
          const planned = await mutate<PendingChatEdit>(dataRef.current!.document.id, '/chat-edits/reuse', { job_id: pendingChatEdit.job_id, indices: [...bulkSelection], take_indices: [...takeConflicts] });
          setReusedCandidate(planned); const latest = await getDocument(dataRef.current!.document.id); ingest(latest); setChatConflict(latest);
        } catch (reason) { setChatEditError(message(reason)); } finally { setApplyingChatEdit(false); }
      })()}>核对最新正文并复用所选候选</button>
        {reusedCandidate && <><p>可复用 {reusedCandidate.steps.length} 项。冲突项默认保留当前正文；本次没有调用模型。</p>
          {reusedCandidate.reuse_conflicts?.map(c => <div key={c.step_index}><p>{c.step_index >= 0 ? `操作 ${c.step_index + 1}：` : ''}{c.reason}</p>
            {c.can_take && <label><input type="checkbox" checked={takeConflicts.has(c.step_index)} onChange={e => setTakeConflicts(old => { const next = new Set(old); e.target.checked ? next.add(c.step_index) : next.delete(c.step_index); return next; })} />此单元格采用候选值（勾选后重新核对）</label>}
          </div>)}
          <button disabled={!reusedCandidate.steps.length || applyingChatEdit} onClick={() => { setPendingChatEdit(reusedCandidate); setBulkSelection(new Set(reusedCandidate.steps.map((_, i) => i))); setChatEditError(''); }}>打开可复用部分的独立预览</button>
        </>}
      </section>}
      {chatConflict && <section aria-label="当前正文与原候选比较"><h3>基线、当前正文与原候选</h3><p>比较后可保留当前值、复用无冲突项；无法精确定位的项需要重新生成。</p>
        {pendingChatEdit.preview.map(item => <details key={item.block_id} open><summary>{item.title}</summary>
          <h4>基线 → 当前正文</h4><BlockDiff before={item.before} after={chatConflict.blocks.find(b => b.id === item.block_id)?.content ?? ''} />
          <h4>当前正文 → 原候选（只作比较，不直接覆盖）</h4>
          <BlockDiff before={chatConflict.blocks.find(b => b.id === item.block_id)?.content ?? ''} after={item.after} /></details>)}
      </section>}
      <footer><button disabled={applyingChatEdit} onClick={() => setPendingChatEdit(null)}>暂不应用</button><button className="prd-primary" disabled={applyingChatEdit || !selectedPreviewReady || (!pendingChatEdit.restore_version && !bulkSelection.size)} onClick={() => void (async () => {
        setApplyingChatEdit(true); setChatEditError('');
        try {
          if (Object.values(draftRef.current).some(draft => draft?.dirty)) throw new Error('当前有未保存的人工草稿，请先保存再应用。');
          const latest = dataRef.current;
          if (!latest || latest.document.revision !== pendingChatEdit.base_revision) throw new Error('文档已变化，请先核对最新正文并复用候选。');
          const selectedSteps = pendingChatEdit.steps.filter((_, index) => bulkSelection.has(index));
          const diagramSources = selectedSteps.filter(step => step.action === 'mermaid_insert' || step.action === 'mermaid_replace')
            .map(step => String(step.diagram_source ?? ''));
          if (diagramSources.length) {
            const { default: mermaid } = await import('mermaid');
            for (const source of diagramSources) {
              const valid = await mermaid.parse(source);
              if (!valid) throw new Error('Mermaid 图源未通过语法检查，请重新发送指令。');
            }
          }
          if (!selectedSteps.length && !pendingChatEdit.restore_version) throw new Error('请至少保留一处修改。');
          const next = await apiClient.request<Snapshot>(documentPath(latest.document.id) + (pendingChatEdit.restore_version ? '/chat-edits/restore' : '/chat-edits/apply'), {
            method: 'POST', timeoutMs: 30000,
            body: pendingChatEdit.restore_version ? { expected_revision: pendingChatEdit.base_revision, version: pendingChatEdit.restore_version }
              : { expected_revision: pendingChatEdit.base_revision, steps: selectedSteps, alternative_id: pendingChatEdit.alternative_id, job_id: pendingChatEdit.job_id },
          });
          ingest(next);
          pendingChatRef.current = null;
          setPendingChatEdit(null);
          const changed = next.blocks.filter(b => latest.blocks.find(old => old.id === b.id)?.version !== b.version);
          const receipt: PrdEditReceipt = { summary: pendingChatEdit.summary, version: next.document.latest_version,
            chapters: changed.map(b => ({ id: b.id, title: b.title })),
            skipped: pendingChatEdit.restore_version ? 0 : pendingChatEdit.steps.length - selectedSteps.length };
          setLastReceipt(receipt);
          setNotice('已保存新版本；需要时手动审核，或在进入下一阶段前审核。');
          chatEditAppliedRef.current?.(pendingChatEdit.summary, receipt);
        } catch (reason) { setChatEditError(message(reason)); }
        finally { setApplyingChatEdit(false); }
      })()}>{applyingChatEdit ? '正在应用…' : pendingChatEdit.restore_version ? '恢复为新版本' : `应用 ${bulkSelection.size} 项操作`}</button></footer>
    </section></div>}
    {reviewResolution && <section className="prd-review-solution-popover" role="dialog" aria-modal="false" aria-label="选择审核意见解决方案">
      <header><div><strong>为这条审核意见添加批注</strong><small>{reviewResolution.issue.description}</small></div><button aria-label="关闭解决建议" onClick={() => setReviewResolution(null)}><X size={16} /></button></header>
      <div className="prd-review-solution-options">
        <label className={reviewResolution.choice === 'primary' ? 'is-selected' : ''}><input type="radio" name="review-solution" checked={reviewResolution.choice === 'primary'} onChange={() => setReviewResolution({ ...reviewResolution, choice: 'primary' })} /><span><strong>方案 1 · Agent 建议</strong><small>{reviewResolution.issue.suggestion}</small></span></label>
        {reviewResolution.issue.alternative_suggestion?.trim() && <label className={reviewResolution.choice === 'alternative' ? 'is-selected' : ''}><input type="radio" name="review-solution" checked={reviewResolution.choice === 'alternative'} onChange={() => setReviewResolution({ ...reviewResolution, choice: 'alternative' })} /><span><strong>方案 2 · Agent 建议</strong><small>{reviewResolution.issue.alternative_suggestion}</small></span></label>}
        <label className={reviewResolution.choice === 'custom' ? 'is-selected' : ''}><input type="radio" name="review-solution" checked={reviewResolution.choice === 'custom'} onChange={() => setReviewResolution({ ...reviewResolution, choice: 'custom' })} /><span><strong>方案 {reviewResolution.issue.alternative_suggestion?.trim() ? '3' : '2'} · 自定义</strong><textarea rows={3} aria-label="自定义解决方案" value={reviewResolution.customText} onFocus={() => setReviewResolution({ ...reviewResolution, choice: 'custom' })} onChange={event => setReviewResolution({ ...reviewResolution, choice: 'custom', customText: event.target.value })} placeholder="输入你的处理方式…" /></span></label>
      </div>
      <footer><button onClick={() => setReviewResolution(null)}>取消</button><button className="prd-primary" disabled={reviewCommentBusy || (reviewResolution.choice === 'custom' && !reviewResolution.customText.trim())} onClick={() => void attempt(createReviewComment)}>{reviewCommentBusy ? '生成批注中…' : '确认并生成批注'}</button></footer>
    </section>}
    {initialGenerationPending && !firstGenerationDismissed && <div className="prd-modal-backdrop"><section className="prd-modal prd-first-generation-modal" role="dialog" aria-modal="true" aria-label="首次生成 PRD 选择模板">
      <h2>首次生成 PRD · 选择模板</h2>
      <p>{firstGenerationTemplate === 'source'
        ? '将直接把导入的原始 PRD 原文写入文档作为初版，不调用 Agent 生成。后续仍可编辑或交 Agent 修改。'
        : '选择正文结构后再开始生成。后续仍可调整章节和内容。'}</p>
      <div className="prd-first-template-options" role="radiogroup" aria-label="首版 PRD 模板">
        {firstTemplateOptions.map(([value, label, detail]) => <label key={value} className={firstGenerationTemplate === value ? 'is-selected' : ''}>
          <input type="radio" name="initial-prd-template" checked={firstGenerationTemplate === value} onChange={() => { setFirstGenerationTemplate(value); setFirstGenerationError(''); }} />
          <span><strong>{label}</strong><small>{detail}</small></span>
        </label>)}
      </div>
      {firstGenerationTemplate === 'custom' && <div className="prd-first-template-upload">
        <label className="prd-field">上传模板目录文件（DOCX、Markdown、TXT 或文字型 PDF）
          <input type="file" accept=".docx,.md,.txt,.pdf" disabled={firstGenerationBusy} onChange={event => {
            const file = event.target.files?.[0]; event.target.value = '';
            if (file) void (async () => {
              setFirstGenerationBusy(true); setFirstGenerationError('');
              try { await readFirstGenerationTemplate(file); }
              catch (reason) { setFirstGenerationError(message(reason)); }
              finally { setFirstGenerationBusy(false); }
            })();
          }} />
        </label>
        <label className="prd-field">生成前确认目录
          <textarea rows={9} maxLength={100000} value={firstGenerationOutline} onChange={event => setFirstGenerationOutline(event.target.value)} placeholder={'# 产品背景\n# 需求说明\n## 功能需求'} />
        </label>
        <small>仅保留要生成的章节标题；PDF 或无标题样式的 DOCX 需要先删去正文段落。</small>
      </div>}
      {firstGenerationError && <div className="prd-first-template-error" role="alert">{firstGenerationError}</div>}
      <footer><button disabled={firstGenerationBusy} onClick={() => setFirstGenerationDismissed(true)}>稍后再说</button><button className="prd-primary" disabled={firstGenerationBusy} onClick={() => void startFirstGeneration()}>{firstGenerationBusy
        ? (firstGenerationTemplate === 'source' ? '正在导入原文…' : '正在准备…')
        : (firstGenerationTemplate === 'source' ? '直接使用原始文件' : '按所选方式生成 PRD')}</button></footer>
    </section></div>}
    {leaveRequest && <div className="prd-modal-backdrop"><section className="prd-modal prd-unsaved-modal" role="dialog" aria-modal="true" aria-label={unsavedCount ? '未保存更改' : '关闭 PRD'}><h2>{unsavedCount ? '尚未保存，是否保存？' : '关闭 PRD 编辑窗口？'}</h2><p>{unsavedCount ? `当前有 ${unsavedCount} 个 Block 包含未保存的修改。` : '关闭后将返回该项目的顶层图视图。'} 正在生成、审核或修订的 AI 任务不会取消，会继续在后台运行。</p><footer><button onClick={() => setLeaveRequest(null)}>继续编辑</button>{unsavedCount ? <><button onClick={finishLeave}>不保存并离开</button><button className="prd-primary" disabled={saving.size > 0} onClick={() => void attempt(async () => { await saveAll(); finishLeave(); })}>保存并离开</button></> : <button className="prd-primary" onClick={finishLeave}>继续关闭</button>}</footer></section></div>}
    {showImport && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="导入 PRD 文档"><h2>导入 PRD 文档</h2><p>回传系统导出的 DOCX 会把修改写回原 Block 并生成历史版本。Markdown、TXT 或文字型 PDF 仍可用于建立新大纲。</p><select aria-label="选择模板" value={template} onChange={e => setTemplate(e.target.value)}><option value="alibaba">阿里 PRD 模板</option><option value="tencent">鹅厂 PRD 模板</option><option value="custom">自定义大纲</option></select><input aria-label="上传 PRD 文件" type="file" accept=".docx,.md,.txt,.pdf" onChange={e => { if (e.target.files?.[0]) void attempt(() => readFile(e.target.files![0])); }} />{template === 'custom' && <textarea aria-label="大纲内容" rows={14} value={outline} onChange={e => setOutline(e.target.value)} placeholder={'# 产品背景\n# 功能设计\n## 具体功能'} />}<footer><button onClick={() => setShowImport(false)}>关闭</button><button className="prd-primary" disabled={busy} onClick={() => void attempt(importOutline)}>导入大纲并规划</button></footer></section></div>}
    {pptDialog && <div className="prd-modal-backdrop"><section className="prd-modal" role="dialog" aria-modal="true" aria-label="生成 PRD 演示 PPT"><h2>{data && data.document.submitted_spec_id && data.document.submitted_revision === data.document.revision ? '基于已冻结 PRD 生成 PPT' : '基于当前 PRD 草稿生成 PPT'}</h2><p>{data && data.document.submitted_spec_id && data.document.submitted_revision === data.document.revision ? '团队版重点讲清问题、范围、流程和验收；对外版重点讲清用户价值和场景。两版都会生成可编辑的 PPTX，绑定当前冻结版本。' : '当前 PRD 尚未定稿，将基于当前草稿走与正式版一致的编排流程生成可编辑 PPTX（首页会标注「草稿」），不影响冻结流程。'}</p><footer><button disabled={pptBusy} onClick={() => setPptDialog(false)}>关闭</button><button disabled={pptBusy} onClick={() => void createPresentation('team')}>{pptBusy ? <Loader2 className="ff-spin" size={15} /> : null}团队讲解版</button><button className="prd-primary" disabled={pptBusy} onClick={() => void createPresentation('external')}>对外介绍版</button></footer></section></div>}
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
