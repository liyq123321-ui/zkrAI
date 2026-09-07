// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PrdErDiagramDto } from './dto';

const { createViewerForElement, loadDrawioViewer } = vi.hoisted(() => {
  const create = vi.fn((element: HTMLElement) => {
    element.innerHTML = '<svg aria-label="rendered diagram"></svg>';
  });
  return {
    createViewerForElement: create,
    loadDrawioViewer: vi.fn(async () => ({ createViewerForElement: create })),
  };
});

vi.mock('./drawioViewer', () => ({ loadDrawioViewer }));

import { DrawioErDiagram } from './DrawioErDiagram';

const diagram: PrdErDiagramDto = {
  diagram_id: 'data-model',
  title: '核心数据模型',
  after_section: 'core_objects',
  anchor: 'firstflight-er-data-model-deadbeef',
  drawio_xml: '<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>',
};

beforeEach(() => {
  createViewerForElement.mockClear();
  loadDrawioViewer.mockClear();
  loadDrawioViewer.mockResolvedValue({ createViewerForElement });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('DrawioErDiagram', () => {
  it('renders locally with accessible pan and zoom controls', async () => {
    const onEdit = vi.fn();
    render(<DrawioErDiagram diagram={diagram} onEdit={onEdit} />);

    await waitFor(() => expect(createViewerForElement).toHaveBeenCalledOnce());
    const viewerConfig = JSON.parse(
      (createViewerForElement.mock.calls[0][0] as HTMLElement).dataset.mxgraph ?? '{}',
    );
    expect(viewerConfig.lightbox).toBe(false);
    expect(viewerConfig['show-note-icons']).toBe(false);
    expect(viewerConfig['show-tooltip-icons']).toBe(false);
    expect(viewerConfig.toolbar ?? '').not.toContain('lightbox');
    expect(screen.getByRole('region', { name: 'ER 图：核心数据模型' })).toBeTruthy();
    const canvas = screen.getByTestId('drawio-transform-canvas');
    fireEvent.click(screen.getByRole('button', { name: '放大' }));
    expect(canvas.style.transform).toContain('scale(1.2)');
    fireEvent.pointerDown(screen.getByTestId('drawio-viewport'), { pointerId: 1, clientX: 10, clientY: 20 });
    fireEvent.pointerMove(screen.getByTestId('drawio-viewport'), { pointerId: 1, clientX: 35, clientY: 45 });
    expect(canvas.style.transform).toContain('translate(25px, 25px)');
    fireEvent.click(screen.getByRole('button', { name: '复位' }));
    expect(canvas.style.transform).toContain('translate(0px, 0px) scale(1)');
    fireEvent.click(screen.getByRole('button', { name: '在 draw.io 中编辑' }));
    expect(onEdit).toHaveBeenCalledWith(diagram);
  });

  it('downloads editable drawio XML with a stable filename', async () => {
    const createObjectURL = vi.fn(() => 'blob:diagram');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    render(<DrawioErDiagram diagram={diagram} />);
    await waitFor(() => expect(createViewerForElement).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole('button', { name: '下载 .drawio' }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
  });

  it('fits a large diagram below half scale when the viewport requires it', async () => {
    render(<DrawioErDiagram diagram={diagram} />);
    await waitFor(() => expect(createViewerForElement).toHaveBeenCalledOnce());
    const viewport = screen.getByTestId('drawio-viewport');
    const host = screen.getByTestId('drawio-viewer-host');
    Object.defineProperties(viewport, {
      clientWidth: { configurable: true, value: 100 },
      clientHeight: { configurable: true, value: 100 },
    });
    Object.defineProperties(host, {
      scrollWidth: { configurable: true, value: 1_000 },
      scrollHeight: { configurable: true, value: 1_000 },
    });

    fireEvent.click(screen.getByRole('button', { name: '适应画布' }));

    expect(screen.getByTestId('drawio-transform-canvas').style.transform).toContain('scale(0.1)');
  });

  it('keeps the PRD readable and offers retry when the local viewer fails', async () => {
    loadDrawioViewer.mockRejectedValueOnce(new Error('script failed'));
    render(<DrawioErDiagram diagram={diagram} />);

    expect((await screen.findByRole('alert')).textContent).toContain('ER 图暂时无法加载');
    fireEvent.click(screen.getByRole('button', { name: '重试 ER 图' }));
    await waitFor(() => expect(createViewerForElement).toHaveBeenCalledOnce());
  });
});
