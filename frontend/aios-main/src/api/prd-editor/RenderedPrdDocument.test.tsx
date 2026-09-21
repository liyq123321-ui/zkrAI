// @vitest-environment jsdom
import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import mermaid from 'mermaid';
import { locateReadingSelection, RenderedPrdDocument } from './RenderedPrdDocument';
import type { Block } from './types';

vi.mock('mermaid', () => ({ default: {
  initialize: vi.fn(),
  render: vi.fn(async () => ({ svg: '<svg data-testid="rendered-diagram"></svg>' })),
} }));

beforeAll(() => { HTMLElement.prototype.scrollIntoView = vi.fn(); });

const block: Block = {
  id: 'b1', document_id: 'd1', title: '范围', parent_id: null, order: 0,
  status: 'ready', version: 1, content: '第一行重复文本\n第二行重复文本', summary: '', instruction: '', dependencies: [], fragment_version: null,
};

describe('locateReadingSelection', () => {
  it('locates the selected occurrence and its source line', () => {
    expect(locateReadingSelection(block, '重复文本', 1)).toMatchObject({
      blockId: 'b1', quote: '重复文本', startLine: 2, endLine: 2,
    });
  });
});

afterEach(cleanup);

it('reuses a finished Mermaid diagram when an unchanged snapshot arrives', async () => {
  const diagramBlock = { ...block, content: '```mermaid\nflowchart TD\n  A --> B\n```' };
  const props = {
    comments: [], selectedBlockId: diagramBlock.id,
    onSelectBlock: vi.fn(), onCreateComment: vi.fn(), onEditSource: vi.fn(), onOpenComment: vi.fn(),
  };
  const view = render(<RenderedPrdDocument {...props} blocks={[diagramBlock]} />);
  await waitFor(() => expect(vi.mocked(mermaid.render)).toHaveBeenCalledTimes(1));

  view.rerender(<RenderedPrdDocument {...props} blocks={[{ ...diagramBlock }]} />);

  await waitFor(() => expect(vi.mocked(mermaid.render)).toHaveBeenCalledTimes(1));
});
