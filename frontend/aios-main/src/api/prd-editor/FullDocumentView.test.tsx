// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { fullDocument, fullCommentTargets, FullDocumentView } from './FullDocumentView';
import type { Block, Snapshot, AgentRun } from './types';
const blocks = [
  { id: 'a', title: 'Parent', parent_id: null, order: 0, version: 3, content: 'first\r\nsecond\r\n' },
  { id: 'b', title: 'Child', parent_id: 'a', order: 1, version: 5, content: 'third\nfourth' },
  { id: 'c', title: 'Empty', parent_id: null, order: 2, version: 1, content: '' },
] as Block[];
afterEach(cleanup);
it('reuses a block heading without duplicating it or shifting its comment lines', () => {
  const titled = [{ ...blocks[0], title: 'Parent', content: '## Parent\n\nBody text' }];
  expect(fullDocument(titled).text).toBe('# Parent\n\nBody text\n');
  expect(fullCommentTargets(titled, 3, 3)).toEqual([{ block_id: 'a', base_version: 3, start_line: 3, end_line: 3 }]);
});
it('maps selections across headings and modules back to original block lines and versions', () => {
  expect(fullDocument(blocks).text).toContain('# Parent\n\nfirst\nsecond\n\n## Child\n\nthird\nfourth');
  expect(fullCommentTargets(blocks, 4, 8)).toEqual([
    { block_id: 'a', base_version: 3, start_line: 2, end_line: 2 },
    { block_id: 'b', base_version: 5, start_line: 1, end_line: 1 },
  ]);
  expect(fullCommentTargets(blocks, 6, 6)).toEqual([{ block_id: 'b', base_version: 5 }]);
  expect(fullCommentTargets(blocks)).toHaveLength(3);
  expect(() => fullCommentTargets(blocks, 100, 101)).toThrow('范围无效');
  expect(() => fullCommentTargets(blocks, 2.5, 3)).toThrow('范围无效');
});
it('renders holistic findings and an explicit warning for an outdated report', () => {
  const snapshot = { blocks, document: { revision: 9 } } as Snapshot;
  const report = { type: 'document_review', base_version: 8, stale: true, result: { summary: '整体术语不一致', issues: [{ severity: 'warning', block_ids: ['a', 'b'], description: '章节之间存在矛盾', suggestion: '统一范围' }] } } as AgentRun;
  render(<FullDocumentView snapshot={snapshot} selected="a" changed={false} active={false} loading={false} report={report} onRange={vi.fn()} onSelect={vi.fn()} onRefresh={vi.fn()} onBack={vi.fn()} onReview={vi.fn()} />);
  expect(screen.getByText('整体术语不一致')).toBeTruthy();
  expect(screen.getByText('章节之间存在矛盾')).toBeTruthy();
  expect(screen.getByText(/以下意见基于旧版本/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Parent' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Child' })).toBeTruthy();
});
