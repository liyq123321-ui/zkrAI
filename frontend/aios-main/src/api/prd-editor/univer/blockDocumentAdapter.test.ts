import { describe, expect, it } from 'vitest';
import type { Block } from '../types';
import { toUniverDocument } from './blockDocumentAdapter';
import { BlockRangeRegistry } from './blockRangeRegistry';

function blocks(): Block[] {
  return [
    { id: 'background', document_id: 'doc', title: '产品背景', parent_id: null, order: 0, status: 'ready', version: 3,
      content: '第一段\n第二段', summary: '', instruction: '', dependencies: [], fragment_version: 3 },
    { id: 'goal', document_id: 'doc', title: '产品目标', parent_id: 'background', order: 1, status: 'ready', version: 2,
      content: '目标正文', summary: '', instruction: '', dependencies: ['background'], fragment_version: 2 },
  ];
}

describe('Block to Univer document adapter', () => {
  it('creates stable heading and body ranges and round-trips block text', () => {
    const source = blocks();
    const { document } = toUniverDocument(source, 'doc', 'PRD');
    const registry = new BlockRangeRegistry(document, source);
    expect(registry.ranges.map(range => range.blockId)).toEqual(['background', 'goal']);
    expect(registry.content(document, 'background')).toBe('第一段\n第二段');
    expect(registry.content(document, 'goal')).toBe('目标正文');
    expect(registry.get('background')!.bodyEnd).toBeLessThanOrEqual(registry.get('goal')!.headingStart);
  });

  it('maps a document selection back to block-local line ranges', () => {
    const source = blocks();
    const { document } = toUniverDocument(source, 'doc', 'PRD');
    const registry = new BlockRangeRegistry(document, source);
    const range = registry.get('background')!;
    const selected = registry.selections(document, range.bodyStart + 1, range.bodyStart + 6);
    expect(selected).toEqual([{ blockId: 'background', startOffset: 1, endOffset: 6, startLine: 1, endLine: 2 }]);
  });

  it('rejects title and separator damage instead of silently merging blocks', () => {
    const source = blocks();
    const { document } = toUniverDocument(source, 'doc', 'PRD');
    document.body!.dataStream = '已篡改' + document.body!.dataStream.slice('产品背景'.length);
    expect(() => new BlockRangeRegistry(document, source)).toThrow('标题');

    const clean = toUniverDocument(source, 'doc-2', 'PRD').document;
    const registry = new BlockRangeRegistry(clean, source);
    const first = registry.get('background')!;
    clean.body!.dataStream = clean.body!.dataStream.slice(0, first.bodyEnd - 1) + clean.body!.dataStream.slice(first.bodyEnd);
    expect(() => new BlockRangeRegistry(clean, source)).toThrow('边界');
  });
});
