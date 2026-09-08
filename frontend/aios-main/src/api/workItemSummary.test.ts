import { describe, expect, it } from 'vitest';
import { validWorkItemSummary } from './workItemSummary';

describe('validWorkItemSummary', () => {
  it('accepts a trimmed single-line summary of up to 20 Unicode characters', () => {
    expect(validWorkItemSummary('本地温度换算器')).toBe('本地温度换算器');
    expect(validWorkItemSummary('一二三四五六七八九十一二三四五六七八九十')).toBe('一二三四五六七八九十一二三四五六七八九十');
  });

  it.each([
    undefined,
    '',
    '  ',
    ' 未修剪摘要',
    '未修剪摘要 ',
    '第一行\n第二行',
    '第一行\r第二行',
    '第一行 第二行',
    '第一行 第二行',
    '一二三四五六七八九十一二三四五六七八九十一',
  ])('rejects invalid runtime summary %j', (summary) => {
    expect(validWorkItemSummary(summary)).toBeNull();
  });
});
