import { useState } from 'react';
import { Trash2, X } from 'lucide-react';
import type { AgentRun, Block } from './types';

const isActive = (run: AgentRun) => run.status === 'queued' || run.status === 'running';
const storageKey = (documentId: string) => `prd-dismissed-messages:${documentId}`;
function loadDismissed(documentId: string): Set<string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey(documentId)) ?? '[]');
    return new Set(Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []);
  } catch { return new Set(); }
}
export function runTitle(run: AgentRun, blocks: Block[]) {
  return run.type === 'document_review' ? (run.review_scope === 'affected' ? '受影响内容复审' : '全文审核')
    : run.type === 'document_revision' ? '全文自动修订'
    : run.type === 'initial' ? '首版自动生成'
    : blocks.find(block => block.id === run.block_id)?.title ?? '结构规划';
}
export function stageText(run: AgentRun) {
  const stage = run.result?.progress_stage;
  if (run.status === 'queued') return '正在排队';
  if (run.type === 'document_review') return run.review_scope === 'affected' ? '正在复审变动范围及校验全局规则' : '正在综合审核全文';
  if (stage === 'revising') return `正在生成第 ${run.result?.resolution_round ?? 1} 轮修订`;
  if (stage === 'verifying') return '正在复审修订结果';
  if (stage === 'streaming') return '正在流式写入正文';
  if (run.type === 'initial') return '正在连续生成 PRD';
  if (run.type === 'document_revision') return '正在分析并修订相关 Block';
  return 'AI 正在处理';
}
export function statusText(run: AgentRun) {
  if (isActive(run)) {
    const completed = run.result?.completed, total = run.result?.total;
    const count = typeof completed === 'number' && typeof total === 'number' && total > 0 ? ` · ${completed}/${total}` : '';
    if (run.type === 'document_review') return `${run.review_scope === 'affected' ? '正在复审受影响内容及全局规则' : '正在审核全文'}${typeof total === 'number' ? ` · ${total} 个 Block` : ''}`;
    return `${stageText(run)}${count}`;
  }
  if (run.status === 'failed') return run.error || '生成失败';
  if (run.status === 'cancelled') return '已取消';
  if (run.type === 'document_review') return run.apply_status === 'resolved' ? '审核问题已自动解决' : run.stale || run.apply_status === 'conflict' ? '审核完成，正文已变化' : '审核完成';
  if (run.type === 'document_revision') return run.apply_status === 'conflict' ? '自动修订完成，但正文已变化'
    : typeof run.result?.remaining_issues === 'number'
      ? run.result.remaining_issues > 0 ? `自动修订完成，复审仍有 ${run.result.remaining_issues} 条问题` : '审核问题已自动解决'
      : '自动修订完成，等待手动审核';
  if (run.apply_status === 'conflict') return '生成成功，待处理版本冲突';
  if (run.apply_status === 'pending') return '生成成功，待应用';
  return run.type === 'plan' ? '规划完成' : ['review', 'document_review'].includes(run.type) ? '审核完成' : '生成成功';
}

export function GenerationStatus({ documentId, runs, blocks, notice, onDismissNotice, onCancel, onSelectBlock }: {
  documentId: string; runs: AgentRun[]; blocks: Block[]; notice?: string;
  onDismissNotice?: () => void; onCancel: (run: AgentRun) => void; onSelectBlock?: (blockId: string) => void;
}) {
  const [dismissed, setDismissed] = useState(() => loadDismissed(documentId));
  function dismiss(run: AgentRun) {
    if (isActive(run)) return;
    const next = new Set(dismissed).add(run.id);
    setDismissed(next);
    try { localStorage.setItem(storageKey(documentId), JSON.stringify([...next])); } catch { /* Session-only dismissal. */ }
  }
  const visible = [...runs.filter(isActive), ...runs.filter(run => !isActive(run) && !dismissed.has(run.id)).reverse()];
  const latestReviewTargets = [...runs].reverse().find(run => run.type === 'document_review' && run.status === 'completed')
    ?.result?.issues?.flatMap(issue => issue.block_ids).filter((id, index, ids) => ids.indexOf(id) === index) ?? [];
  return <section className="prd-generation-status" aria-label="生成队列">
    <h3>生成队列 <small>{visible.length + (notice ? 1 : 0)} 条</small></h3>
    <div className="prd-runs" role="region" aria-label="生成消息列表" tabIndex={0}>
      {notice && <div className="prd-run-message is-system-notice">
        <span className="prd-dot is-ready" /><span className="prd-run-description">系统通知<small>{notice}</small></span>
        <button className="prd-run-delete" aria-label="关闭系统通知" onClick={onDismissNotice}><X size={14} /></button>
      </div>}
      {visible.length === 0 && !notice && <p className="prd-runs-empty">暂无生成消息</p>}
      {visible.map(run => {
        const title = runTitle(run, blocks), active = isActive(run);
        const dot = active ? 'generating' : run.status === 'failed' ? 'error' : run.status === 'completed' ? 'ready' : 'pending';
        const streamedIds = Object.keys(run.result?.stream_blocks ?? {});
        const currentBlockId = run.result?.current_block_id ?? run.block_id ?? streamedIds.at(-1);
        const reportedTargets = run.result?.active_block_ids?.length ? run.result.active_block_ids
          : run.type === 'document_revision' ? latestReviewTargets : [];
        const targetIds = reportedTargets.filter(id => blocks.some(block => block.id === id));
        const currentBlock = blocks.find(block => block.id === currentBlockId);
        const completedIds = (run.result?.completed_block_ids ?? streamedIds.filter(id => id !== currentBlockId))
          .filter((id, index, ids) => id !== currentBlockId && ids.indexOf(id) === index && blocks.some(block => block.id === id));
        const revisingIds = currentBlockId
          ? [currentBlockId]
          : targetIds.filter(id => !completedIds.includes(id)).slice(0, 6);
        const completed = run.result?.completed ?? 0, total = run.result?.total ?? 0;
        const progress = total > 0 ? Math.max(4, Math.min(100, (completed / total) * 100)) : 14;
        return <div key={run.id} className={`prd-run-message${active ? ' is-active' : ''}`} tabIndex={active ? 0 : undefined}>
          <span className={`prd-dot is-${dot}`} /><span className="prd-run-description">{title}<small>{statusText(run)}</small>
            {typeof run.result?.elapsed_seconds === 'number' && <small>已记录耗时 {run.result.elapsed_seconds.toFixed(1)} 秒</small>}
            {Boolean(run.result?.timing_events?.length) && <details><summary>查看步骤耗时</summary><ol>{run.result?.timing_events?.map((item, i) => <li key={i}>+{item.elapsed_seconds.toFixed(1)} 秒 · {item.message}</li>)}</ol></details>}
          </span>
          {active ? <button aria-label={`取消${title}`} onClick={() => onCancel(run)}>取消</button>
            : <button className="prd-run-delete" aria-label={`删除${title}消息`} title="删除消息" onClick={() => dismiss(run)}><Trash2 size={14} /></button>}
          {active && <aside className="prd-run-popover" aria-label={`${title}执行进度`}>
            <strong>{title}</strong><span>{stageText(run)}</span>
            {total > 0 && <><div className="prd-run-progress" aria-label={`${completed}/${total}`}><i style={{ width: `${progress}%` }} /></div>
              <small>{run.result?.progress_unit === 'scope' ? `审核范围 ${total}/${total} 个 Block` : `进度 ${completed}/${total}`}</small></>}
            {run.type === 'document_revision' && revisingIds.length > 0 && <div className="prd-run-block-list is-revising"><span>正在修订</span><div>{revisingIds.map(id => {
              const target = blocks.find(block => block.id === id)!;
              return <button key={id} onClick={() => onSelectBlock?.(id)}><i />{target.title}</button>;
            })}</div></div>}
            {run.type === 'document_revision' && <div className="prd-run-block-list is-completed"><span>已经修改 · {completedIds.length}</span>
              {completedIds.length > 0 ? <div>{completedIds.map(id => {
                const target = blocks.find(block => block.id === id)!;
                return <button key={id} onClick={() => onSelectBlock?.(id)}>{target.title}</button>;
              })}</div> : <small>暂时还没有完成写回的 Block</small>}
            </div>}
            {run.type !== 'document_revision' && currentBlock && <div className="prd-run-current"><span>当前 Block</span><button onClick={() => onSelectBlock?.(currentBlock.id)}>{currentBlock.title}</button></div>}
            {run.type !== 'document_revision' && !currentBlock && targetIds.length > 0 && <div className="prd-run-targets"><span>涉及 Block</span><div>{targetIds.slice(0, 6).map(id => {
              const target = blocks.find(block => block.id === id)!;
              return <button key={id} onClick={() => onSelectBlock?.(id)}>{target.title}</button>;
            })}{targetIds.length > 6 && <small>等 {targetIds.length} 个</small>}</div></div>}
            <small className="prd-run-popover-hint">点击 Block 名称可定位到实时正文。</small>
          </aside>}
        </div>;
      })}
    </div>
  </section>;
}
