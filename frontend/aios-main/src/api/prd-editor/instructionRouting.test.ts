import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { isPrdRevisionInstruction, locateScreenshotInPrd, namedPrdTargets, prdEditScope } from './instructionRouting';

describe('global instruction routing with an open PRD', () => {
  it('uses the open PRD for an editing instruction without repeating PRD', () => {
    expect(isPrdRevisionInstruction('把项目目标部分补充量化指标', true)).toBe(true);
    expect(isPrdRevisionInstruction('修改当前 PRD 的项目目标', false)).toBe(true);
    expect(isPrdRevisionInstruction('产品概念设计里的信息结构图和产品结构图请重新生成', true)).toBe(true);
    expect(isPrdRevisionInstruction('请重画这一段的配图', true)).toBe(true);
  });

  it('honors an explicit command for another area', () => {
    expect(isPrdRevisionInstruction('修改后端接口的错误处理', true)).toBe(false);
    expect(isPrdRevisionInstruction('修改前端 PRD 编辑器代码', true)).toBe(false);
    expect(isPrdRevisionInstruction('修改 PRD 中的前端部分', true)).toBe(true);
    expect(isPrdRevisionInstruction('打开看板', true)).toBe(false);
    expect(isPrdRevisionInstruction('生成PRD', true)).toBe(false);
  });

  it('edits both named diagrams without mistakenly selecting their parent', () => {
    const blocks = [
      { id: 'parent', title: '产品概念设计', parent_id: null },
      { id: 'info', title: '信息结构图', parent_id: 'parent' },
      { id: 'product', title: '产品结构图', parent_id: 'parent' },
    ];
    expect(namedPrdTargets(blocks, '产品概念设计里的信息结构图和产品结构图请重新生成')
      .map(block => block.id)).toEqual(['info', 'product']);
  });

  it('anchors a screenshot to one exact paragraph and refuses ambiguous text', () => {
    const blocks = [
      { id: 'scope', title: '项目范围', content: '前言\n用户可以按分类查看每日支出。\n结尾' },
      { id: 'goal', title: '项目目标', content: '用户能够按月份查看趋势。' },
    ];
    expect(locateScreenshotInPrd(blocks, { anchor_text: '用户可以按分类查看每日支出', heading: '项目范围' }))
      .toMatchObject({ blockId: 'scope', startLine: 2, endLine: 2 });
    expect(() => locateScreenshotInPrd([...blocks, { id: 'copy', title: '副本', content: blocks[0].content }],
      { anchor_text: '用户可以按分类查看每日支出', heading: '' })).toThrow('出现多次');
  });
});

describe('command scope comes only from explicit acts', () => {
  const blocks = [
    { id: 'a', title: '产品背景', parent_id: null },
    { id: 'b', title: '用户问题', parent_id: null },
    { id: 'c', title: '更新记录', parent_id: null },
  ];

  it('prefers the chapters named in the instruction', () => {
    const scope = prdEditScope(blocks, '把用户问题里的表格重排', ['c']);
    expect(scope.source).toBe('named');
    expect(scope.targets.map(block => block.id)).toEqual(['b']);
  });

  it('falls back to the ticked chapters, in document order', () => {
    const scope = prdEditScope(blocks, '把这张表重排一下', ['c', 'a']);
    expect(scope.source).toBe('checked');
    expect(scope.targets.map(block => block.id)).toEqual(['a', 'c']);
  });

  it('uses the chapter whose text was selected before defaulting to the whole document', () => {
    const scope = prdEditScope(blocks, '把这一段改短', [], 'b');
    expect(scope.source).toBe('focused');
    expect(scope.targets.map(block => block.id)).toEqual(['b']);
  });

  it('defaults to the whole document when nothing was named or ticked', () => {
    const scope = prdEditScope(blocks, '把这张表重排一下', []);
    expect(scope.source).toBe('document');
    expect(scope.targets).toEqual([]);
  });

  it('resolves precedence: named > ticked > selected text > whole document', () => {
    expect(prdEditScope(blocks, '把用户问题里的表格重排', ['c'], 'a').source).toBe('named');
    expect(prdEditScope(blocks, '把这张表重排一下', ['c'], 'a').source).toBe('checked');
    expect(prdEditScope(blocks, '把这张表重排一下', [], 'a').source).toBe('focused');
    expect(prdEditScope(blocks, '把这张表重排一下', []).source).toBe('document');
  });

  it('keeps the outline click out of the editor scope wiring', () => {
    // 解析器本身测不出这条规则——旧实现就是**在调用点**把「目录里点中的那一章」当兜底喂了进去。
    // 所以直接守住调用点：算范围时不得引用目录的选中态（selected / selectedRef）。
    const source = readFileSync(new URL('./PRDEditor.tsx', import.meta.url), 'utf8');
    const index = source.indexOf('prdEditScope(');
    expect(index).toBeGreaterThan(-1);
    const call = source.slice(index, source.indexOf('\n', index));
    expect(call).toContain('focusedRange?.blockId');
    expect(call).not.toMatch(/selectedRef\.current|,\s*selected\s*[,)]/);
  });
});
