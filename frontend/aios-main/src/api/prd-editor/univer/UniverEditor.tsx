import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import type { IDocumentBody, IDocumentData } from '@univerjs/presets';
import { CommandType } from '@univerjs/presets';
import { IRenderManagerService } from '@univerjs/engine-render';
import type { IRender } from '@univerjs/engine-render';
import { calcDocRangePositions } from '@univerjs/docs-ui';
import type { Block } from '../types';
import { toUniverBlockBody, toUniverDocument } from './blockDocumentAdapter';
import { BlockRangeRegistry } from './blockRangeRegistry';
import { createPrdUniver } from './univerFactory';
import type { BlockTextSelection, UniverEditorHandle } from './types';

interface DocumentFacade {
  getSnapshot: () => IDocumentData;
  setSelection: (startOffset: number, endOffset: number) => void;
}

interface UniverEditorProps {
  documentId: string;
  title: string;
  blocks: Block[];
  selectedBlockId: string;
  streamingBlockId?: string;
  streamingLabel?: string;
  onSelectBlock: (blockId: string) => void;
  onViewportBlockChange?: (blockId: string) => void;
  onBlockContentChange: (blockId: string, content: string) => void;
  onSelection: (selections: BlockTextSelection[]) => void;
  onBoundaryViolation: (message: string) => void;
}

const selectionCommand = 'doc.operation.set-selections';
const editingMutation = 'doc.mutation.rich-text-editing';
const insertCommand = 'doc.command.insert-text';
const unsafeEditCommands = new Set([
  insertCommand, 'doc.command.delete-text', 'doc.command.delete-left', 'doc.command.delete-right',
  'doc.command.break-line', 'doc.command.inner-paste', 'doc.command.inner-cut', 'doc.command.merge-two-paragraph',
]);

export const UniverEditor = forwardRef<UniverEditorHandle, UniverEditorProps>(function UniverEditor({
  documentId, title, blocks, selectedBlockId, streamingBlockId, streamingLabel = '正在流式写入', onSelectBlock, onViewportBlockChange, onBlockContentChange, onSelection, onBoundaryViolation,
}, ref) {
  const hostRef = useRef<HTMLDivElement>(null);
  const facadeRef = useRef<DocumentFacade | null>(null);
  const apiRef = useRef<ReturnType<typeof createPrdUniver>['univerAPI'] | null>(null);
  const registryRef = useRef<BlockRangeRegistry | null>(null);
  const blocksRef = useRef(blocks);
  const selectedBlockRef = useRef(selectedBlockId);
  const passiveSelectionRef = useRef<string | null>(null);
  const viewportCallbackRef = useRef(onViewportBlockChange);
  const contentsRef = useRef(new Map(blocks.map(block => [block.id, block.content])));
  const currentSelectionRef = useRef({ startOffset: 0, endOffset: 0 });
  const applyingRef = useRef(false);
  const renderRef = useRef<IRender | null>(null);
  const [guide, setGuide] = useState<{ top: number; left: number; width: number; height: number } | null>(null);
  const structureKey = useMemo(() => blocks.map(block => `${block.id}:${block.title}:${block.parent_id ?? ''}:${block.order}`).join('|'), [blocks]);
  const initial = useMemo(() => toUniverDocument(blocks, documentId, title), [documentId, title, structureKey]);

  blocksRef.current = blocks;
  selectedBlockRef.current = selectedBlockId;
  viewportCallbackRef.current = onViewportBlockChange;

  const updateGuide = useCallback((blockId = selectedBlockRef.current) => {
    const host = hostRef.current, render = renderRef.current, range = registryRef.current?.get(blockId);
    if (!host || !render || !range) { setGuide(null); return; }
    const positions = calcDocRangePositions({ startOffset: range.headingStart, endOffset: registryRef.current!.editableEnd(range), collapsed: false, segmentId: '' }, render);
    if (!positions?.length) { setGuide(null); return; }
    const hostRect = host.getBoundingClientRect();
    const visible = positions.filter(position => position.bottom >= hostRect.top && position.top <= hostRect.bottom);
    if (!visible.length) { setGuide(null); return; }
    const top = Math.max(4, Math.min(...visible.map(position => position.top)) - hostRect.top - 5);
    const bottom = Math.min(hostRect.height - 4, Math.max(...visible.map(position => position.bottom)) - hostRect.top + 5);
    const left = Math.max(4, Math.min(...visible.map(position => position.left)) - hostRect.left - 10);
    const right = Math.min(hostRect.width - 4, Math.max(...visible.map(position => position.right)) - hostRect.left + 10);
    setGuide({ top, left, width: Math.max(40, right - left), height: Math.max(28, bottom - top) });
  }, []);

  const updateViewportBlock = useCallback(() => {
    const host = hostRef.current, render = renderRef.current, registry = registryRef.current;
    if (!host || !render || !registry?.ranges.length) return;
    const hostRect = host.getBoundingClientRect();
    const anchor = hostRect.top + Math.min(180, Math.max(92, hostRect.height * 0.24));
    const headings = registry.ranges.map(range => ({
      blockId: range.blockId,
      top: calcDocRangePositions({ startOffset: range.headingStart, endOffset: range.headingStart, collapsed: true, segmentId: '' }, render)?.[0]?.top,
    })).filter((item): item is { blockId: string; top: number } => typeof item.top === 'number');
    if (!headings.length) return;
    let active = headings[0];
    for (const heading of headings) {
      if (heading.top > anchor) break;
      active = heading;
    }
    if (active.blockId === selectedBlockRef.current) return;
    passiveSelectionRef.current = active.blockId;
    selectedBlockRef.current = active.blockId;
    viewportCallbackRef.current?.(active.blockId);
  }, []);

  const focusBlock = (blockId: string, startOffset?: number, endOffset?: number) => {
    const range = registryRef.current?.get(blockId);
    if (!range) return;
    const editableLength = Math.max(0, range.bodyEnd - range.bodyStart - 2);
    const relativeStart = Math.min(Math.max(0, startOffset ?? 0), editableLength);
    const relativeEnd = Math.min(Math.max(relativeStart, endOffset ?? relativeStart), editableLength);
    applyingRef.current = true;
    facadeRef.current?.setSelection(range.bodyStart + relativeStart, range.bodyStart + relativeEnd);
    queueMicrotask(() => { applyingRef.current = false; });
    requestAnimationFrame(() => requestAnimationFrame(() => updateGuide(blockId)));
  };
  useImperativeHandle(ref, () => ({ focusBlock }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const container = document.createElement('div');
    container.className = 'prd-univer-canvas';
    host.append(container);
    const { univer, univerAPI } = createPrdUniver(container);
    const documentFacade = univerAPI.createUniverDoc(initial.document) as unknown as DocumentFacade;
    facadeRef.current = documentFacade;
    apiRef.current = univerAPI;
    registryRef.current = new BlockRangeRegistry(documentFacade.getSnapshot(), blocksRef.current);
    contentsRef.current = new Map(blocksRef.current.map(block => [block.id, block.content]));
    let scrollDisposable: { unsubscribe: () => void } | undefined;
    let scrollFrame = 0;
    let bindGuideFrame = 0;
    let frame = 0;
    let disposed = false;
    const bindGuide = () => {
      if (disposed) return;
      const render = univer.__getInjector().get(IRenderManagerService).getRenderById(documentId);
      if (!render) {
        if (frame++ < 12) bindGuideFrame = requestAnimationFrame(bindGuide);
        else setGuide(null);
        return;
      }
      renderRef.current = render;
      const viewport = render.scene.getViewport('viewMain');
      const syncViewport = () => {
        cancelAnimationFrame(scrollFrame);
        scrollFrame = requestAnimationFrame(() => { updateViewportBlock(); updateGuide(); });
      };
      scrollDisposable = viewport?.onScrollAfter$.subscribeEvent(syncViewport);
      updateViewportBlock();
      updateGuide();
    };
    bindGuideFrame = requestAnimationFrame(bindGuide);

    const before = univerAPI.addEvent(univerAPI.Event.BeforeCommandExecute, event => {
      if (applyingRef.current || event.type !== CommandType.COMMAND || !unsafeEditCommands.has(event.id)) return;
      const params = event.params as { range?: { startOffset: number; endOffset: number }; unitId?: string } | undefined;
      if (params?.unitId && params.unitId !== documentId) return;
      const range = params?.range ?? currentSelectionRef.current;
      const blockRange = registryRef.current?.find(range.startOffset);
      const deletesProtectedLeft = event.id === 'doc.command.delete-left' && range.startOffset === range.endOffset && range.startOffset <= (blockRange?.bodyStart ?? -1);
      const deletesProtectedRight = event.id === 'doc.command.delete-right' && range.startOffset === range.endOffset && range.endOffset >= (blockRange ? registryRef.current!.editableEnd(blockRange) : Number.MAX_SAFE_INTEGER);
      if (deletesProtectedLeft || deletesProtectedRight || !registryRef.current?.isEditableSelection(range.startOffset, range.endOffset)) {
        event.cancel = true;
        onBoundaryViolation('Block 标题、顺序和跨 Block 结构编辑暂时受保护，请在单个 Block 正文内修改。');
      }
    });

    const executed = univerAPI.addEvent(univerAPI.Event.CommandExecuted, event => {
      const params = event.params as { unitId?: string; ranges?: { startOffset: number; endOffset: number }[] } | undefined;
      if (params?.unitId && params.unitId !== documentId) return;
      if (event.id === selectionCommand && params?.ranges?.length) {
        const active = params.ranges[0];
        currentSelectionRef.current = active;
        const registry = registryRef.current;
        const snapshot = documentFacade.getSnapshot();
        const range = registry?.find(active.startOffset);
        if (!applyingRef.current && range) {
          passiveSelectionRef.current = range.blockId;
          selectedBlockRef.current = range.blockId;
          onSelectBlock(range.blockId);
          updateGuide(range.blockId);
        }
        if (!applyingRef.current && active.startOffset !== active.endOffset && registry) {
          onSelection(registry.selections(snapshot, active.startOffset, active.endOffset));
        }
        return;
      }
      if (event.id !== editingMutation || applyingRef.current) return;
      queueMicrotask(() => {
        try {
          const snapshot = documentFacade.getSnapshot();
          const registry = new BlockRangeRegistry(snapshot, blocksRef.current);
          const changed = blocksRef.current.filter(block => registry.content(snapshot, block.id) !== contentsRef.current.get(block.id));
          if (changed.length > 1) throw new Error('一次编辑不能跨越多个 Block');
          registryRef.current = registry;
          if (changed[0]) {
            const content = registry.content(snapshot, changed[0].id);
            contentsRef.current.set(changed[0].id, content);
            onBlockContentChange(changed[0].id, content);
          }
        } catch (error) {
          applyingRef.current = true;
          void documentFacade.getSnapshot();
          void (univerAPI.getActiveDocument() as unknown as { undo: () => Promise<boolean> } | null)?.undo()
            .finally(() => { applyingRef.current = false; });
          onBoundaryViolation(error instanceof Error ? error.message : String(error));
        }
      });
    });

    queueMicrotask(() => focusBlock(selectedBlockId));
    return () => {
      disposed = true;
      before.dispose();
      executed.dispose();
      facadeRef.current = null;
      apiRef.current = null;
      renderRef.current = null;
      registryRef.current = null;
      scrollDisposable?.unsubscribe();
      cancelAnimationFrame(scrollFrame);
      cancelAnimationFrame(bindGuideFrame);
      queueMicrotask(() => { univer.dispose(); container.remove(); });
    };
  }, [documentId, initial, updateGuide, updateViewportBlock]);

  useEffect(() => {
    if (passiveSelectionRef.current === selectedBlockId) {
      passiveSelectionRef.current = null;
      requestAnimationFrame(() => updateGuide(selectedBlockId));
      return;
    }
    focusBlock(selectedBlockId);
  }, [selectedBlockId, updateGuide]);

  useEffect(() => {
    if (!streamingBlockId) return;
    focusBlock(streamingBlockId);
  }, [streamingBlockId, updateGuide]);

  useEffect(() => {
    const documentFacade = facadeRef.current;
    const univerAPI = apiRef.current;
    if (!documentFacade || !univerAPI) return;
    let cancelled = false;
    void (async () => {
      for (const block of blocks) {
        if (cancelled || contentsRef.current.get(block.id) === block.content) continue;
        const range = registryRef.current?.get(block.id);
        if (!range) continue;
        applyingRef.current = true;
        const fragment: IDocumentBody = toUniverBlockBody(block.content);
        try {
          await univerAPI.executeCommand(insertCommand, {
            unitId: documentId,
            body: fragment,
            range: { startOffset: range.bodyStart, endOffset: range.bodyEnd, collapsed: false },
            segmentId: '',
            cursorOffset: 0,
          });
          const snapshot = documentFacade.getSnapshot();
          registryRef.current = new BlockRangeRegistry(snapshot, blocksRef.current);
          contentsRef.current.set(block.id, block.content);
          updateViewportBlock();
          updateGuide();
        } finally {
          applyingRef.current = false;
        }
      }
    })().catch(error => onBoundaryViolation(error instanceof Error ? error.message : String(error)));
    return () => { cancelled = true; };
  }, [blocks, documentId, onBoundaryViolation, updateGuide, updateViewportBlock]);

  const current = blocks.find(block => block.id === (streamingBlockId ?? selectedBlockId));
  return <div ref={hostRef} className="prd-univer-host" role="textbox" aria-label="Univer PRD 文档" aria-multiline="true">
    {guide && <div className={`prd-univer-block-guide${streamingBlockId ? ' is-ai-revising' : ''}`} style={guide} aria-hidden="true"><span>{streamingBlockId ? streamingLabel : '当前目录'} · {current?.title}</span></div>}
  </div>;
});
