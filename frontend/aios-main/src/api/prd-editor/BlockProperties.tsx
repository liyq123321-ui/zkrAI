import type { Block, Draft } from './types';

export function BlockProperties({ block, draft, blocks, onEdit }: {
  block: Block;
  draft: Draft;
  blocks: Block[];
  onEdit: (patch: Partial<Draft>) => void;
}) {
  return <>
    <div className="prd-editor-title"><input aria-label="Block 标题" value={draft.title} onChange={event => onEdit({ title: event.target.value })} /><span>v{block.version}</span></div>
    <details className="prd-dependencies"><summary>上游依赖 · {draft.dependencies.length} 项</summary>
      {blocks.filter(item => item.id !== block.id).map(item => <label key={item.id}><input type="checkbox" checked={draft.dependencies.includes(item.id)} onChange={event => onEdit({ dependencies: event.target.checked ? [...draft.dependencies, item.id] : draft.dependencies.filter(id => id !== item.id) })} />{item.title}</label>)}
    </details>
  </>;
}
