import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Block, Draft } from './types';

export function BlockEditor({ block, draft, blocks, onEdit, onRange }: {
  block: Block; draft: Draft; blocks: Block[]; onEdit: (patch: Partial<Draft>) => void;
  onRange: (start: number, end: number) => void;
}) {
  const [preview, setPreview] = useState(false);
  return <>
    <div className="prd-editor-title"><input aria-label="Block 标题" value={draft.title} onChange={e => onEdit({ title: e.target.value })} />
      <span>v{block.version}</span><button onClick={() => setPreview(!preview)}>{preview ? '编辑' : '预览'}</button></div>
    <label className="prd-field">本块写作要求<textarea rows={2} value={draft.instruction} onChange={e => onEdit({ instruction: e.target.value })} /></label>
    <details className="prd-dependencies"><summary>上游依赖 · {draft.dependencies.length} 项</summary>
      {blocks.filter(b => b.id !== block.id).map(b => <label key={b.id}><input type="checkbox" checked={draft.dependencies.includes(b.id)} onChange={e => onEdit({ dependencies: e.target.checked ? [...draft.dependencies, b.id] : draft.dependencies.filter(id => id !== b.id) })} />{b.title}</label>)}
    </details>
    {preview ? <article className="prd-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft.content || '此 Block 尚未编写。'}</ReactMarkdown></article> :
      <div className="prd-code-editor"><pre aria-hidden="true">{draft.content.split('\n').map((_, i) => i + 1).join('\n')}</pre>
        <textarea aria-label="Block 正文" spellCheck={false} value={draft.content} placeholder="在这里编写当前章节，或点击「生成当前块」。" wrap="off"
          onScroll={e => { const gutter = e.currentTarget.previousElementSibling; if (gutter) gutter.scrollTop = e.currentTarget.scrollTop; }}
          onChange={e => onEdit({ content: e.target.value })} onSelect={e => {
            const el = e.currentTarget;
            if (el.selectionStart !== el.selectionEnd) onRange(el.value.slice(0, el.selectionStart).split('\n').length, el.value.slice(0, el.selectionEnd - 1).split('\n').length);
          }} /></div>}
  </>;
}
