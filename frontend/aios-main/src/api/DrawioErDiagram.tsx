import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent } from 'react';
import type { PrdErDiagramDto } from './dto';
import { loadDrawioViewer } from './drawioViewer';

type Offset = { x: number; y: number };

const clampScale = (value: number) => Math.min(3, Math.max(0.1, Math.round(value * 10) / 10));

export function DrawioErDiagram({
  diagram,
  onEdit,
}: {
  diagram: PrdErDiagramDto;
  onEdit?: (diagram: PrdErDiagramDto) => void;
}) {
  const viewerHost = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number; origin: Offset } | null>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Offset>({ x: 0, y: 0 });
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    loadDrawioViewer()
      .then((viewer) => {
        if (cancelled || !viewerHost.current) return;
        const host = viewerHost.current;
        host.replaceChildren();
        host.dataset.mxgraph = JSON.stringify({
          highlight: '#22d3ee',
          nav: false,
          resize: true,
          lightbox: false,
          links: false,
          'show-note-icons': false,
          'show-tooltip-icons': false,
          xml: diagram.drawio_xml,
        });
        viewer.createViewerForElement(host);
        if (!cancelled) setStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setStatus('error');
      });
    return () => { cancelled = true; };
  }, [diagram.drawio_xml, loadAttempt]);

  const reset = useCallback(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, []);

  const fit = useCallback(() => {
    const frame = viewport.current;
    const content = viewerHost.current;
    if (!frame || !content) return reset();
    const width = content.scrollWidth || frame.clientWidth;
    const height = content.scrollHeight || frame.clientHeight;
    const next = Math.min(frame.clientWidth / width, frame.clientHeight / height, 1);
    setScale(clampScale(Number.isFinite(next) && next > 0 ? next : 1));
    setOffset({ x: 0, y: 0 });
  }, [reset]);

  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const x = event.clientX ?? 0;
    const y = event.clientY ?? 0;
    drag.current = { pointerId: event.pointerId ?? 0, x, y, origin: offset };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const pointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || (event.pointerId ?? 0) !== active.pointerId) return;
    setOffset({
      x: active.origin.x + (event.clientX ?? 0) - active.x,
      y: active.origin.y + (event.clientY ?? 0) - active.y,
    });
  };
  const pointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === (event.pointerId ?? 0)) drag.current = null;
  };
  const wheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setScale((current) => clampScale(current + (event.deltaY < 0 ? 0.1 : -0.1)));
  };

  const download = () => {
    const blob = new Blob([diagram.drawio_xml], { type: 'application/vnd.jgraph.mxfile+xml;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = `${diagram.diagram_id}.drawio`;
    link.click();
    URL.revokeObjectURL(href);
  };

  const fullscreen = () => {
    void viewport.current?.requestFullscreen?.();
  };

  return (
    <section id={diagram.anchor} role="region" aria-label={`ER 图：${diagram.title}`} className="my-5 overflow-hidden rounded-xl border border-cyan-500/30 bg-slate-950">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
        <div>
          <h3 className="text-sm font-medium text-cyan-100">ER 图：{diagram.title}</h3>
          <p className="text-[11px] text-slate-500">拖动画布 · 滚轮或按钮缩放</p>
        </div>
        <div className="flex flex-wrap gap-1" aria-label="ER 图控制">
          <button type="button" aria-label="缩小" onClick={() => setScale((value) => clampScale(value - 0.2))} className="rounded border border-slate-700 px-2 py-1 text-xs">−</button>
          <button type="button" aria-label="放大" onClick={() => setScale((value) => clampScale(value + 0.2))} className="rounded border border-slate-700 px-2 py-1 text-xs">＋</button>
          <button type="button" aria-label="适应画布" onClick={fit} className="rounded border border-slate-700 px-2 py-1 text-xs">适应</button>
          <button type="button" aria-label="复位" onClick={reset} className="rounded border border-slate-700 px-2 py-1 text-xs">复位</button>
          <button type="button" aria-label="全屏" onClick={fullscreen} className="rounded border border-slate-700 px-2 py-1 text-xs">全屏</button>
          <button type="button" aria-label="下载 .drawio" onClick={download} className="rounded border border-slate-700 px-2 py-1 text-xs">下载</button>
          <button type="button" aria-label="在 draw.io 中编辑" disabled={!onEdit} onClick={() => onEdit?.(diagram)} className="rounded bg-cyan-500 px-2 py-1 text-xs font-medium text-slate-950 disabled:opacity-40">在 draw.io 中编辑</button>
        </div>
      </header>
      {status === 'error' && (
        <div role="alert" className="m-3 rounded bg-amber-500/10 p-3 text-sm text-amber-200">
          <p>ER 图暂时无法加载，PRD 其他内容不受影响。</p>
          <button type="button" aria-label="重试 ER 图" onClick={() => setLoadAttempt((value) => value + 1)} className="mt-2 rounded border border-amber-500/40 px-2 py-1 text-xs">重试</button>
        </div>
      )}
      <div
        ref={viewport}
        data-testid="drawio-viewport"
        className="relative h-[420px] cursor-grab overflow-hidden bg-white touch-none active:cursor-grabbing"
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onPointerCancel={pointerUp}
        onWheel={wheel}
      >
        {status === 'loading' && <p className="absolute inset-0 grid place-items-center text-sm text-slate-500">正在加载 ER 图…</p>}
        <div
          data-testid="drawio-transform-canvas"
          className="h-full w-full origin-top-left will-change-transform"
          style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
        >
          <div ref={viewerHost} data-testid="drawio-viewer-host" className="geDiagramContainer min-h-full min-w-full" />
        </div>
      </div>
    </section>
  );
}
