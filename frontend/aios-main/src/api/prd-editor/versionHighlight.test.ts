import { describe, expect, it } from 'vitest';
import {
  INSERT_HREF, diffTokens, escapeMarkdown, highlightByBlock, markdownWithVersionDiff, splitCells, tokenize,
} from './versionHighlight';

/**
 * 高亮的硬约束：**表格不能断**。
 *
 * 表格行的 `|` 一旦被 `~~…~~` 整行包住，这一行就不再是表格行，markdown 会把整张表
 * 切成碎片。所以变化必须落到片段/单元格这一级。
 */

const ROW = '| 协同 | 消息中心、待办、评审组件框架 | FR-4.1–4.5 |';

// 用户在真实数据上的那次改动：把「协同」整行下移并改名为「协作」。
const BEFORE = [
  '底座是与具体业务领域无关的多 Agent 协作运行时。',
  '',
  '| 底座模块 | 通用机制职责 | 对应 FR |',
  '| --- | --- | --- |',
  '| 观测与治理 | 日志 Schema、运行时间线 | FR-5.1–5.5 |',
  '| 协同 | 消息中心、待办、评审组件框架 | FR-4.1–4.5 |',
  '| 插件框架 | 知识形成范式、应用包加载器 | FR-1.18 |',
].join('\n');

const AFTER = [
  '底座是与具体业务领域无关的多 Agent 协作运行时。',
  '',
  '| 底座模块 | 通用机制职责 | 对应 FR |',
  '| --- | --- | --- |',
  '| 观测与治理 | 日志 Schema、运行时间线 | FR-5.1–5.5 |',
  '| 插件框架 | 知识形成范式、应用包加载器 | FR-1.18 |',
  '| 协作 | 消息中心、待办、评审组件框架 | FR-4.1–4.5 |',
].join('\n');

describe('token 化与行内 diff', () => {
  it('中文逐字切分，英文单词整体成 token', () => {
    expect(tokenize('协同')).toEqual(['协', '同']);
    expect(tokenize('FR-4.1')).toEqual(['FR', '-', '4', '.', '1']);
    expect(tokenize('')).toEqual([]);
  });

  it('只标出真正变化的那一个字，而不是两个无关的词', () => {
    expect(diffTokens('协同', '协作')).toEqual([
      { kind: 'same', text: '协' },
      { kind: 'remove', text: '同' },
      { kind: 'add', text: '作' },
    ]);
  });
});

describe('markdown 转义', () => {
  it('转义会改写结构的记号', () => {
    expect(escapeMarkdown('a*b|c')).toBe('a\\*b\\|c');
  });

  it('拆分单元格时不会切在已转义的管道上', () => {
    expect(splitCells('| a\\|b | c |')).toEqual(['', ' a\\|b ', ' c ', '']);
  });
});

describe('正文高亮', () => {
  it('未改动的正文原样返回', () => {
    expect(markdownWithVersionDiff(BEFORE, BEFORE)).toBe(BEFORE);
  });

  it('同一行内改一个词：改动落在单元格内部，表格结构不变', () => {
    const result = markdownWithVersionDiff(ROW, ROW.replace('协同', '协作'));
    expect(result).toContain('~~同~~');
    expect(result).toContain(`[作](${INSERT_HREF})`);
    expect(result.startsWith('|')).toBe(true);
    expect(result.endsWith('|')).toBe(true);
    expect(splitCells(result)).toHaveLength(5); // 首尾空片段 + 3 个单元格
  });

  it('整行删除的表格行逐单元格标注，而不是把整行包起来', () => {
    const result = markdownWithVersionDiff(BEFORE, BEFORE.replace(`\n${ROW}`, ''));
    expect(result).toContain('~~协同~~');
    expect(result).not.toContain(`~~${ROW}~~`);
    for (const line of result.split('\n').filter(line => line.includes('协同'))) {
      expect(line.trim().startsWith('|')).toBe(true);
      expect(line.trim().endsWith('|')).toBe(true);
    }
  });

  it('用户那次「下移并改名」的改动：删的行标红、新增的行高亮，表格仍是完整的表', () => {
    const result = markdownWithVersionDiff(BEFORE, AFTER);
    const lines = result.split('\n');
    const tableLines = lines.filter(line => line.trim().startsWith('|'));
    // 表格行的 `|` 结构必须完整：删掉的那行仍留在表里（便于看出它去哪了），新增行补在末尾。
    expect(tableLines).toHaveLength(6);
    for (const line of tableLines) {
      expect(splitCells(line)).toHaveLength(5); // 首尾空片段 + 3 个单元格
    }
    expect(result).toContain('~~协同~~');
    expect(result).toContain(`[协作](${INSERT_HREF})`);
    expect(result).toContain('| 观测与治理 | 日志 Schema、运行时间线 | FR-5.1–5.5 |');
    expect(result).toContain('| 插件框架 | 知识形成范式、应用包加载器 | FR-1.18 |');
  });

  it('新增的非表格行整行高亮', () => {
    const result = markdownWithVersionDiff('第一段。', '第一段。\n\n第二段是新的。');
    expect(result).toContain(`[第二段是新的。](${INSERT_HREF})`);
  });

  it('新增内容里的 markdown 记号被转义，不会变成真实结构', () => {
    const result = markdownWithVersionDiff('旧。', '旧。\n\n| a | b |\n| --- | --- |\n| x*y | z |');
    expect(result).toContain('\\*');
    expect(result).not.toContain('| x*y |');
  });
});

describe('逐块差异地图', () => {
  it('区分「改动」「新增」并跳过未改动的块', () => {
    const previous = new Map([['a', '旧内容'], ['b', '一样']]);
    const current = new Map([['a', '新内容'], ['b', '一样'], ['c', '全新章节']]);
    const { highlighted, added } = highlightByBlock(previous, current);
    expect([...highlighted.keys()]).toEqual(['a']);
    expect([...added]).toEqual(['c']);
  });
});
