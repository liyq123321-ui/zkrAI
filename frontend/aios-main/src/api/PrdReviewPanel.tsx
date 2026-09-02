import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Loader2, MessageSquarePlus, RefreshCw, Send } from 'lucide-react';
import type { PrdCommentDto, PrdCommentableLinesDto, PrdDocumentDto, ReviewTaskDto } from './dto';
import { normalizeNetworkError } from './errors';
import {
  createPrdComment,
  getCommentableLines,
  getLatestPrd,
  getReviewTask,
  listPrdComments,
  listPrdVersions,
  publishPrdReview,
  replyPrdComment,
  resolvePrdComment,
} from './prd';

type Draft = { id: string; line: number; text: string; anchor: string; status: 'ready' | 'sending' | 'done' | 'error'; error?: string };
type ReviewData = { document: PrdDocumentDto; commentable: PrdCommentableLinesDto; comments: PrdCommentDto[]; versionCount: number };

const taskKey = (wi: string) => `firstflight.prd-task.${wi}`;
const failure = (reason: unknown) => {
  const error = normalizeNetworkError(reason);
  return `${error.code}：${error.message}`;
};

export function PrdReviewPanel({ wi }: { wi: string }) {
  const [data, setData] = useState<ReviewData | null>(null);
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const [draftText, setDraftText] = useState('');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [task, setTask] = useState<ReviewTaskDto | null>(null);
  const [replyText, setReplyText] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    const [document, commentable, comments, versions] = await Promise.all([
      getLatestPrd(wi, signal),
      getCommentableLines(wi, signal),
      listPrdComments(wi, signal),
      listPrdVersions(wi, signal),
    ]);
    setData({ document, commentable, comments, versionCount: versions.length });
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
        .then(setTask)
        .catch(() => localStorage.removeItem(taskKey(wi)));
    }
    return () => controller.abort();
  }, [load, wi]);

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
  }, [load, task?.status, task?.task_id, wi]);

  const unresolved = useMemo(() => data?.comments.filter((comment) => !comment.resolved) ?? [], [data]);

  function addDraft() {
    if (selectedLine === null || !draftText.trim() || !data) return;
    const line = data.commentable.lines.find((item) => item.line === selectedLine);
    if (!line) return;
    setDrafts((current) => [...current, { id: crypto.randomUUID(), line: selectedLine, text: draftText.trim(), anchor: line.text, status: 'ready' }]);
    setDraftText('');
    setSelectedLine(null);
  }

  async function submitDrafts() {
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
    setBusy(true); setError(null);
    try {
      const accepted = await publishPrdReview(wi);
      localStorage.setItem(taskKey(wi), accepted.task_id);
      setTask({ task_id: accepted.task_id, wi, status: 'pending', base_version: accepted.base_version, new_version: null, new_commit_sha: null, error: null });
    } catch (reason) { setError(failure(reason)); }
    finally { setBusy(false); }
  }

  if (!data) return <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5"><h2 className="font-medium">PRD 行批注</h2><p className="mt-3 text-sm text-slate-500">{error || '正在从 Gitea 加载 PRD…'}</p></section>;

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-medium">PRD 行批注</h2><p className="text-xs text-slate-500">v{data.document.version} · {data.versionCount} 个版本 · commit {data.document.commit_sha?.slice(0, 8)}</p></div><button onClick={() => load().catch((reason) => setError(failure(reason)))}><RefreshCw className="h-4 w-4" /></button></div>
      {error && <p className="mb-4 rounded-lg bg-rose-500/10 p-3 text-sm text-rose-200">{error}</p>}
      {task && <div className="mb-4 flex items-center gap-2 rounded-lg bg-cyan-500/10 p-3 text-sm text-cyan-200">{['pending', 'processing'].includes(task.status) && <Loader2 className="h-4 w-4 animate-spin" />}发布任务：{task.status}{task.new_version ? ` · 已生成 v${task.new_version}` : ''}</div>}

      <div className="max-h-[480px] overflow-auto rounded-xl border border-slate-800 bg-slate-950 font-mono text-xs">
        {data.commentable.lines.map((line) => <button key={line.line} onClick={() => setSelectedLine(line.line)} className={`grid w-full grid-cols-[48px_16px_1fr] gap-2 border-b border-slate-900 px-3 py-1 text-left hover:bg-cyan-500/10 ${selectedLine === line.line ? 'bg-cyan-500/15' : ''}`}><span className="text-right text-slate-600">{line.line}</span><span className={line.kind === 'addition' ? 'text-emerald-400' : 'text-slate-600'}>{line.kind === 'addition' ? '+' : ' '}</span><span className="whitespace-pre-wrap text-slate-300">{line.text || ' '}</span></button>)}
      </div>

      {selectedLine !== null && <div className="mt-3 flex gap-2"><input value={draftText} onChange={(event) => setDraftText(event.target.value)} placeholder={`给第 ${selectedLine} 行添加批注`} className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" /><button onClick={addDraft} className="rounded-lg bg-cyan-500 px-3 text-slate-950"><MessageSquarePlus className="h-4 w-4" /></button></div>}

      {drafts.length > 0 && <div className="mt-4 space-y-2"><div className="flex items-center justify-between"><h3 className="text-sm font-medium">待提交批注</h3><button disabled={busy || drafts.every((item) => item.status === 'done')} onClick={submitDrafts} className="rounded-lg bg-cyan-500 px-3 py-1.5 text-xs font-medium text-slate-950 disabled:opacity-50">逐条提交</button></div>{drafts.map((draft) => <div key={draft.id} className="rounded-lg border border-slate-800 p-3 text-sm"><p>第 {draft.line} 行：{draft.text}</p><p className={`mt-1 text-xs ${draft.status === 'error' ? 'text-rose-300' : draft.status === 'done' ? 'text-emerald-300' : 'text-slate-500'}`}>{draft.status}{draft.error ? ` · ${draft.error}` : ''}</p></div>)}</div>}

      <div className="mt-5 space-y-3"><div className="flex items-center justify-between"><h3 className="text-sm font-medium">Gitea 评论（{data.comments.length}）</h3><button disabled={busy || unresolved.length === 0 || task?.status === 'pending' || task?.status === 'processing'} onClick={publish} className="flex items-center gap-2 rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-1.5 text-xs text-violet-200 disabled:opacity-40"><Send className="h-3.5 w-3.5" />发布并生成新版</button></div>{data.comments.map((comment) => <article key={comment.id} className="rounded-xl border border-slate-800 bg-slate-950 p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-xs text-slate-500">第 {comment.line} 行 · {comment.author_type}</p><p className="mt-1 text-sm">{comment.body}</p></div><button disabled={busy} onClick={() => setResolved(comment, !comment.resolved)} className={`rounded px-2 py-1 text-xs ${comment.resolved ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-800 text-slate-300'}`}><Check className="mr-1 inline h-3 w-3" />{comment.resolved ? '已解决，点击恢复' : '标记解决'}</button></div>{comment.replies.map((reply) => <div key={reply.id} className="mt-2 border-l border-slate-700 pl-3 text-sm"><span className="text-xs text-slate-500">{reply.author_type}</span><p>{reply.body}</p></div>)}<div className="mt-3 flex gap-2"><input value={replyText[comment.id] || ''} onChange={(event) => setReplyText((current) => ({ ...current, [comment.id]: event.target.value }))} placeholder="人工回复（不会自动解决）" className="min-w-0 flex-1 rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs" /><button disabled={busy} onClick={() => sendReply(comment)} className="rounded bg-slate-800 px-2 text-xs">回复</button></div></article>)}</div>
    </section>
  );
}
