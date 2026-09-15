import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AgentRun, Block, Comment, Snapshot } from './types';

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

export function FullDocumentView({ snapshot, selected, changed, active, loading, report, onRange, onSelect, onRefresh, onBack, onReview }: {
  snapshot: Snapshot; selected: string; changed: boolean; active: boolean; loading: boolean; report?: AgentRun;
  onRange: (start: number, end: number) => void; onSelect: (id: string) => void; onRefresh: () => void; onBack: () => void; onReview: () => void;
}) {
  const [preview, setPreview] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const previewArea = useRef<HTMLElement>(null);
  const full = fullDocument(snapshot.blocks);
  useEffect(() => {
    const section = fullDocument(snapshot.blocks).sections.find(s => s.block.id === selected);
    if (!section) return;
    if (textarea.current) textarea.current.scrollTop = (section.start - 1) * 23.4;
    const target = previewArea.current?.querySelector<HTMLElement>(`[data-block-id="${CSS.escape(selected)}"]`);
    if (target && previewArea.current) previewArea.current.scrollTop += target.getBoundingClientRect().top - previewArea.current.getBoundingClientRect().top - 15;
  }, [selected, snapshot, preview]);
  return <>
    <div className="prd-editor-title"><h2>全文审核模式</h2><span>{snapshot.blocks.length} 个模块</span><button onClick={() => setPreview(!preview)}>{preview ? '文字视图' : '预览'}</button></div>
    <p className="prd-full-hint">按全文整体阅读和审核；选中文字可添加跨模块批注，点击目录可定位模块。</p>
    {changed && <div className="prd-conflict">正文或背景已更新，请更新全文后再批注或审核。<button onClick={onRefresh} disabled={loading}>更新全文</button></div>}
    {preview ? <article ref={previewArea} className="prd-preview prd-full-preview">{full.sections.map(section => <section key={section.block.id} data-block-id={section.block.id}><ReactMarkdown remarkPlugins={[remarkGfm]}>{full.text.split('\n').slice(section.start - 1, section.end).join('\n')}</ReactMarkdown></section>)}</article>
      : <div className="prd-code-editor prd-full-editor"><pre aria-hidden="true">{full.text.split('\n').map((_, i) => i + 1).join('\n')}</pre>
        <textarea ref={textarea} aria-label="PRD 全文" readOnly value={full.text} wrap="off"
          onScroll={e => { const gutter = e.currentTarget.previousElementSibling; if (gutter) gutter.scrollTop = e.currentTarget.scrollTop; }}
          onSelect={e => { const el = e.currentTarget; if (el.selectionStart !== el.selectionEnd) onRange(el.value.slice(0, el.selectionStart).split('\n').length, el.value.slice(0, el.selectionEnd - 1).split('\n').length); }} /></div>}
    <div className="prd-toolbar"><button className="prd-primary" onClick={onReview} disabled={active || loading || changed || !snapshot.blocks.some(b => b.content.trim())}>{active ? '审核全文中…' : '审核全文'}</button><button onClick={onRefresh} disabled={loading}>更新全文</button><button onClick={onBack}>返回分块编辑</button></div>
    {report?.result && <section className="prd-full-report" aria-label="全文审核意见"><h3>全文审核意见</h3><small>审核基线 · 文档 v{report.base_version}</small>
      {(report.stale || report.apply_status === 'conflict') && <p className="prd-conflict">正文或背景已变化，以下意见基于旧版本，请重新审核全文。</p>}
      <p>{report.result.summary}</p>
      {report.result.issues?.length === 0 && <p>本次审核未发现需列出的问题。</p>}
      {report.result.issues?.map((issue, index) => <article key={index}><strong>{({ blocker: '阻断问题', warning: '建议改进', info: '提示' })[issue.severity]}</strong><p>{issue.description}</p><p>建议：{issue.suggestion}</p><div>{issue.block_ids.length ? issue.block_ids.map(id => <button key={id} onClick={() => onSelect(id)}>{snapshot.blocks.find(b => b.id === id)?.title ?? '相关模块'}</button>) : <small>涉及全文</small>}</div></article>)}
    </section>}
  </>;
}
