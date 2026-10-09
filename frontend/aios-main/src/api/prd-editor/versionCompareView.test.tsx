// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { RenderedPrdDocument } from './RenderedPrdDocument';
import type { Block } from './types';

vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), render: vi.fn() } }));

// jsdom 没实现 scrollIntoView，组件挂载时会调用它。
beforeAll(() => { HTMLElement.prototype.scrollIntoView = vi.fn(); });

const make = (id: string, title: string, content: string): Block => ({
  id, document_id: 'd1', title, parent_id: null, order: Number(id.slice(1)),
  status: 'ready', version: 1, content, summary: '', instruction: '', dependencies: [], fragment_version: null,
});

const TABLE_OLD = '| 底座模块 | 职责 |\n| --- | --- |\n| 观测与治理 | 日志 |\n| 协同 | 消息中心 |';
const TABLE_NEW = '| 底座模块 | 职责 |\n| --- | --- |\n| 观测与治理 | 日志 |\n| 协作 | 消息中心 |';

const CURRENT = [make('b1', '第一章', TABLE_NEW), make('b2', '新增章节', '全新内容。')];
const BASELINE = [make('b1', '第一章', TABLE_OLD)];

const props = {
  comments: [], selectedBlockId: 'b1', navSeq: 0,
  onSelectBlock: vi.fn(), onCreateComment: vi.fn(), onEditSource: vi.fn(), onOpenComment: vi.fn(),
};

afterEach(cleanup);

describe('对比上一版', () => {
  it('没有基线时就是普通图文阅读：不出现任何差异标记', () => {
    const { container } = render(<RenderedPrdDocument {...props} blocks={CURRENT} />);
    expect(container.querySelector('.prd-diff-banner')).toBeNull();
    expect(container.querySelector('.ff-diff-removed')).toBeNull();
    expect(container.querySelector('.ff-diff-added')).toBeNull();
    expect(container.querySelector('.ff-diff-badge')).toBeNull();
  });

  it('同一行内只改了一个词：只把变化的那几个字标出来', () => {
    const { container } = render(<RenderedPrdDocument {...props} blocks={CURRENT}
      compareBaseline={{ version: 10, blocks: BASELINE }} />);
    expect(screen.getByText(/对比上一版 v10/)).toBeTruthy();

    const removed = [...container.querySelectorAll('del.ff-diff-removed')].map(node => node.textContent).join('');
    const added = [...container.querySelectorAll('ins.ff-diff-added')].map(node => node.textContent).join('');
    expect(removed).toBe('同');
    expect(added).toBe('作');
    // 上一版没有的章节整体标为新增
    expect(container.querySelector('.is-diff-added')?.getAttribute('data-block-id')).toBe('b2');
  });

  it('整行移动（顺序变了）时整行标删、整行标增，表格依然完整', () => {
    const movedOld = ['| 底座模块 | 职责 |', '| --- | --- |', '| 观测与治理 | 日志 |',
      '| 协同 | 消息中心 |', '| 插件框架 | 加载器 |'].join('\n');
    const movedNew = ['| 底座模块 | 职责 |', '| --- | --- |', '| 观测与治理 | 日志 |',
      '| 插件框架 | 加载器 |', '| 协作 | 消息中心 |'].join('\n');
    const { container } = render(<RenderedPrdDocument {...props} blocks={[make('b1', '第一章', movedNew)]}
      compareBaseline={{ version: 10, blocks: [make('b1', '第一章', movedOld)] }} />);
    const removed = [...container.querySelectorAll('del.ff-diff-removed')].map(node => node.textContent).join('');
    const added = [...container.querySelectorAll('ins.ff-diff-added')].map(node => node.textContent).join('');
    expect(removed).toContain('协同');
    expect(added).toContain('协作');
    const table = container.querySelector('table');
    expect(table).toBeTruthy();
    const rows = [...table!.querySelectorAll('tbody tr')];
    expect(rows).toHaveLength(4);
    // 只数行/列还不够：把整行包进 `~~…~~` 时它仍含 `|`，remark-gfm 照样当表格行，
    // 行数、列数都不变，但整行文字被**挤进第一个单元格**、第二格空着，管道符还当正文显示。
    // 所以必须断言高亮是**落在各自单元格里**的。
    const removedCells = [...table!.querySelectorAll('td del.ff-diff-removed')].map(node => node.textContent);
    expect(removedCells).toEqual(['协同', '消息中心']);
    for (const row of rows) expect(row.querySelectorAll('td')).toHaveLength(2);
  });

  it('注入高亮之后表格仍然是完整的表格 —— 没有被拆成碎片', () => {
    const { container } = render(<RenderedPrdDocument {...props} blocks={CURRENT}
      compareBaseline={{ version: 10, blocks: BASELINE }} />);
    const table = container.querySelector('table');
    expect(table).toBeTruthy();
    expect(table!.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(table!.querySelectorAll('th')).toHaveLength(2);
  });

  it('不再对比时回到普通渲染', () => {
    const view = render(<RenderedPrdDocument {...props} blocks={CURRENT}
      compareBaseline={{ version: 10, blocks: BASELINE }} />);
    expect(view.container.querySelector('.ff-diff-added')).toBeTruthy();
    view.rerender(<RenderedPrdDocument {...props} blocks={CURRENT} compareBaseline={null} />);
    expect(view.container.querySelector('.ff-diff-added')).toBeNull();
    expect(view.container.querySelector('.prd-diff-banner')).toBeNull();
  });
});
