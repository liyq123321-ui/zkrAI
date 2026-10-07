import type { Comment, Block } from './types';
export function BlockCommentPanel({ comments, blocks, current, focusedCommentId, focusOnly = false, showAll = false, feedback, onFeedback, start, end, onRange, count, onAdd, onRevise, onResolve, wholeDocument, onWholeDocument }: {
  comments: Comment[]; blocks: Block[]; current: string; feedback: string; onFeedback: (value: string) => void;
  focusedCommentId?: string | null;
  focusOnly?: boolean; showAll?: boolean;
  start: string; end: string; onRange: (start: string, end: string) => void; count: number;
  onAdd: () => void; onRevise: (comment: Comment) => void; onResolve: (comment: Comment) => void;
  wholeDocument: boolean; onWholeDocument: (value: boolean) => void;
}) {
  const visible = comments.filter(comment => focusOnly ? comment.id === focusedCommentId
    : showAll || comment.targets.some(target => target.block_id === current) || comment.targets.length === blocks.length)
    .sort((left, right) => Number(left.resolved) - Number(right.resolved));
  return <section className="prd-comments"><h3>{focusOnly ? '正文批注' : 'AI 指令与批注'}</h3>{!focusOnly && <><p>默认批注当前 Block；选中文字可定位该 Block 内的行范围，目录复选框可批注多个 Block。</p>
    <textarea aria-label="修改意见" value={feedback} onChange={e => onFeedback(e.target.value)} placeholder="描述希望调整的内容，AI 仅修改指定 Block…" rows={5} />
    <label className="prd-whole-comment"><input aria-label="全文批注" type="checkbox" checked={wholeDocument} onChange={event => onWholeDocument(event.target.checked)} /><span><strong>全文批注</strong><small>勾选后覆盖全部 Block</small></span></label>
    <div className={`prd-line-range ${wholeDocument ? 'is-disabled' : ''}`} aria-disabled={wholeDocument}><label>起始行<input type="number" min={1} value={start} disabled={wholeDocument} onChange={e => onRange(e.target.value, end)} /></label><label>结束行<input type="number" min={1} value={end} disabled={wholeDocument} onChange={e => onRange(start, e.target.value)} /></label><button disabled={wholeDocument} onClick={() => onRange('', '')}>整块</button></div>
    <button disabled={!feedback.trim()} onClick={onAdd}>{wholeDocument ? '添加全文批注' : `添加批注 · ${count || 1} 个 Block`}</button></>}
    <div className="prd-comment-list">{visible.length === 0 && <p className="prd-comments-empty">暂无批注</p>}{visible.map(c => {
      const deletion = c.targets.some(target => target.action === 'delete');
      return <article id={`prd-comment-${c.id}`} key={c.id} className={[c.resolved && 'is-resolved', c.id === focusedCommentId && 'is-focused'].filter(Boolean).join(' ')}>
      <p>{deletion && <span className="prd-comment-kind">划掉待删除</span>}{c.text}</p><small>{c.targets.map(t => `${blocks.find(b => b.id === t.block_id)?.title ?? t.block_id} · v${t.base_version}${t.start_line ? ` · L${t.start_line}–${t.end_line}` : ''}`).join('；')}</small>
      <div><button onClick={() => onRevise(c)} disabled={c.resolved}>{deletion ? '按批注删除原文' : `按此批注修改 ${c.targets.length > 1 ? '各块' : ''}`}</button><button onClick={() => onResolve(c)}>{c.resolved ? '重新打开' : '解决批注'}</button></div>
    </article>;
    })}</div>
  </section>;
}
