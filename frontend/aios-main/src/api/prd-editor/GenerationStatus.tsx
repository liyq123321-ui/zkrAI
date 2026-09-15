import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import type { AgentRun, Block } from './types';

const isActive = (run: AgentRun) => run.status === 'queued' || run.status === 'running';
const storageKey = (documentId: string) => `prd-dismissed-messages:${documentId}`;
function loadDismissed(documentId: string): Set<string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey(documentId)) ?? '[]');
    return new Set(Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []);
  } catch { return new Set(); }
}
function statusText(run: AgentRun) {
  if (run.status === 'queued') return '排队中';
  if (run.status === 'running') return 'AI 正在处理';
  if (run.status === 'failed') return run.error || '生成失败';
  if (run.status === 'cancelled') return '已取消';
  if (run.type === 'document_review') return run.stale || run.apply_status === 'conflict' ? '审核完成，正文已变化' : '审核完成';
  if (run.apply_status === 'conflict') return '生成成功，待处理版本冲突';
  if (run.apply_status === 'pending') return '生成成功，待应用';
  return run.type === 'plan' ? '规划完成' : ['review', 'document_review'].includes(run.type) ? '审核完成' : '生成成功';
}

export function GenerationStatus({ documentId, runs, blocks, onCancel }: {
  documentId: string; runs: AgentRun[]; blocks: Block[]; onCancel: (run: AgentRun) => void;
}) {
  const [dismissed, setDismissed] = useState(() => loadDismissed(documentId));
  function dismiss(run: AgentRun) {
    if (isActive(run)) return;
    const next = new Set(dismissed).add(run.id);
    setDismissed(next);
    // Dismiss the notification only: jobs remain available for candidates,
    // version history and the durable first-draft idempotency guard.
    try { localStorage.setItem(storageKey(documentId), JSON.stringify([...next])); } catch { /* Session-only dismissal if storage is unavailable. */ }
  }
  const visible = [
    ...runs.filter(isActive),
    ...runs.filter(run => !isActive(run) && !dismissed.has(run.id)).reverse(),
  ];
  return <section className="prd-generation-status" aria-label="生成队列">
    <h3>生成队列 <small>{visible.length} 条</small></h3>
    <div className="prd-runs" role="region" aria-label="生成消息列表" tabIndex={0}>
      {visible.length === 0 && <p className="prd-runs-empty">暂无生成消息</p>}
      {visible.map(run => {
        const title = run.type === 'document_review' ? '全文审核' : run.type === 'initial' ? '首版自动生成' : blocks.find(block => block.id === run.block_id)?.title ?? '结构规划';
        const active = isActive(run);
        const dot = active ? 'generating' : run.status === 'failed' ? 'error' : run.status === 'completed' ? 'ready' : 'pending';
        return <div key={run.id} className="prd-run-message">
          <span className={`prd-dot is-${dot}`} />
          <span className="prd-run-description">{title}<small>{statusText(run)}</small></span>
          {active ? <button aria-label={`取消${title}`} onClick={() => onCancel(run)}>取消</button>
            : <button className="prd-run-delete" aria-label={`删除${title}消息`} title="删除消息" onClick={() => dismiss(run)}><Trash2 size={14} /></button>}
        </div>;
      })}
    </div>
  </section>;
}
