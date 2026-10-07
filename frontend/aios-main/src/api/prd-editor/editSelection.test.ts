import { describe, expect, it } from 'vitest';
import { editGroups } from './editSelection';
import { markdownTables } from './TableDiff';
import type { Block } from './types';

describe('independent edit choices', () => {
  const blocks = [{ id: 'a', dependencies: [] }, { id: 'b', dependencies: ['a'] }, { id: 'c', dependencies: [] }] as unknown as Block[];
  const steps = ['a', 'c', 'a', 'b'].map(block_id => ({ action: 'replace_text', block_id }));
  it('keeps dependent and sequential changes together', () => {
    expect(editGroups(steps, blocks)).toEqual([[0, 2, 3], [1]]);
    expect(editGroups(steps, blocks, true)).toEqual([[0, 1, 2, 3]]);
  });
  it('keeps a structural move with all related order changes', () => {
    expect(editGroups([...steps, { action: 'move_block', block_id: 'c' }], blocks)).toEqual([[0, 1, 2, 3, 4]]);
  });
  it('parses escaped pipes and ignores fenced source', () => {
    expect(markdownTables('```md\n| A | B |\n| --- | --- |\n```\n| Name | Value |\n| --- | --- |\n| A\\|B | `x|y` |'))
      .toEqual([[['Name', 'Value'], ['A|B', '`x|y`']]]);
  });
});
