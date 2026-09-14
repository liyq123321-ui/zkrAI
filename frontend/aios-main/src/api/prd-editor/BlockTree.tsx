import type { Block } from './types';
import { statusNames } from './types';

export function BlockTree({ blocks, selected, checked, onSelect, onCheck }: {
  blocks: Block[]; selected: string; checked: Set<string>; onSelect: (id: string) => void; onCheck: (id: string) => void;
}) {
  function branch(parent: string | null) {
    return <ul>{blocks.filter(b => b.parent_id === parent).map(b => <li key={b.id}>
      <div className={selected === b.id ? 'is-selected' : ''}>
        <input type="checkbox" aria-label={`选择 ${b.title}`} checked={checked.has(b.id)} onChange={() => onCheck(b.id)} />
        <button onClick={() => onSelect(b.id)}><span className={`prd-dot is-${b.status}`} /><span>{b.title}</span><small>{statusNames[b.status]}</small></button>
      </div>{branch(b.id)}</li>)}</ul>;
  }
  return <nav aria-label="Block 目录" className="prd-tree">{branch(null)}</nav>;
}

export function BlockPlanMap({ blocks, selected, onSelect, interactive = true, maxDepth }: {
  blocks: Block[]; selected: string; onSelect: (id: string) => void; interactive?: boolean; maxDepth?: number;
}) {
  function branch(parent: string | null, depth = 0) {
    if (maxDepth !== undefined && depth >= maxDepth) return null;
    const children = blocks.filter(b => b.parent_id === parent);
    if (!children.length) return null;
    return <div className="prd-map-children">{children.map(b => {
      const content = <><span className={`prd-dot is-${b.status}`} /><span>{b.title}<small>{statusNames[b.status]}{b.dependencies.length ? ` · ${b.dependencies.length} 项依赖` : ''}</small></span></>;
      const title = `${b.id}\n${b.dependencies.length ? '依赖：' + b.dependencies.map(id => blocks.find(x => x.id === id)?.title).join('、') : '无上游依赖'}`;
      return <div className="prd-map-branch" key={b.id}>
        {interactive
          ? <button className={`prd-map-node ${selected === b.id ? 'is-selected' : ''}`} onClick={() => onSelect(b.id)} title={title}>{content}</button>
          : <div className={`prd-map-node ${selected === b.id ? 'is-selected' : ''}`} title={title}>{content}</div>}
        {branch(b.id, depth + 1)}
      </div>;
    })}</div>;
  }
  const current = blocks.find(b => b.id === selected);
  return <><div className="prd-map" aria-label={interactive ? '水平文档规划图' : '文档规划缩略图'}><div className="prd-map-root">PRD</div>{branch(null)}</div>
    {interactive && current && current.dependencies.length > 0 && <div className="prd-map-dependencies" aria-label="当前 Block 依赖关系"><span>必要上游</span>{current.dependencies.map(id => <button key={id} onClick={() => onSelect(id)}>{blocks.find(b => b.id === id)?.title}</button>)}<span>→</span><strong>{current.title}</strong></div>}</>;
}
