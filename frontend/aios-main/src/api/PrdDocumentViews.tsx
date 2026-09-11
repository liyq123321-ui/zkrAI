import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { PrdCommentableLinesDto, PrdErDiagramDto, PrdPrototypeDto } from './dto';
import { DrawioErDiagram } from './DrawioErDiagram';
import { appConfig } from './config';

type DiffRow = { text: string; line: number | null; kind: 'context' | 'addition' | 'deletion' | 'header' };
export function diffRows(patch: string): DiffRow[] {
  let newLine: number | null = null;
  return patch.split('\n').filter((text, index, all) => index < all.length - 1 || text !== '').map((text) => {
    const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) { newLine = Number(hunk[1]); return { text, line:null, kind:'header' }; }
    if (newLine !== null && text.startsWith('-')) return { text, line:null, kind:'deletion' };
    if (newLine !== null && (text.startsWith('+') || text.startsWith(' '))) {
      return { text, line:newLine++, kind:text.startsWith('+') ? 'addition' : 'context' };
    }
    return { text, line:null, kind:'header' };
  });
}

export function unchangedDiffRows(content: string): DiffRow[] {
  const lines = content.replace(/\r\n?/g, '\n').split('\n');
  if (lines.at(-1) === '') lines.pop();
  return lines.map((text, index) => ({
    text: ` ${text}`,
    line: index + 1,
    kind: 'context' as const,
  }));
}

export function PrdDocumentViews({ content, diagrams = [], prototype = null, prototypeSkippedForReview = false, prototypeBusy = false, readOnly = false, version, patch, commentable, ready, selectedLine, onSelectLine, onEditDiagram, onGeneratePrototype }: {
  content: string; version: number; patch: string | null; commentable: PrdCommentableLinesDto | null;
  diagrams?: PrdErDiagramDto[];
  prototype?: PrdPrototypeDto | null;
  prototypeSkippedForReview?: boolean;
  prototypeBusy?: boolean;
  readOnly?: boolean;
  ready: boolean; selectedLine: number | null; onSelectLine: (line: number) => void;
  onEditDiagram?: (diagram: PrdErDiagramDto) => void;
  onGeneratePrototype?: () => void;
}) {
  const [mode, setMode] = useState<'split' | 'document' | 'diff' | 'prototype'>(readOnly ? 'document' : 'diff');
  useEffect(() => {
    if (readOnly) setMode('document');
  }, [readOnly]);
  const allowed = new Set(commentable?.lines.map((line) => line.line) ?? []);
  const unchanged = patch === '';
  const rows = patch === null ? [] : unchanged ? unchangedDiffRows(content) : diffRows(patch);
  const diagramsByAnchor = new Map(diagrams.map((diagram) => [`#${diagram.anchor}`, diagram]));
  const prototypeUrl = prototype?.status === 'ready' && prototype.content_url
    ? `${appConfig.apiBaseUrl}${prototype.content_url}`
    : null;
  const modes: Array<readonly ['split' | 'document' | 'diff' | 'prototype', string]> = readOnly
    ? [['document', '正文'], ['prototype', 'HTML 原型']]
    : [['split', '并排查看'], ['document', '正文'], ['diff', 'Diff'], ['prototype', 'HTML 原型']];
  return <div className="mb-5">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div className="flex gap-1 rounded-lg bg-slate-950 p-1" aria-label="阅读方式">
        {modes.map(([value,label]) => <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)} className={`rounded px-3 py-1.5 text-xs ${mode === value ? 'bg-cyan-500/20 text-cyan-200' : 'text-slate-400 hover:text-slate-100'}`}>{label}</button>)}
      </div>
      <p className="text-xs text-slate-400">{readOnly ? '此版本为只读正式文档，可查看已映射的 HTML 原型；不提供 Diff、编辑或重新生成。' : '在 Diff 的新增或上下文行上点击，可添加批注。'}</p>
    </div>
    <div className={`grid gap-4 ${mode === 'split' ? 'lg:grid-cols-2' : ''}`}>
      {(mode === 'split' || mode === 'document') && <section aria-label="PRD 正文" className="min-w-0 rounded-xl border border-slate-700 bg-slate-950">
        <h3 className="border-b border-slate-800 px-4 py-3 text-sm font-medium">PRD 正文 · v{version} · 完整文档</h3>
        <div className="max-h-[60vh] overflow-auto break-words px-5 py-4 text-sm leading-7 text-slate-200 [&_h1]:mb-4 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:font-semibold [&_p]:my-3 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1 [&_table]:w-full [&_th]:border [&_th]:border-slate-700 [&_th]:p-2 [&_td]:border [&_td]:border-slate-700 [&_td]:p-2 [&_pre]:overflow-auto [&_pre]:rounded [&_pre]:bg-slate-900 [&_pre]:p-3 [&_code]:text-cyan-200 [&_blockquote]:border-l-2 [&_blockquote]:border-slate-600 [&_blockquote]:pl-3">
          <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']} components={{
            p: ({ node, children }) => {
              const only = node?.children.length === 1 ? node.children[0] : null;
              const href = only && 'properties' in only ? String(only.properties?.href ?? '') : '';
              const diagram = diagramsByAnchor.get(href);
              return diagram ? <DrawioErDiagram diagram={diagram} onEdit={onEditDiagram} /> : <p>{children}</p>;
            },
            a: ({node,...props}) => <a {...props} className="text-cyan-300 underline" target="_blank" rel="noreferrer" />,
          }}>{content}</Markdown>
        </div>
      </section>}
      {(mode === 'split' || mode === 'diff') && <section aria-label="PRD Diff" className="min-w-0 rounded-xl border border-slate-700 bg-slate-950">
        <h3 className="border-b border-slate-800 px-4 py-3 text-sm font-medium">{version > 1 ? `Diff · v${version} 对比 v${version - 1}` : 'Diff · v1 初始版本'}</h3>
        <p className="border-b border-slate-800 px-4 py-2 text-xs text-slate-400">绿色为相对上一版的新增，红色为相对上一版的删除。仅初始版本会全部显示为新增。</p>
        <div className="max-h-[55vh] overflow-auto font-mono text-xs leading-6">
          {patch === null ? <p className="p-4 text-slate-400">Diff 暂不可用，仍可阅读完整正文。</p> : <>{unchanged && <p className="border-b border-slate-200 bg-white px-4 py-2 text-slate-600">本版本与上一版无差异，以下显示完整正文，可点击任意可批注行继续提出修改。</p>}{rows.map((row,index) => {
            const canComment = ready && row.line !== null && allowed.has(row.line);
            const tone = unchanged ? 'bg-white text-slate-900' : row.kind === 'addition' ? 'bg-emerald-500/10 text-slate-900' : row.kind === 'deletion' ? 'bg-rose-500/10 text-slate-900' : row.kind === 'header' ? 'bg-slate-800/60 text-slate-400' : 'text-slate-300';
            const className = `grid w-full grid-cols-[36px_1fr] gap-2 px-3 py-0.5 text-left ${tone} ${canComment ? 'hover:bg-cyan-500/20' : ''} ${selectedLine === row.line && row.line !== null ? 'ring-1 ring-inset ring-cyan-400' : ''}`;
            const children = <><span className="select-none text-right text-slate-500">{row.line}</span><span className="whitespace-pre-wrap break-all">{row.text || ' '}</span></>;
            return canComment ? <button key={index} type="button" aria-label={`给第 ${row.line} 行添加批注`} className={className} onClick={() => onSelectLine(row.line!)}>{children}</button> : <div key={index} className={className}>{children}</div>;
          })}</>}
        </div>
      </section>}
      {mode === 'prototype' && <section aria-label="HTML 原型" className="min-w-0 rounded-xl border border-slate-700 bg-white text-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
          <div><h3 className="text-sm font-medium">{prototype?.title || 'HTML 交互原型'}</h3>{prototype?.generation_summary && <p className="mt-1 text-xs text-slate-500">{prototype.generation_summary}</p>}</div>
          {prototypeUrl && <a href={prototypeUrl} target="_blank" rel="noreferrer" className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50">在新窗口打开原型</a>}
        </div>
        {prototypeUrl
          ? <iframe title={prototype?.title || 'HTML 交互原型'} src={prototypeUrl} sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" className="h-[65vh] w-full bg-white" />
          : <div role="status" className="p-6 text-sm text-slate-600">
              <p>{prototype?.error_code ? `${prototype.error_code}：` : ''}{prototype?.error_message || (prototypeSkippedForReview ? '当前 PRD 存在待处理审核项，系统已跳过自动生成 HTML 原型，以节省处理时间。' : '当前版本尚未生成 HTML 原型。')}</p>
              {onGeneratePrototype && <button type="button" disabled={prototypeBusy} onClick={onGeneratePrototype} className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-xs font-medium text-white disabled:opacity-50">{prototypeBusy ? '正在生成 HTML 原型…' : '手动生成 HTML 原型'}</button>}
            </div>}
      </section>}
    </div>
  </div>;
}
