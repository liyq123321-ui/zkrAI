import { useEffect, useState } from 'react';
import { apiClient } from '../client';
import { documentPath, getDocument, mutate } from './api';
import type { PendingChatEdit } from './PRDEditor';
import { currentActorId } from '../../auth/session';
import { appConfig } from '../config';

export interface DocumentJob {
  id: string; title: string; status: 'queued' | 'running' | 'ready' | 'failed' | 'cancelled' | 'applied';
  base_revision: number; error?: string; result?: PendingChatEdit;
  events: { message: string; seconds: number }[]; created_at: string;
  group?: { id: string; part: number; total: number };
}
export const jobNames: Record<DocumentJob['status'], string> = { queued: '排队中', running: '生成中', ready: '候选已保存，待应用', failed: '失败，可重试', cancelled: '已取消结果接收', applied: '已应用' };
export const listDocumentJobs = (id: string) => apiClient.request<DocumentJob[]>(documentPath(id) + '/workbench/jobs');
export const getDocumentJob = (id: string, job: string) => apiClient.request<DocumentJob>(documentPath(id) + `/workbench/jobs/${encodeURIComponent(job)}`);
export async function submitPrdOperation<T>(id: string, path: string, payload: object): Promise<T> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify([path, payload])));
  const key = `ff-prd-submit:${encodeURIComponent(appConfig.apiBaseUrl)}:${encodeURIComponent(currentActorId())}:${id}:${Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')}`;
  let requestId: string;
  try { requestId = localStorage.getItem(key) || crypto.randomUUID(); localStorage.setItem(key, requestId); }
  catch { throw new Error('无法保存请求标识，请检查本机存储后重试，以避免重复提交。'); }
  const result = await mutate<T>(id, path, { ...payload, request_id: requestId });
  try { localStorage.removeItem(key); } catch { /* Keeping the same request id is safe. */ }
  return result;
}
export const enqueueDocumentJob = (id: string, revision: number, payload: object) =>
  submitPrdOperation<DocumentJob>(id, '/workbench/jobs', { ...payload, expected_revision: revision });

export function DocumentJobs({ documentId, revision, onPreview, beforePreview }: { documentId: string; revision: number; onPreview: (p: PendingChatEdit) => void; beforePreview: () => void }) {
  const [jobs, setJobs] = useState<DocumentJob[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('pending');
  const [group, setGroup] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let live = true, timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try { const values = await listDocumentJobs(documentId); if (live) { setJobs(values); setError(''); } }
      catch (e) { if (live) setError(e instanceof Error ? e.message : String(e)); }
      finally { if (live) timer = setTimeout(refresh, 3000); }
    }
    void refresh(); return () => { live = false; clearTimeout(timer); };
  }, [documentId]);
  async function act(id: string, operation: () => Promise<unknown>) {
    setBusy(id); setError(''); setNotice(''); try { await operation(); setJobs(await listDocumentJobs(documentId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(''); }
  }
  const visible = jobs.filter(job => (filter === 'all' || (filter === 'pending' ? !['applied', 'cancelled'].includes(job.status) : job.status === filter))
    && (!group || job.group?.id === group) && `${job.title} ${job.error ?? ''} ${job.group?.id ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const groups = [...new Set(jobs.flatMap(job => job.group ? [job.group.id] : []))];
  return <section aria-label="已保存的候选任务"><p>生成期间可以关闭面板继续阅读。完成后从这里找回预览；正文版本变化时，先尝试无需模型的核对复用。</p>
    <div className="prd-workbench-compose"><input aria-label="搜索候选任务" placeholder="搜索修改要求、失败原因或联动组…" value={query} onChange={e => setQuery(e.target.value)} />
      <label>任务状态<select value={filter} onChange={e => setFilter(e.target.value)}><option value="pending">待处理</option><option value="all">全部</option>{Object.entries(jobNames).map(([key, name]) => <option key={key} value={key}>{name}（{jobs.filter(j => j.status === key).length}）</option>)}</select></label>
      <label>联动组<select value={group} onChange={e => setGroup(e.target.value)}><option value="">全部组</option>{groups.map(g => <option key={g} value={g}>{g.slice(-8)}</option>)}</select></label>
      <button disabled={!!busy} onClick={() => void act('refresh', async () => undefined)}>刷新任务</button>
      <button onClick={() => { setQuery(''); setFilter('pending'); setGroup(''); }}>重置筛选</button>
    </div><p>显示 {visible.length} / 最近 {jobs.length} 项 · 待预览 {jobs.filter(j => j.status === 'ready').length} 项</p>
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}{!jobs.length && <p>暂无候选任务。</p>}
    {!!jobs.length && !visible.length && <p>没有符合筛选条件的任务，可切换“全部”或清空搜索。</p>}
    {visible.map(job => <article className="prd-workbench-card" key={job.id}><h3>{job.title}</h3><small>{jobNames[job.status]} · {new Date(job.created_at).toLocaleString('zh-CN', { hour12: false })} · 来源修订 {job.base_revision}</small>
      {job.status === 'ready' && job.base_revision !== revision && <p>正文或规则已更新，请核对后再应用。</p>}
      {job.group && <p>联动组 {job.group.id.slice(-8)} · 第 {job.group.part} / {job.group.total} 份</p>}
      <p role="status">{job.events.at(-1)?.message}</p>{job.error && <p>{job.error}</p>}
      <details><summary>执行记录</summary>{job.events.map((event, i) => <p key={i}>+{event.seconds}s · {event.message}</p>)}</details>
      {job.status === 'ready' && <button disabled={!!busy} onClick={() => void act(job.id, async () => { beforePreview(); const detail = await getDocumentJob(documentId, job.id); if (detail.result) onPreview(detail.result); })}>打开已保存预览</button>}
      {job.status === 'ready' && <button disabled={!!busy} onClick={() => void act(job.id, async () => {
        beforePreview(); const detail = await getDocumentJob(documentId, job.id);
        if (!detail.result?.steps.length) throw new Error('此候选没有可复用操作，请打开原预览查看。');
        const result = await mutate<PendingChatEdit>(documentId, '/chat-edits/reuse', { job_id: job.id, indices: detail.result.steps.map((_, i) => i) });
        const conflicts = result.reuse_conflicts ?? [];
        if (conflicts.length) { setNotice(`已核对：可复用 ${result.steps.length} 项，${conflicts.length} 项需处理。${result.steps.length ? '可复用部分已另存为待预览任务。' : ''}原候选保留。` + conflicts.map(c => c.reason).join('；')); }
        else onPreview(result);
      })}>核对并复用（不调用模型）</button>}
      {['failed', 'cancelled', 'ready'].includes(job.status) && <button disabled={!!busy} onClick={() => void act(job.id, async () => { beforePreview(); const current = await getDocument(documentId); await mutate(documentId, `/workbench/jobs/${job.id}/retry`, { request_id: crypto.randomUUID(), expected_revision: current.document.revision }); })}>基于当前版本重试这一项</button>}
      {['queued', 'running'].includes(job.status) && <button disabled={!!busy} onClick={() => void act(job.id, () => mutate(documentId, `/workbench/jobs/${job.id}/cancel`, {}))}>取消结果接收</button>}
    </article>)}
  </section>;
}
