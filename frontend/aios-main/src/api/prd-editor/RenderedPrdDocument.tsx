import { Children, cloneElement, isValidElement, memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ElementType, ReactElement, ReactNode } from 'react';
import mermaid from 'mermaid';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MessageSquareText } from 'lucide-react';
import { DrawioErDiagram } from '../DrawioErDiagram';
import { appConfig } from '../config';
import { documentPath } from './api';
import type { Block, Comment } from './types';
import { orderedBlocks } from './univer/blockDocumentAdapter';

let mermaidReady = false;
const mermaidSvgCache = new Map<string, string>();

function initializeMermaid() {
  if (mermaidReady) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'neutral',
    fontFamily: 'Microsoft YaHei, Arial, sans-serif',
    flowchart: { htmlLabels: false, useMaxWidth: true },
  });
  mermaidReady = true;
}

function rememberMermaidSvg(key: string, svg: string) {
  if (mermaidSvgCache.size >= 100) mermaidSvgCache.delete(mermaidSvgCache.keys().next().value as string);
  mermaidSvgCache.set(key, svg);
}

function MermaidDiagram({ source, title, cacheKey, streaming = false }: { source: string; title: string; cacheKey: string; streaming?: boolean }) {
  const reactId = useId();
  const renderId = useMemo(() => `firstflight-mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, '')}`, [reactId]);
  const [result, setResult] = useState(() => ({ key: cacheKey, svg: mermaidSvgCache.get(cacheKey) ?? '', error: '' }));

  useEffect(() => {
    let active = true;
    initializeMermaid();
    const cached = mermaidSvgCache.get(cacheKey);
    if (cached) {
      setResult({ key: cacheKey, svg: cached, error: '' });
      return () => { active = false; };
    }
    setResult({ key: cacheKey, svg: '', error: '' });
    void mermaid.render(renderId, source.trim()).then(result => {
      if (active) {
        rememberMermaidSvg(cacheKey, result.svg);
        setResult({ key: cacheKey, svg: result.svg, error: '' });
      }
    }).catch(reason => {
      if (active) setResult({ key: cacheKey, svg: '', error: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { active = false; };
  }, [cacheKey, renderId, source]);

  const svg = result.key === cacheKey ? result.svg : '';
  const error = result.key === cacheKey ? result.error : '';

  if (error && streaming) return <figure className="prd-mermaid-figure is-streaming" role="status">
    <div className="prd-mermaid-loading">正在生成并渲染图表…</div><figcaption>{title}</figcaption>
  </figure>;
  if (error) return <figure className="prd-mermaid-figure is-error" role="alert">
    <div><strong>图表渲染失败</strong><span>{error}</span></div>
    <figcaption>{title} · Mermaid 源码仍保存在后台，可切换到源码编辑修正。</figcaption>
  </figure>;
  return <figure className="prd-mermaid-figure" aria-label={title}>
    {svg ? <div className="prd-mermaid-svg" dangerouslySetInnerHTML={{ __html: svg }} /> : <div className="prd-mermaid-loading">正在渲染图表…</div>}
    <figcaption>{title}</figcaption>
  </figure>;
}

export function PrdMermaidPreview({ source, title }: { source: string; title: string }) {
  return <MermaidDiagram source={source} title={title} cacheKey={`preview:${source}`} />;
}

export interface ReadingSelection {
  blockId: string;
  quote: string;
  startOffset: number;
  endOffset: number;
  startLine: number;
  endLine: number;
}

export type ReadingAnnotationAction = 'comment' | 'delete';

function normalizedContent(content: string) {
  return content.replace(/\r\n?/g, '\n');
}

export function locateReadingSelection(block: Block, quote: string, occurrence = 0): ReadingSelection | null {
  const content = normalizedContent(block.content);
  const selected = quote.trim();
  let startOffset = -1;
  for (let index = 0, from = 0; index <= occurrence; index += 1) {
    startOffset = content.indexOf(selected, from);
    if (startOffset < 0) break;
    from = startOffset + selected.length;
  }
  if (!selected || startOffset < 0) return null;
  const endOffset = startOffset + selected.length;
  return {
    blockId: block.id,
    quote: selected,
    startOffset,
    endOffset,
    startLine: content.slice(0, startOffset).split('\n').length,
    endLine: content.slice(0, endOffset).split('\n').length,
  };
}

interface ReadingHighlight {
  commentId: string;
  blockId: string;
  quote: string;
  action: ReadingAnnotationAction;
  startLine?: number;
  endLine?: number;
  temporary?: boolean;
}

type OpenComment = (commentId: string, blockId: string, anchor?: { x: number; y: number }) => void;

function decorateText(text: string, highlights: ReadingHighlight[], onOpenComment: OpenComment) {
  const result: ReactNode[] = [];
  const used = new Set<string>();
  let cursor = 0;
  while (cursor < text.length) {
    const matches = highlights.filter(highlight => !used.has(highlight.commentId)).map(highlight => ({ highlight, index: text.indexOf(highlight.quote, cursor) }))
      .filter(match => match.index >= 0).sort((a, b) => a.index - b.index || b.highlight.quote.length - a.highlight.quote.length);
    const next = matches[0];
    if (!next) { result.push(text.slice(cursor)); break; }
    if (next.index > cursor) result.push(text.slice(cursor, next.index));
    const end = next.index + next.highlight.quote.length;
    if (next.highlight.temporary) {
      result.push(<mark key={`${next.highlight.commentId}-${next.index}`} className="prd-inline-annotation is-review-focus" aria-current="true">{text.slice(next.index, end)}</mark>);
      used.add(next.highlight.commentId);
      cursor = end;
      continue;
    }
    const open = () => onOpenComment(next.highlight.commentId, next.highlight.blockId);
    const common = { tabIndex: 0, role: 'link', title: '查看批注', onClick: open,
      onKeyDown: (event: React.KeyboardEvent) => { if (event.key === 'Enter' || event.key === ' ') open(); } };
    result.push(<span key={`${next.highlight.commentId}-${next.index}`} className="prd-annotated-inline">
      {next.highlight.action === 'delete'
        ? <del {...common} className="prd-inline-annotation is-delete">{text.slice(next.index, end)}</del>
        : <mark {...common} className="prd-inline-annotation is-comment">{text.slice(next.index, end)}</mark>}
      <button type="button" className="prd-inline-comment-button" aria-label="查看这条批注" title="查看这条批注" onMouseDown={event => event.preventDefault()} onClick={event => {
        event.stopPropagation();
        const rect = event.currentTarget.getBoundingClientRect();
        onOpenComment(next.highlight.commentId, next.highlight.blockId, { x: rect.right, y: rect.top });
      }}><MessageSquareText size={14} aria-hidden="true" /></button>
    </span>);
    used.add(next.highlight.commentId);
    cursor = end;
  }
  return result;
}

function decorateChildren(children: ReactNode, highlights: ReadingHighlight[], onOpenComment: OpenComment): ReactNode {
  if (typeof children === 'string') return decorateText(children, highlights, onOpenComment);
  if (Array.isArray(children)) return Children.map(children, child => decorateChildren(child, highlights, onOpenComment));
  if (!isValidElement(children)) return children;
  const element = children as ReactElement<{ children?: ReactNode }>;
  if (element.props.children == null) return element;
  return cloneElement(element, {}, decorateChildren(element.props.children, highlights, onOpenComment));
}

interface MarkdownBodyProps {
  block: Block; streaming: boolean; highlights: ReadingHighlight[];
  onOpenComment: OpenComment;
}

function sameHighlights(left: ReadingHighlight[], right: ReadingHighlight[]) {
  return left.length === right.length && left.every((item, index) => {
    const other = right[index];
    return item.commentId === other.commentId && item.blockId === other.blockId && item.quote === other.quote
      && item.action === other.action && item.startLine === other.startLine && item.endLine === other.endLine
      && item.temporary === other.temporary;
  });
}

const MarkdownBody = memo(function MarkdownBody({ block, streaming, highlights, onOpenComment }: MarkdownBodyProps) {
  const renderedContent = useMemo(() => {
    let diagramNumber = 0;
    return block.content
      .replace(/^```mermaid\s*$/gim, () => `\`\`\`mermaid-${++diagramNumber}`)
      .replace(/(!\[[^\]]*\]\()asset:\/\/([A-Za-z0-9_-]+)(\))/g,
        (_, before: string, assetId: string, after: string) =>
          `${before}${appConfig.apiBaseUrl}${documentPath(block.document_id)}/assets/${encodeURIComponent(assetId)}${after}`);
  }, [block.content, block.document_id]);

  const forNode = (node: unknown) => {
    const position = (node as { position?: { start?: { line?: number }; end?: { line?: number } } } | undefined)?.position;
    const first = position?.start?.line, last = position?.end?.line;
    if (!first || !last) return highlights;
    return highlights.filter(highlight => !highlight.startLine || !highlight.endLine || (highlight.startLine <= last && highlight.endLine >= first));
  };
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    p({ children, node, ...props }) { return <p {...props}>{decorateChildren(children, forNode(node), onOpenComment)}</p>; },
    li({ children, node, ...props }) { return <li {...props}>{decorateChildren(children, forNode(node), onOpenComment)}</li>; },
    td({ children, node, ...props }) { return <td {...props}>{decorateChildren(children, forNode(node), onOpenComment)}</td>; },
    th({ children, node, ...props }) { return <th {...props}>{decorateChildren(children, forNode(node), onOpenComment)}</th>; },
    pre({ children, ...props }) {
      const child = Array.isArray(children) ? children[0] : children;
      if (isValidElement(child) && child.type === MermaidDiagram) return child;
      return <pre {...props}>{children}</pre>;
    },
    code({ className, children, ...props }) {
      const source = String(children).replace(/\n$/, '');
      const mermaidMatch = /\blanguage-mermaid(?:-(\d+))?\b/i.exec(className ?? '');
      if (mermaidMatch) {
        const diagramNumber = mermaidMatch[1] ?? '1';
        return <MermaidDiagram source={source} cacheKey={`${block.id}:${diagramNumber}:${source}`} title={`${block.title} · 图 ${diagramNumber}`} streaming={streaming} />;
      }
      return <code className={className} {...props}>{children}</code>;
    },
  }}>{renderedContent}</ReactMarkdown>;
}, (previous, next) => previous.block.id === next.block.id && previous.block.title === next.block.title
  && previous.block.content === next.block.content && previous.streaming === next.streaming
  && sameHighlights(previous.highlights, next.highlights));

export function RenderedPrdDocument({ blocks, comments, selectedBlockId, streamingBlockId, streamingLabel = '正在流式写入', reviewSelection, onSelectBlock, onSelectionChange, onCreateComment, onEditSource, onOpenComment, onChooseObject }: {
  blocks: Block[]; streamingBlockId?: string; streamingLabel?: string;
  comments: Comment[];
  selectedBlockId: string;
  reviewSelection?: ReadingSelection | null;
  onSelectBlock: (blockId: string) => void;
  onSelectionChange?: (selection: ReadingSelection | null) => void;
  onCreateComment: (selection: ReadingSelection, text: string, action: ReadingAnnotationAction) => Promise<void>;
  onEditSource: (selection: ReadingSelection) => void;
  onOpenComment: OpenComment;
  onChooseObject?: (blockId: string) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const ordered = useMemo(() => orderedBlocks(blocks), [blocks]);
  const [selection, setSelection] = useState<(ReadingSelection & { x: number; y: number }) | null>(null);
  const [composing, setComposing] = useState(false);
  const [commentText, setCommentText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [selectionError, setSelectionError] = useState('');
  const openCommentRef = useRef(onOpenComment);
  openCommentRef.current = onOpenComment;
  const openComment = useCallback((commentId: string, blockId: string) => openCommentRef.current(commentId, blockId), []);
  const highlights = useMemo(() => {
    const persisted = comments.filter(comment => !comment.resolved).flatMap(comment => comment.targets.flatMap(target => {
    const quote = target.selected_text ?? target.quote;
    return quote ? [{ commentId: comment.id, blockId: target.block_id, quote, action: target.action ?? 'comment',
      startLine: target.start_line, endLine: target.end_line } satisfies ReadingHighlight] : [];
    }));
    return reviewSelection ? [...persisted, { commentId: '__review-focus__', blockId: reviewSelection.blockId,
      quote: reviewSelection.quote, action: 'comment' as const, startLine: reviewSelection.startLine,
      endLine: reviewSelection.endLine, temporary: true }] : persisted;
  }, [comments, reviewSelection]);

  useEffect(() => {
    const targetId = streamingBlockId ?? selectedBlockId;
    const target = hostRef.current?.querySelector<HTMLElement>(`[data-block-id="${CSS.escape(targetId)}"]`);
    target?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, [selectedBlockId, streamingBlockId]);

  useEffect(() => {
    if (!reviewSelection) return;
    const frame = requestAnimationFrame(() => hostRef.current?.querySelector<HTMLElement>('.prd-inline-annotation.is-review-focus')
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
    return () => cancelAnimationFrame(frame);
  }, [reviewSelection]);

  function readSelection() {
    const browserSelection = window.getSelection();
    if (!browserSelection || browserSelection.isCollapsed || !browserSelection.rangeCount) { setSelection(null); onSelectionChange?.(null); return; }
    const range = browserSelection.getRangeAt(0);
    const startElement = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer as Element : range.startContainer.parentElement;
    const endElement = range.endContainer.nodeType === Node.ELEMENT_NODE ? range.endContainer as Element : range.endContainer.parentElement;
    const body = startElement?.closest<HTMLElement>('.prd-rendered-block-body');
    if (!body || body !== endElement?.closest('.prd-rendered-block-body')) { setSelection(null); onSelectionChange?.(null); return; }
    const blockId = body.closest<HTMLElement>('[data-block-id]')?.dataset.blockId;
    const block = blocks.find(item => item.id === blockId);
    const selectedText = browserSelection.toString().trim();
    const prefix = document.createRange();
    prefix.selectNodeContents(body);
    prefix.setEnd(range.startContainer, range.startOffset);
    const occurrence = selectedText ? prefix.toString().split(selectedText).length - 1 : 0;
    const located = block && locateReadingSelection(block, selectedText, occurrence);
    if (!located) { setSelection(null); onSelectionChange?.(null); return; }
    const rect = range.getBoundingClientRect();
    setSelection({ ...located, x: Math.min(window.innerWidth - 170, Math.max(170, rect.left + rect.width / 2)), y: Math.max(70, rect.top - 10) });
    setComposing(false); setCommentText(''); setSelectionError('');
    onSelectBlock(located.blockId);
    onSelectionChange?.(located);
  }

  async function create(text: string, action: ReadingAnnotationAction) {
    if (!selection || !text.trim()) return;
    setSubmitting(true); setSelectionError('');
    try {
      await onCreateComment(selection, text.trim(), action);
      window.getSelection()?.removeAllRanges();
      setSelection(null); setComposing(false); setCommentText('');
      onSelectionChange?.(null);
    } catch (error) {
      setSelectionError(error instanceof Error ? error.message : String(error));
    } finally { setSubmitting(false); }
  }

  return <div ref={hostRef} className="prd-rendered-document" aria-label="PRD 图文阅读" onMouseUp={readSelection}>
    {selection && <div className={`prd-reading-selection-menu${composing ? ' is-composing' : ''}`} style={{ left: selection.x, top: selection.y }}
      onMouseDown={event => event.preventDefault()} onMouseUp={event => event.stopPropagation()} role="toolbar" aria-label="选中文字操作">
      {!composing ? <><button onClick={() => setComposing(true)}>加批注</button>
        <button disabled={submitting} onClick={() => void create(`请删除划掉的原文：“${selection.quote}”`, 'delete')}>划掉</button>
        <button onClick={() => onEditSource(selection)}>原文修改</button></> : <div className="prd-reading-comment-composer">
        <textarea autoFocus rows={3} aria-label="批注内容" value={commentText} onChange={event => setCommentText(event.target.value)} placeholder="输入对这段原文的批注…" />
        {selectionError && <small role="alert">{selectionError}</small>}<footer><button onClick={() => setComposing(false)}>取消</button><button className="prd-primary" disabled={!commentText.trim() || submitting} onClick={() => void create(commentText, 'comment')}>{submitting ? '保存中…' : '确认批注'}</button></footer>
      </div>}
    </div>}
    {ordered.map(({ block, depth }) => {
      const Heading = `h${Math.min(depth + 1, 6)}` as ElementType;
      const className = [block.id === selectedBlockId && 'is-selected', block.id === streamingBlockId && 'is-ai-revising'].filter(Boolean).join(' ');
      return <section key={block.id} data-block-id={block.id} className={className}
        onClick={() => onSelectBlock(block.id)}>
        <Heading>{block.title}</Heading>
        {onChooseObject && /```mermaid|\|\s*:?-{3,}/.test(block.content) && <button type="button" onClick={event => { event.stopPropagation(); onChooseObject(block.id); }}>选择本章表格 / 图中对象</button>}
        {block.id === streamingBlockId && <div className="prd-stream-progress" role="status"><span />{streamingLabel}…</div>}
        <div className="prd-rendered-block-body"><MarkdownBody block={block} streaming={block.id === streamingBlockId}
          highlights={highlights.filter(highlight => highlight.blockId === block.id)} onOpenComment={openComment} />
          {block.fragment?.er_diagrams?.map(diagram => {
            const anchor = block.content.match(new RegExp(`firstflight-er-${diagram.diagram_id}-[0-9a-f]{8}`))?.[0]
              ?? `firstflight-er-${diagram.diagram_id}`;
            return <DrawioErDiagram key={diagram.diagram_id} diagram={{ ...diagram, anchor }} />;
          })}</div>
      </section>;
    })}
  </div>;
}
