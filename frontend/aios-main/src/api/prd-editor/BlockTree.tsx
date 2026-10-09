import { useEffect, useRef } from 'react';
import type { Block } from './types';
import { statusNames } from './types';

export function BlockTree({ blocks, selected, checked, onSelect, onCheck }: {
  blocks: Block[]; selected: string; checked: Set<string>; onSelect: (id: string) => void; onCheck: (id: string) => void;
}) {
  const selectedItemRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const item = selectedItemRef.current;
      const scroller = item?.closest<HTMLElement>('.prd-directory');
      if (!item || !scroller) return;
      const itemRect = item.getBoundingClientRect(), scrollerRect = scroller.getBoundingClientRect();
      const rowHeight = Math.max(itemRect.height, 34);
      // Keep the active directory entry near the fourth visible row. Native
      // scroll clamping naturally keeps the first few entries at the top.
      const desiredTop = scrollerRect.top + rowHeight * 3;
      const top = Math.max(0, scroller.scrollTop + itemRect.top - desiredTop);
      if (typeof scroller.scrollTo === 'function') scroller.scrollTo({ top, behavior: 'auto' });
      else scroller.scrollTop = top;
    });
    return () => cancelAnimationFrame(frame);
  }, [selected]);

  function branch(parent: string | null) {
    return <ul>{blocks.filter(b => b.parent_id === parent).map(b => <li key={b.id}>
      <div ref={selected === b.id ? selectedItemRef : undefined} data-block-id={b.id} className={selected === b.id ? 'is-selected' : ''}>
        <input type="checkbox" aria-label={`选择 ${b.title}`} checked={checked.has(b.id)} onChange={() => onCheck(b.id)} />
        {/* 选中态下再点一次即取消选中；取消后回到「无章节被查看」的状态。
            注意：这里只改变「在看哪一章」，不改变命令的执行范围——范围只由勾选框（和指令中点名的章节）决定。 */}
        <button aria-pressed={selected === b.id} onClick={() => onSelect(selected === b.id ? '' : b.id)}><span className={`prd-dot is-${b.status}`} /><span>{b.title}</span><small>{statusNames[b.status]}</small></button>
      </div>{branch(b.id)}</li>)}</ul>;
  }
  return <nav aria-label="Block 目录" className="prd-tree">{branch(null)}</nav>;
}

export function BlockPlanMap({ blocks, selected, onSelect, interactive = true, sourceLinked = false }: {
  blocks: Block[]; selected: string; onSelect: (id: string) => void; interactive?: boolean; sourceLinked?: boolean;
}) {
  function branch(parent: string | null) {
    const children = blocks.filter(b => b.parent_id === parent);
    if (!children.length) return null;
    return <div className="prd-map-children">{children.map(b => {
      const content = <><span className={`prd-dot is-${b.status}`} /><span className="prd-map-node-copy">{b.title}<small>{statusNames[b.status]}{b.dependencies.length ? ` · ${b.dependencies.length} 项依赖` : ''}</small></span>{sourceLinked && <span className="prd-map-source-action">定位原文</span>}</>;
      const excerpt = (b.summary || b.content).replace(/\s+/g, ' ').trim().slice(0, 100);
      const title = `${b.id}\n${b.dependencies.length ? '依赖：' + b.dependencies.map(id => blocks.find(x => x.id === id)?.title).join('、') : '无上游依赖'}${excerpt ? `\n${excerpt}` : ''}`;
      return <div className="prd-map-branch" key={b.id}>
        {interactive
          ? <button className={`prd-map-node ${selected === b.id ? 'is-selected' : ''}`} onClick={() => onSelect(b.id)} title={title}>{content}</button>
          : <div className={`prd-map-node ${selected === b.id ? 'is-selected' : ''}`} title={title}>{content}</div>}
        {branch(b.id)}
      </div>;
    })}</div>;
  }
  const current = blocks.find(b => b.id === selected);
  return <><div className={`prd-map${sourceLinked ? ' is-source-linked' : ''}`} aria-label={sourceLinked ? 'PRD 思维导图' : interactive ? '水平文档规划图' : '文档规划缩略图'}><div className="prd-map-root">PRD</div>{branch(null)}</div>
    {interactive && current && current.dependencies.length > 0 && <div className="prd-map-dependencies" aria-label="当前 Block 依赖关系"><span>必要上游</span>{current.dependencies.map(id => <button key={id} onClick={() => onSelect(id)}>{blocks.find(b => b.id === id)?.title}</button>)}<span>→</span><strong>{current.title}</strong></div>}</>;
}
