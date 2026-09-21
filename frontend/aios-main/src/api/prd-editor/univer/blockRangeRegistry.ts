import type { IDocumentData, IParagraph } from '@univerjs/presets';
import type { Block } from '../types';
import { BLOCK_HEADING_PREFIX } from './blockDocumentAdapter';
import type { BlockRange, BlockTextSelection } from './types';

const separatorLength = 2;

function paragraphStart(paragraphs: IParagraph[], index: number) {
  return index === 0 ? 0 : paragraphs[index - 1].startIndex + 1;
}

export class BlockRangeRegistry {
  readonly ranges: BlockRange[];
  private readonly byId: Map<string, BlockRange>;

  constructor(document: IDocumentData, blocks: Block[]) {
    const body = document.body;
    if (!body) throw new Error('Univer 文档缺少正文');
    const paragraphs = body.paragraphs ?? [];
    const expected = new Map(blocks.map(block => [block.id, block]));
    const headings = paragraphs.map((paragraph, index) => ({ paragraph, index }))
      .filter(({ paragraph }) => paragraph.paragraphStyle?.headingId?.startsWith(BLOCK_HEADING_PREFIX));
    if (headings.length !== blocks.length) throw new Error('Block 标题边界已被改变');

    const ranges = headings.map(({ paragraph, index }, headingIndex) => {
      const blockId = paragraph.paragraphStyle!.headingId!.slice(BLOCK_HEADING_PREFIX.length);
      const block = expected.get(blockId);
      if (!block) throw new Error('Univer 文档包含未知 Block 边界');
      const headingStart = paragraphStart(paragraphs, index);
      const headingEnd = paragraph.startIndex;
      if (body.dataStream.slice(headingStart, headingEnd) !== block.title) throw new Error('Block 标题只能通过左侧目录属性修改');
      const next = headings[headingIndex + 1];
      const end = next ? paragraphStart(paragraphs, next.index) : body.dataStream.length - 1;
      if (end - (headingEnd + 1) < separatorLength || body.dataStream.slice(end - separatorLength, end) !== '\r\r') {
        throw new Error('Block 之间的受保护边界已被改变');
      }
      if (/[\n\v\f\0\b\x1a-\x1f]/.test(body.dataStream.slice(headingEnd + 1, end - separatorLength))) {
        throw new Error('当前阶段仅支持 Block 内的文本与文字格式；表格、图片和结构块将在资产存储接入后开放');
      }
      return {
        blockId,
        headingStart,
        headingEnd,
        bodyStart: headingEnd + 1,
        bodyEnd: end,
        end,
        version: block.version,
      };
    });
    if (new Set(ranges.map(range => range.blockId)).size !== blocks.length) throw new Error('Block 边界重复或缺失');
    this.ranges = ranges;
    this.byId = new Map(ranges.map(range => [range.blockId, range]));
  }

  get(blockId: string) {
    return this.byId.get(blockId);
  }

  find(offset: number) {
    return this.ranges.find(range => offset >= range.headingStart && offset < range.end)
      ?? (offset === this.ranges.at(-1)?.end ? this.ranges.at(-1) : undefined);
  }

  editableEnd(range: BlockRange) {
    return range.bodyEnd - separatorLength;
  }

  isEditableSelection(startOffset: number, endOffset: number) {
    const start = this.find(startOffset);
    const end = this.find(Math.max(startOffset, endOffset - (endOffset > startOffset ? 1 : 0)));
    return Boolean(start && end && start.blockId === end.blockId
      && startOffset >= start.bodyStart && endOffset <= this.editableEnd(start));
  }

  content(document: IDocumentData, blockId: string) {
    const body = document.body;
    const range = this.get(blockId);
    if (!body || !range) return '';
    return body.dataStream.slice(range.bodyStart, this.editableEnd(range)).replace(/\r/g, '\n');
  }

  selections(document: IDocumentData, startOffset: number, endOffset: number): BlockTextSelection[] {
    const body = document.body;
    if (!body) return [];
    const first = Math.min(startOffset, endOffset), last = Math.max(startOffset, endOffset);
    return this.ranges.flatMap(range => {
      const start = Math.max(first, range.bodyStart);
      const end = Math.min(last, this.editableEnd(range));
      if (start >= end) return [];
      const blockText = body.dataStream.slice(range.bodyStart, this.editableEnd(range));
      const localStart = start - range.bodyStart, localEnd = end - range.bodyStart;
      return [{
        blockId: range.blockId,
        startOffset: localStart,
        endOffset: localEnd,
        startLine: blockText.slice(0, localStart).split('\r').length,
        endLine: blockText.slice(0, Math.max(localStart, localEnd - 1)).split('\r').length,
      }];
    });
  }
}
