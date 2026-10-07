import { describe, expect, it } from 'vitest';
import { isPrdRevisionInstruction, locateScreenshotInPrd, prdRevisionTargets } from './instructionRouting';

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
    expect(prdRevisionTargets(blocks, '产品概念设计里的信息结构图和产品结构图请重新生成', null, 'parent')
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
