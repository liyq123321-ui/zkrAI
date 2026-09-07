// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.useRealTimers();
  vi.resetModules();
  delete window.GraphViewer;
  for (const name of ['DRAW_MATH_URL','PROXY_URL','STYLE_PATH','SHAPES_PATH','STENCIL_PATH','GRAPH_IMAGE_PATH','mxImageBasePath','mxBasePath','MathJax']) {
    delete (window as unknown as Record<string, unknown>)[name];
  }
  document.head.replaceChildren();
});

it('pins every optional viewer dependency to same-origin assets and disables unused math loading', async () => {
  const { loadDrawioViewer } = await import('./drawioViewer');
  const pending = loadDrawioViewer();
  const configured = window as unknown as Record<string, unknown>;

  for (const name of ['DRAW_MATH_URL','PROXY_URL','STYLE_PATH','SHAPES_PATH','STENCIL_PATH','GRAPH_IMAGE_PATH','mxImageBasePath','mxBasePath']) {
    expect(configured[name]).toMatch(/^\/drawio\/31\.4\.2(?:\/|$)/);
  }
  expect(configured.MathJax).toEqual({});
  expect([...document.querySelectorAll('script, link, img')].every((element) =>
    ![element.getAttribute('src'), element.getAttribute('href')].some((value) => value?.startsWith('http'))
  )).toBe(true);

  window.GraphViewer = { createViewerForElement: vi.fn() };
  document.querySelector('script')?.dispatchEvent(new Event('load'));
  await expect(pending).resolves.toBe(window.GraphViewer);
});

it('replaces a failed script element so a later retry can succeed', async () => {
  const { loadDrawioViewer } = await import('./drawioViewer');
  const first = loadDrawioViewer();
  const failedScript = document.querySelector('script') as HTMLScriptElement;

  failedScript.dispatchEvent(new Event('error'));
  await expect(first).rejects.toThrow('could not be loaded');
  expect(document.querySelector('script')).toBeNull();

  const second = loadDrawioViewer();
  const retryScript = document.querySelector('script') as HTMLScriptElement;
  expect(retryScript).not.toBe(failedScript);
  window.GraphViewer = { createViewerForElement: vi.fn() };
  retryScript.dispatchEvent(new Event('load'));

  await expect(second).resolves.toBe(window.GraphViewer);
});
