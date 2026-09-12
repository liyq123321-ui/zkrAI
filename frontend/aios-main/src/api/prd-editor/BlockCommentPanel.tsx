import type { Comment, Block } from './types';
export function BlockCommentPanel({ comments, blocks, current, feedback, onFeedback, start, end, onRange, count, onAdd, onRevise, onResolve }: {
  comments: Comment[]; blocks: Block[]; current: string; feedback: string; onFeedback: (value: string) => void;
  start: string; end: string; onRange: (start: string, end: string) => void; count: number;
  onAdd: () => void; onRevise: (comment: Comment) => void; onResolve: (comment: Comment) => void;
}) {
  return <section className="prd-comments"><h3>AI 指令与批注</h3><p>选中文字可定位行范围；勾选目录可批注多个块。</p>
    <textarea aria-label="修改意见" value={feedback} onChange={e => onFeedback(e.target.value)} placeholder="描述希望调整的内容，AI 仅修改指定 Block…" rows={5} />
    <div className="prd-line-range"><label>起始行<input type="number" min={1} value={start} onChange={e => onRange(e.target.value, end)} /></label><label>结束行<input type="number" min={1} value={end} onChange={e => onRange(start, e.target.value)} /></label><button onClick={() => onRange('', '')}>整块</button></div>
    <button disabled={!feedback.trim()} onClick={onAdd}>添加批注 · {count || 1} 个块</button>
    <div className="prd-comment-list">{comments.filter(c => c.targets.some(t => t.block_id === current)).map(c => <article key={c.id} className={c.resolved ? 'is-resolved' : ''}>
      <p>{c.text}</p><small>{c.targets.map(t => `${blocks.find(b => b.id === t.block_id)?.title ?? t.block_id} · v${t.base_version}${t.start_line ? ` · L${t.start_line}–${t.end_line}` : ''}`).join('；')}</small>
      <div><button onClick={() => onRevise(c)} disabled={c.resolved}>按此批注修改 {c.targets.length > 1 ? '各块' : ''}</button><button onClick={() => onResolve(c)}>{c.resolved ? '重新打开' : '解决批注'}</button></div>
    </article>)}</div>
  </section>;
}
