import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Download, Loader2, MessageSquarePlus, RefreshCw, Send } from 'lucide-react';
import type { PrdCommentDto, PrdCommentableLinesDto, PrdDocumentDto, ReviewTaskDto, SessionStateDto, SpecVersionDto, PrdDiffDto } from './dto';
import { normalizeNetworkError } from './errors';
import {
  createPrdComment,
  getCommentableLines,
  getLatestPrd,
  getPrdDiff,
  getReviewTask,
  listPrdComments,
  listPrdVersions,
  publishPrdReview,
  replyPrdComment,
  resolvePrdComment,
} from './prd';
import { PrdDocumentViews } from './PrdDocumentViews';
import { ReviewFindings } from './ReviewFindings';
import { nextPrdConfirmationStep, reviewTaskRecoveryAction } from './workflowUi';

type Draft = { id: string; line: number; text: string; anchor: string; status: 'ready' | 'sending' | 'done' | 'error'; error?: string };
type ReviewData = { document: PrdDocumentDto | null; commentable: PrdCommentableLinesDto | null; diff: PrdDiffDto | null; comments: PrdCommentDto[]; commentsAvailable: boolean; versionCount: number };

const taskKey = (wi: string) => `firstflight.prd-task.${wi}`;
const failure = (reason: unknown) => {
  const error = normalizeNetworkError(reason);
  if (error.code === 'GITEA_UNAUTHORIZED') {
    return 'GITEA_UNAUTHORIZED：Gitea token 无效或已被撤销，请更新后端 .env 后重启服务。';
  }
  if (error.code === 'GITEA_FORBIDDEN') {
    return 'GITEA_FORBIDDEN：Gitea 账号缺少目标仓库的读写或评审权限。';
  }
  return `${error.code}：${error.message}`;
};

export function PrdReviewPanel({
  wi,
  sessionState,
  fallbackSpec,
  workflowBusy = false,
  onConfirmAndDecompose,
  onResourcesChanged,
}: {
  wi: string;
  sessionState: SessionStateDto;
  fallbackSpec: SpecVersionDto;
  workflowBusy?: boolean;
  onConfirmAndDecompose: (reviewNote: string) => Promise<void>;
  onResourcesChanged?: () => Promise<void>;
}) {
  const [data, setData] = useState<ReviewData | null>(null);
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const [draftText, setDraftText] = useState('');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [task, setTask] = useState<ReviewTaskDto | null>(null);
  const [replyText, setReplyText] = useState<Record<number, string>>({});
  const [reviewNote, setReviewNote] = useState('');
  const [autoResolveFindings, setAutoResolveFindings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewReady, setReviewReady] = useState(false);
  const [fallbackConfirmed, setFallbackConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setReviewReady(false);
    setFallbackConfirmed(false);
    setError(null);
    const [document, commentable, comments, versions, diff] = await Promise.allSettled([
      getLatestPrd(wi, signal), getCommentableLines(wi, signal),
      listPrdComments(wi, signal), listPrdVersions(wi, signal), getPrdDiff(wi, signal),
    ]);
    if (signal?.aborted) return;
    const next = {
      document: document.status === 'fulfilled' ? document.value : null,
      commentable: commentable.status === 'fulfilled' ? commentable.value : null,
      comments: comments.status === 'fulfilled' ? comments.value : [],
      commentsAvailable: comments.status === 'fulfilled',
      versionCount: versions.status === 'fulfilled' ? versions.value.length : 0,
      diff: diff.status === 'fulfilled' ? diff.value : null,
    };
    setData(next);
    const failures = [document, commentable, comments, versions, diff].filter((result) => result.status === 'rejected');
    if (failures.length) {
      setError(`批注或审核数据暂不可用，正文仍可阅读。${failure(failures[0].reason)}`);
    } else if (next.document?.version !== next.commentable?.version
      || next.document?.commit_sha !== next.commentable?.commit_sha
      || next.document?.version !== next.diff?.version
      || next.document?.commit_sha !== next.diff?.commit_sha) {
      setError('PRD 与 Diff 的版本不一致，请重试加载后再审核。');
    } else {
      setReviewReady(true);
    }
  }, [wi]);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    load(controller.signal).catch((reason) => {
      if (normalizeNetworkError(reason).code !== 'REQUEST_ABORTED') setError(failure(reason));
    });
    const savedTask = localStorage.getItem(taskKey(wi));
    if (savedTask) {
      getReviewTask(savedTask, controller.signal)
        .then(async (next) => {
          setTask(next);
          const recovery = reviewTaskRecoveryAction(next.status);
          if (recovery === 'refresh') {
            localStorage.removeItem(taskKey(wi));
            await load(controller.signal);
            await onResourcesChanged?.();
          } else if (recovery === 'error') {
            localStorage.removeItem(taskKey(wi));
            setError(next.error || 'PRD 发布失败。');
          }
        })
        .catch(() => localStorage.removeItem(taskKey(wi)));
    }
    return () => controller.abort();
  }, [load, onResourcesChanged, wi]);

  useEffect(() => {
    if (!task || !['pending', 'processing'].includes(task.status)) return;
    const controller = new AbortController();
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const baseDelay = [500, 1000, 2000, 3000][Math.min(attempt, 3)];
      const delay = document.visibilityState === 'hidden' ? Math.max(baseDelay, 10_000) : baseDelay;
      timer = setTimeout(async () => {
        try {
          const next = await getReviewTask(task.task_id, controller.signal);
          setTask(next);
          attempt += 1;
          if (next.status === 'done') {
            localStorage.removeItem(taskKey(wi));
            await load(controller.signal);
            await onResourcesChanged?.();
          } else if (next.status === 'error') {
            localStorage.removeItem(taskKey(wi));
            setError(next.error || 'PRD 发布失败。');
          } else {
            poll();
          }
        } catch (reason) {
          if (normalizeNetworkError(reason).code !== 'REQUEST_ABORTED') setError(failure(reason));
        }
      }, delay);
    };
    poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [load, onResourcesChanged, task?.status, task?.task_id, wi]);

  const unresolved = useMemo(() => data?.comments.filter((comment) => !comment.resolved) ?? [], [data]);
  const hasReviewFindings = sessionState.review_findings.length > 0;
  const reviewTaskActive = Boolean(task && ['pending', 'processing'].includes(task.status));
  const hasPendingDrafts = drafts.some((item) => item.status !== 'done');
  const localConfirmationStep = sessionState.legal_actions.includes('convert_to_work_item')
    ? 'convert_to_work_item'
    : sessionState.legal_actions.includes('approve')
      ? 'approve'
      : 'none';
  const confirmationStep = reviewReady
    ? nextPrdConfirmationStep(
        sessionState,
        unresolved.length,
        reviewTaskActive,
        hasPendingDrafts,
        autoResolveFindings,
      )
    : reviewTaskActive
      ? 'wait'
      : hasPendingDrafts
        ? 'submit_comments'
        : unresolved.length > 0
          ? 'none'
          : localConfirmationStep;
  const canPublishRevision = confirmationStep === 'publish_review';
  const canEnterDecomposition = confirmationStep === 'approve' || confirmationStep === 'convert_to_work_item';

  useEffect(() => {
    if (!hasReviewFindings) setAutoResolveFindings(false);
  }, [hasReviewFindings]);

  function addDraft() {
    if (selectedLine === null || !draftText.trim() || !data?.commentable || !reviewReady) return;
    const line = data.commentable.lines.find((item) => item.line === selectedLine);
    if (!line) return;
    setDrafts((current) => [...current, { id: crypto.randomUUID(), line: selectedLine, text: draftText.trim(), anchor: line.text, status: 'ready' }]);
    setDraftText('');
    setSelectedLine(null);
  }

  async function submitDrafts() {
    if (!reviewReady) return;
    setBusy(true);
    setError(null);
    for (const draft of drafts.filter((item) => item.status !== 'done')) {
      setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, status: 'sending', error: undefined } : item));
      try {
        await createPrdComment(wi, { line: draft.line, text: draft.text, anchor: draft.anchor });
        setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, status: 'done' } : item));
      } catch (reason) {
        setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, status: 'error', error: failure(reason) } : item));
      }
    }
    await load().catch((reason) => setError(failure(reason)));
    setBusy(false);
  }

  async function setResolved(comment: PrdCommentDto, resolved: boolean) {
    setBusy(true); setError(null);
    try { await resolvePrdComment(wi, comment.id, resolved); await load(); }
    catch (reason) { setError(failure(reason)); }
    finally { setBusy(false); }
  }

  async function sendReply(comment: PrdCommentDto) {
    const text = replyText[comment.id]?.trim();
    if (!text) return;
    setBusy(true); setError(null);
    try { await replyPrdComment(wi, comment.id, text); setReplyText((current) => ({ ...current, [comment.id]: '' })); await load(); }
    catch (reason) { setError(failure(reason)); }
    finally { setBusy(false); }
  }

  async function publish() {
    if (!reviewReady) return;
    setBusy(true); setError(null);
    try {
      const accepted = await publishPrdReview(wi, autoResolveFindings);
      localStorage.setItem(taskKey(wi), accepted.task_id);
      setTask({ task_id: accepted.task_id, wi, status: 'pending', base_version: accepted.base_version, new_version: null, new_commit_sha: null, error: null });
      setAutoResolveFindings(false);
    } catch (reason) { setError(failure(reason)); }
    finally { setBusy(false); }
  }

  async function confirmAndDecompose() {
    if (confirmationStep !== 'approve' && confirmationStep !== 'convert_to_work_item') return;
    if (!reviewReady && !fallbackConfirmed) {
      setError('请先确认已阅读正文，并同意在 Gitea 审核数据不可用时继续。');
      return;
    }
    setError(null);
    try {
      await onConfirmAndDecompose(reviewNote.trim());
      setReviewNote('');
    } catch (reason) {
      setError(failure(reason));
    }
  }

  function downloadMarkdown() {
    const content = data?.document?.content ?? fallbackSpec.markdown;
    const version = data?.document?.version ?? fallbackSpec.revision;
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = 'firstFlight-PRD-v' + version + '.md';
    link.click();
    URL.revokeObjectURL(href);
  }


  return (
    <section className="ff-prd-review-panel rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-medium">PRD 正文与审核</h2>
          <p className="text-xs text-slate-500">v{data?.document?.version ?? fallbackSpec.revision} · {data?.versionCount || 1} 个版本{data?.document?.commit_sha ? ` · ${data.document.commit_sha.slice(0,8)}` : ' · 已保存的正文'}</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={downloadMarkdown} className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs"><Download className="h-3.5 w-3.5" />下载 .md</button>
          <button aria-label="刷新 PRD" onClick={() => load()}><RefreshCw className="h-4 w-4" /></button>
        </div>
      </div>
      {error && <div role="alert" className="mb-4 rounded-lg bg-amber-500/10 p-3 text-sm text-amber-200"><p>{error}</p><button type="button" onClick={() => load()} className="mt-2 rounded border border-amber-500/40 px-3 py-1">重试加载</button></div>}
      {task && <div className="mb-4 flex items-center gap-2 rounded-lg bg-cyan-500/10 p-3 text-sm text-cyan-200">{['pending', 'processing'].includes(task.status) && <Loader2 className="h-4 w-4 animate-spin" />}发布任务：{task.status}{task.new_version ? ` · 已生成 v${task.new_version}` : ''}</div>}

      <PrdDocumentViews content={data?.document?.content ?? fallbackSpec.markdown} version={data?.document?.version ?? fallbackSpec.revision} patch={data?.diff?.patch ?? null} commentable={data?.commentable ?? null} ready={reviewReady && !busy && !workflowBusy && !reviewTaskActive} selectedLine={selectedLine} onSelectLine={setSelectedLine} />
      {selectedLine !== null && reviewReady && <div className="mb-4 flex gap-2"><input value={draftText} onChange={(event) => setDraftText(event.target.value)} placeholder={`给第 ${selectedLine} 行添加批注`} className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" /><button aria-label="暂存批注" onClick={addDraft} className="rounded-lg bg-cyan-500 px-3 text-slate-950"><MessageSquarePlus className="h-4 w-4" /></button></div>}
      {sessionState.review_findings.length > 0 && <details className="mb-4 rounded-xl border border-amber-500/20 p-4"><summary className="cursor-pointer text-sm text-amber-200">审核发现 · {sessionState.review_findings.length} 项（点击展开）</summary><div className="mt-3"><ReviewFindings findings={sessionState.review_findings} /></div></details>}
      <div className="mb-4 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4">
        <h3 className="text-sm font-medium text-cyan-100">人工审核门禁</h3>
        <p className="mt-1 text-xs leading-5 text-slate-400">
          更新 PRD 与进入任务拆分是两个独立方向。更新只生成新版并继续审核；拆分仅在 Agent 自动审核通过且没有待处理批注时可用。
        </p>
        {!reviewReady && (confirmationStep === 'approve' || confirmationStep === 'convert_to_work_item') && (
          <label className="ff-fallback-approval">
            <input type="checkbox" checked={fallbackConfirmed} onChange={(event) => setFallbackConfirmed(event.target.checked)} />
            <span>我已阅读当前 PRD 正文，确认在 Gitea Diff 与评论暂不可用时继续。此操作不会伪造或补写 Gitea 批注。</span>
          </label>
        )}
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-slate-700 bg-slate-950 p-3">
            <h4 className="text-xs font-medium">方向 1 · 根据批注更新 PRD</h4>
            <p className="mt-1 min-h-10 text-xs leading-5 text-slate-500">Agent 优先融合所有未解决批注；勾选自动处理后，再按推荐建议修复剩余审核发现。生成新版本后仍停留在 PRD 审核阶段。</p>
            <label className={`mt-3 flex items-center gap-2 text-xs ${hasReviewFindings ? 'cursor-pointer text-slate-300' : 'cursor-not-allowed text-slate-600'}`}>
              <input
                type="checkbox"
                checked={autoResolveFindings}
                disabled={!hasReviewFindings || busy || workflowBusy || reviewTaskActive}
                onChange={(event) => setAutoResolveFindings(event.target.checked)}
              />
              <span>agent自动处理待审核项</span>
            </label>
            <button disabled={busy || workflowBusy || !canPublishRevision} onClick={publish} className="mt-3 flex items-center gap-2 rounded-lg bg-violet-500 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">
              <Send className="h-3.5 w-3.5" />确认批注并生成新版 PRD
            </button>
          </div>
          <div className="rounded-lg border border-slate-700 bg-slate-950 p-3">
            <h4 className="text-xs font-medium">方向 2 · 进入任务拆分</h4>
            <p className="mt-1 min-h-10 text-xs leading-5 text-slate-500">仅确认当前已通过 Agent 自动审核的版本；该动作不会处理批注。</p>
            <button disabled={busy || workflowBusy || !canEnterDecomposition || (!reviewReady && !fallbackConfirmed)} onClick={confirmAndDecompose} className="mt-3 flex items-center gap-2 rounded-lg bg-cyan-500 px-3 py-2 text-xs font-medium text-slate-950 disabled:opacity-40">
              {(busy || workflowBusy) && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {confirmationStep === 'convert_to_work_item' ? '开始任务拆分' : '确认当前 PRD，进入任务拆分'}
            </button>
          </div>
          {confirmationStep === 'wait' && <span className="text-xs text-cyan-200">Agent 正在根据批注生成新版 PRD，请等待完成后继续审核。</span>}
          {confirmationStep === 'submit_comments' && <span className="text-xs text-amber-200">请先逐条提交上方暂存批注，再确认生成新版 PRD。</span>}
          {sessionState.current_spec_status === 'REWORK' && <span className="text-xs text-amber-200">Agent 自动审核仍有阻断项，请先通过批注生成修订版。</span>}
          {reviewReady && sessionState.current_spec_status === 'HUMAN_REVIEW' && unresolved.length > 0 && <span className="text-xs text-amber-200">仍有未解决批注，当前版本不能进入任务拆分。</span>}
          {!reviewReady && confirmationStep === 'none' && <span className="text-xs text-amber-200">Gitea 审核数据不可用；恢复后才能生成修订版或进入拆分。</span>}
        </div>
      </div>

      {drafts.length > 0 && <div className="mt-4 space-y-2"><div className="flex items-center justify-between"><h3 className="text-sm font-medium">待提交批注</h3><button disabled={busy || !reviewReady || drafts.every((item) => item.status === 'done')} onClick={submitDrafts} className="rounded-lg bg-cyan-500 px-3 py-1.5 text-xs font-medium text-slate-950 disabled:opacity-50">逐条提交</button></div>{drafts.map((draft) => <div key={draft.id} className="rounded-lg border border-slate-800 p-3 text-sm"><p>第 {draft.line} 行：{draft.text}</p><p className={`mt-1 text-xs ${draft.status === 'error' ? 'text-rose-300' : draft.status === 'done' ? 'text-emerald-300' : 'text-slate-500'}`}>{draft.status}{draft.error ? ` · ${draft.error}` : ''}</p></div>)}</div>}

      <div className="mt-5 space-y-3"><div className="flex items-center justify-between"><h3 className="text-sm font-medium">Gitea 评论（{(data?.comments.length ?? 0)}）</h3><span className="text-xs text-slate-500">{unresolved.length} 条未解决</span></div>{data?.comments.map((comment) => <article key={comment.id} className="rounded-xl border border-slate-800 bg-slate-950 p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-xs text-slate-500">第 {comment.line} 行 · {comment.author_type}</p><p className="mt-1 text-sm">{comment.body}</p></div><button disabled={busy || !reviewReady || workflowBusy || reviewTaskActive} onClick={() => setResolved(comment, !comment.resolved)} className={`rounded px-2 py-1 text-xs ${comment.resolved ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-800 text-slate-300'}`}><Check className="mr-1 inline h-3 w-3" />{comment.resolved ? '已解决，点击恢复' : '标记解决'}</button></div>{comment.replies.map((reply) => <div key={reply.id} className="mt-2 border-l border-slate-700 pl-3 text-sm"><span className="text-xs text-slate-500">{reply.author_type}</span><p>{reply.body}</p></div>)}<div className="mt-3 flex gap-2"><input value={replyText[comment.id] || ''} onChange={(event) => setReplyText((current) => ({ ...current, [comment.id]: event.target.value }))} placeholder="人工回复（不会自动解决）" className="min-w-0 flex-1 rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs" /><button disabled={busy || !reviewReady || workflowBusy || reviewTaskActive} onClick={() => sendReply(comment)} className="rounded bg-slate-800 px-2 text-xs">回复</button></div></article>)}</div>
    </section>
  );
}
