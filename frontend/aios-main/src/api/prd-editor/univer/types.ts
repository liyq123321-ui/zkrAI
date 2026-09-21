export interface BlockRange {
  blockId: string;
  headingStart: number;
  headingEnd: number;
  bodyStart: number;
  bodyEnd: number;
  end: number;
  version: number;
}

export interface BlockTextSelection {
  blockId: string;
  startOffset: number;
  endOffset: number;
  startLine: number;
  endLine: number;
}

export interface UniverEditorHandle {
  focusBlock: (blockId: string, startOffset?: number, endOffset?: number) => void;
}
