import { describe, expect, it } from 'vitest';
import type { Block } from './types';
import { alignVersionBlocks, compareVersionBlocks, versionBlockDepth } from './versionDiffModel';

const block = (id: string, order: number, content = id, parent_id: string | null = null): Block => ({
  id, document_id: 'doc', title: id, parent_id, order, content, status: 'ready', version: 1,
  summary: '', instruction: '', dependencies: [], fragment_version: null,
});

describe('whole document version directory comparison', () => {
  it('marks additions, removals, content changes and structural moves', () => {
    const old = [block('A', 0), block('B', 1), block('C', 2), block('D', 3)];
    const next = [block('A', 0, 'updated'), block('E', 1), block('D', 2), block('B', 3)];
    expect(Object.fromEntries(compareVersionBlocks(old, next).map(item => [item.id, item.change]))).toEqual({
      A: 'content', B: 'structure', C: 'removed', D: 'structure', E: 'added',
    });
  });

  it('does not treat insertion alone as a move, and follows parent depth', () => {
    const old = [block('A', 0), block('B', 1)];
    const next = [block('A', 0), block('X', 1), block('B', 2)];
    expect(compareVersionBlocks(old, next).find(item => item.id === 'B')?.change).toBe('same');
    expect(versionBlockDepth(block('B', 2, 'B', 'A'), next)).toBe(1);
  });

  it('aligns surviving chapters and leaves a blank opposite additions and removals', () => {
    const rows = alignVersionBlocks(
      [block('A', 0), block('B', 1), block('C', 2)],
      [block('A', 0), block('X', 1), block('C', 2)],
    );
    expect(rows.map(row => [row.before?.id ?? null, row.after?.id ?? null])).toEqual([
      ['A', 'A'], ['B', null], [null, 'X'], ['C', 'C'],
    ]);
  });
});
