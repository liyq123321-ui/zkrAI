export interface DrawioGraphViewer {
  createViewerForElement: (element: HTMLElement) => void;
}

declare global {
  interface Window {
    GraphViewer?: DrawioGraphViewer;
    DRAW_MATH_URL?: string;
    PROXY_URL?: string;
    STYLE_PATH?: string;
    SHAPES_PATH?: string;
    STENCIL_PATH?: string;
    GRAPH_IMAGE_PATH?: string;
    mxImageBasePath?: string;
    mxBasePath?: string;
    MathJax?: unknown;
  }
}

const SCRIPT_ID = 'firstflight-drawio-viewer-31-4-2';
const ASSET_BASE = `${import.meta.env.BASE_URL}drawio/31.4.2`;
const SCRIPT_PATH = `${ASSET_BASE}/viewer-static.min.js`;
let viewerPromise: Promise<DrawioGraphViewer> | null = null;

function configureOfflineViewer(): void {
  window.DRAW_MATH_URL = `${ASSET_BASE}/math4/es5`;
  window.PROXY_URL = `${ASSET_BASE}/disabled/proxy`;
  window.STYLE_PATH = `${ASSET_BASE}/styles`;
  window.SHAPES_PATH = `${ASSET_BASE}/shapes`;
  window.STENCIL_PATH = `${ASSET_BASE}/stencils`;
  window.GRAPH_IMAGE_PATH = `${ASSET_BASE}/img`;
  window.mxImageBasePath = `${ASSET_BASE}/mxgraph/images`;
  window.mxBasePath = `${ASSET_BASE}/mxgraph/`;
  // ER diagrams do not support math markup. A defined sentinel prevents the
  // upstream bundle's unconditional Editor.initMath() from injecting a CDN script.
  if (window.MathJax === undefined) window.MathJax = {};
}

export function loadDrawioViewer(timeoutMs = 10_000): Promise<DrawioGraphViewer> {
  if (window.GraphViewer) return Promise.resolve(window.GraphViewer);
  if (viewerPromise) return viewerPromise;
  configureOfflineViewer();

  viewerPromise = new Promise<DrawioGraphViewer>((resolve, reject) => {
    let script = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
    if (!script) {
      script = document.createElement('script');
      script.id = SCRIPT_ID;
      script.src = SCRIPT_PATH;
      script.async = true;
      document.head.appendChild(script);
    }

    let settled = false;
    const cleanup = () => {
      window.clearTimeout(timeout);
      script.removeEventListener('load', finish);
      script.removeEventListener('error', onError);
    };
    const finish = () => {
      if (settled) return;
      if (!window.GraphViewer) return fail(new Error('draw.io viewer loaded without GraphViewer'));
      settled = true;
      cleanup();
      resolve(window.GraphViewer);
    };
    const fail = (reason?: unknown) => {
      if (settled) return;
      settled = true;
      cleanup();
      script.remove();
      viewerPromise = null;
      reject(reason instanceof Error ? reason : new Error('draw.io viewer could not be loaded'));
    };
    const onError = () => fail();
    const timeout = window.setTimeout(() => fail(), timeoutMs);
    script.addEventListener('load', finish, { once: true });
    script.addEventListener('error', onError, { once: true });
  });
  return viewerPromise;
}
