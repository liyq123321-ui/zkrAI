import { BooleanNumber, LocaleType, NamedStyleType } from '@univerjs/presets';
import type { IDocumentBody, IDocumentData, IParagraph, ITextRun } from '@univerjs/presets';
import type { Block } from '../types';
import type { BlockRange } from './types';

export const BLOCK_HEADING_PREFIX = 'firstflight-block:';

function normalizedText(value: string) {
  return value.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

export function orderedBlocks(blocks: Block[]) {
  const ordered = [...blocks].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  const result: { block: Block; depth: number }[] = [];
  const visited = new Set<string>();
  function walk(parentId: string | null, depth: number) {
    for (const block of ordered.filter(item => item.parent_id === parentId)) {
      if (visited.has(block.id)) continue;
      visited.add(block.id);
      result.push({ block, depth });
      walk(block.id, depth + 1);
    }
  }
  walk(null, 1);
  // Malformed legacy trees must remain visible instead of silently losing data.
  for (const block of ordered) if (!visited.has(block.id)) result.push({ block, depth: 1 });
  return result;
}

function headingStyle(depth: number) {
  return [NamedStyleType.HEADING_1, NamedStyleType.HEADING_2, NamedStyleType.HEADING_3,
    NamedStyleType.HEADING_4, NamedStyleType.HEADING_5][Math.min(Math.max(depth, 1), 5) - 1];
}

function pushParagraphs(dataStream: string, base: number, paragraphs: IParagraph[]) {
  for (let index = 0; index < dataStream.length; index++) {
    if (dataStream[index] === '\r') paragraphs.push({ startIndex: base + index });
  }
}

/** Two trailing paragraph tokens are structural separators, not Block.content. */
export function toUniverBlockBody(content: string): IDocumentBody {
  const dataStream = normalizedText(content).replace(/\n/g, '\r') + '\r\r';
  const paragraphs: IParagraph[] = [];
  pushParagraphs(dataStream, 0, paragraphs);
  return { dataStream, paragraphs };
}

export function toUniverDocument(blocks: Block[], documentId: string, title: string) {
  let dataStream = '';
  const paragraphs: IParagraph[] = [];
  const textRuns: ITextRun[] = [];
  const ranges: BlockRange[] = [];

  for (const { block, depth } of orderedBlocks(blocks)) {
    const headingStart = dataStream.length;
    dataStream += block.title;
    const headingEnd = dataStream.length;
    textRuns.push({ st: headingStart, ed: headingEnd, ts: { bl: BooleanNumber.TRUE, fs: depth === 1 ? 18 : 15 } });
    dataStream += '\r';
    paragraphs.push({
      startIndex: dataStream.length - 1,
      paragraphStyle: {
        headingId: BLOCK_HEADING_PREFIX + block.id,
        namedStyleType: headingStyle(depth),
        spaceAbove: { v: depth === 1 ? 18 : 12 },
        spaceBelow: { v: 6 },
      },
    });
    const bodyStart = dataStream.length;
    const body = toUniverBlockBody(block.content);
    pushParagraphs(body.dataStream, bodyStart, paragraphs);
    dataStream += body.dataStream;
    ranges.push({
      blockId: block.id,
      headingStart,
      headingEnd,
      bodyStart,
      bodyEnd: dataStream.length,
      end: dataStream.length,
      version: block.version,
    });
  }
  dataStream += '\n';

  const document: IDocumentData = {
    id: documentId,
    rev: 0,
    locale: LocaleType.ZH_CN,
    title,
    body: {
      dataStream,
      paragraphs,
      textRuns,
      sectionBreaks: [{ startIndex: dataStream.length - 1 }],
    },
    documentStyle: {
      pageSize: { width: 793, height: 1123 },
      marginTop: 60,
      marginBottom: 60,
      marginLeft: 72,
      marginRight: 72,
      textStyle: { ff: 'Arial', fs: 11 },
    },
  };
  return { document, ranges };
}
