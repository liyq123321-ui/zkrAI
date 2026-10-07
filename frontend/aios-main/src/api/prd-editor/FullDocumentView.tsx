import type { AgentRun, Block, Comment, DocumentReviewIssue, Snapshot } from './types';

export function fullDocument(blocks: Block[]) {
  const lines: string[] = [];
  const sections: { block: Block; start: number; bodyStart: number; end: number; bodyLines: number }[] = [];
  const ordered = [...blocks].sort((a, b) => a.order - b.order);
  const visited = new Set<string>();
  function walk(parent: string | null, depth: number) {
    for (const block of ordered.filter(b => b.parent_id === parent)) {
      if (visited.has(block.id)) continue;
      visited.add(block.id);
      const body = block.content ? block.content.split(/\r\n|[\n\r\v\f\u0085\u2028\u2029\x1c-\x1e]/) : [];
      if (body.length && body.at(-1) === '') body.pop();
      const start = lines.length + 1;
      const heading = `${'#'.repeat(Math.min(depth, 6))} ${block.title}`;
      const existingHeading = body[0]?.match(/^#{1,6}\s+(.+?)(?:\s+#+)?\s*$/)?.[1];
      // A generated block may already include its own title. Reuse that line
      // without losing the mapping to original body line numbers.
      if (existingHeading === block.title.trim()) body[0] = heading;
      else lines.push(heading, '');
      const bodyStart = lines.length + 1;
      lines.push(...body);
      sections.push({ block, start, bodyStart, end: lines.length, bodyLines: body.length });
      lines.push('');
      walk(block.id, depth + 1);
    }
  }
  walk(null, 1);
  return { text: lines.join('\n'), sections, lineCount: lines.length };
}

export function fullCommentTargets(blocks: Block[], start?: number, end?: number): Omit<Comment['targets'][number], 'quote'>[] {
  const full = fullDocument(blocks);
  if (start === undefined && end === undefined) return full.sections.map(({ block }) => ({ block_id: block.id, base_version: block.version }));
  if (!Number.isInteger(start) || !Number.isInteger(end) || start! < 1 || end! < start! || end! > full.lineCount) throw new Error('全文行范围无效，请重新选择');
  const targets = full.sections.filter(s => start! <= s.end && end! >= s.start).map(s => {
    const first = Math.max(start!, s.bodyStart), last = Math.min(end!, s.bodyStart + s.bodyLines - 1);
    return { block_id: s.block.id, base_version: s.block.version,
      ...(first <= last ? { start_line: first - s.bodyStart + 1, end_line: last - s.bodyStart + 1 } : {}) };
  });
  if (!targets.length) throw new Error('所选行没有模块内容，请重新选择');
  return targets;
}

export function fullSnapshotChanged(before: Snapshot, current: Snapshot) {
  return before.document.context_revision !== current.document.context_revision || before.blocks.length !== current.blocks.length
    || before.blocks.some(b => current.blocks.find(next => next.id === b.id)?.version !== b.version);
}

export function FullDocumentReport({ snapshot, loading, resolving = false, report, reviewError, onSelect, onOpenIssue, onResolve = () => {} }: {
  snapshot: Snapshot; loading: boolean; resolving?: boolean; report?: AgentRun; reviewError?: string;
  onSelect: (id: string) => void; onOpenIssue?: (issue: DocumentReviewIssue) => void; onResolve?: () => void;
}) {
  return <>
    {reviewError && <div className="prd-review-failure" role="alert"><strong>本次全文审核失败</strong><span>{reviewError}</span><small>请再次点击“审核全文”重试；旧审核结果已隐藏。</small></div>}
    {report?.result && <section className="prd-full-report" aria-label="全文审核意见"><header className="prd-full-report-header"><div><h3>全文审核意见</h3><small>审核基线 · 内容修订 {report.base_version}</small></div><button className="prd-primary" onClick={onResolve} disabled={resolving || loading || report.stale || report.apply_status === 'conflict' || !report.result.issues?.some(issue => issue.severity !== 'info')}>{resolving ? '正在修订，完成后待手动审核…' : '自动解决审核问题'}</button></header>
      {(report.stale || report.apply_status === 'conflict') && <p className="prd-conflict">正文或背景已变化，以下意见待复核。你可以继续编辑，准备好后手动审核。</p>}
      <p>{report.result.summary}</p>
      {report.result.issues?.length === 0 && <p>本次审核未发现需列出的问题。</p>}
      {report.result.issues?.map((issue, index) => <article key={index}>
        <button type="button" className="prd-review-issue-open" onClick={() => onOpenIssue?.(issue)} disabled={!onOpenIssue}>
          <strong>{({ blocker: '阻断问题', warning: '建议改进', info: '提示' })[issue.severity]}</strong>
          <span>{issue.description}</span><small>建议：{issue.suggestion}</small><em>定位原文并处理</em>
        </button>
        <div>{issue.block_ids.length ? issue.block_ids.map(id => <button key={id} onClick={() => onSelect(id)}>{snapshot.blocks.find(b => b.id === id)?.title ?? '相关模块'}</button>) : <small>涉及全文</small>}</div>
      </article>)}
    </section>}
  </>;
}

export function FullDocumentView({ snapshot, changed, active, loading, resolving = false, report, reviewError, onSelect, onOpenIssue, onRefresh, onReview, onResolve = () => {}, deferReport = false }: {
  snapshot: Snapshot; changed: boolean; active: boolean; loading: boolean; resolving?: boolean; report?: AgentRun; reviewError?: string;
  onSelect: (id: string) => void; onOpenIssue?: (issue: DocumentReviewIssue) => void; onRefresh: () => void; onReview: () => void; onResolve?: () => void;
  selected?: string; onRange?: (start: number, end: number) => void; onBack?: () => void;
  deferReport?: boolean;
}) {
  return <>
    {changed && <div className="prd-conflict">正文已更新，审核留待手动触发或进入下一阶段前进行。<button onClick={onRefresh} disabled={loading}>刷新全文</button></div>}
    <div className="prd-toolbar"><button className="prd-primary" onClick={onReview} disabled={active || loading || !snapshot.blocks.some(b => b.content.trim())}>{active ? '审核全文中…' : '审核全文'}</button><button onClick={onRefresh} disabled={loading}>刷新全文</button></div>
    {!deferReport && <FullDocumentReport snapshot={snapshot} loading={loading} resolving={resolving} report={report} reviewError={reviewError} onSelect={onSelect} onOpenIssue={onOpenIssue} onResolve={onResolve} />}
  </>;
}
