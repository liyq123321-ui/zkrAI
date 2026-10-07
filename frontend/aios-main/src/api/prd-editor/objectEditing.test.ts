import { expect, it } from 'vitest';
import { alignTableRows } from './TableDiff';
import { discussionIssue } from './discussionContext';
import { editGroups } from './editSelection';
import type { Block } from './types';

it('aligns business rows after sorting and rejects duplicate identities', () => {
  const old = [['编号', '值'], ['FR1', 'P0'], ['FR2', 'P2']];
  expect(alignTableRows(old, [['编号', '值'], ['FR2', 'P2'], ['FR1', 'P1']], '编号'))
    .toEqual([{ key: 'FR1', before: 1, after: 2 }, { key: 'FR2', before: 2, after: 1 }]);
  expect(alignTableRows(old, [['编号', '值'], ['FR1', 'P2'], ['FR1', 'P1']], '编号')).toBeNull();
});

it('invalidates stale discussion targets rather than silently following selection', () => {
  const target = { blockId: 'a', version: 2, quote: 'hello', pinned: true, constraints: 'keep amounts' };
  expect(discussionIssue(target, [{ id: 'a', version: 2, content: 'hello world' }] as Block[])).toBe('');
  expect(discussionIssue(target, [{ id: 'a', version: 3, content: 'hello world' }] as Block[])).toContain('新版本');
  expect(discussionIssue(target, [])).toContain('删除');
});

it('splits independent cells and paragraphs while retaining sequential dependencies', () => {
  const blocks = [{ id: 'a', dependencies: [] }] as unknown as Block[];
  expect(editGroups([{ action: 'table_set', block_id: 'a', table_index: 0, row: 1, column: 'A' }, { action: 'table_set', block_id: 'a', table_index: 0, row: 2, column: 'A' }], blocks)).toEqual([[0], [1]]);
  expect(editGroups([{ action: 'replace_text', block_id: 'a', old_text: 'A', new_text: 'B' }, { action: 'replace_text', block_id: 'a', old_text: 'B', new_text: 'C' }], blocks)).toEqual([[0, 1]]);
});
