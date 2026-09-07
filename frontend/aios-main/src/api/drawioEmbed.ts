export const DRAWIO_EMBED_ORIGIN = 'https://embed.diagrams.net';
export const DRAWIO_EMBED_URL = `${DRAWIO_EMBED_ORIGIN}/?embed=1&proto=json&spin=1&libraries=0&saveAndExit=1`;

const DRAFT_PREFIX = 'firstflight.drawio-draft.';

export interface DrawioDraft {
  wi: string;
  diagramId: string;
  baseVersion: number;
  baseCommitSha: string;
  xml: string;
  savedAt: string;
  changeSummary?: string;
}

export class DrawioPopupBlockedError extends Error {
  constructor() {
    super('draw.io editor popup was blocked');
    this.name = 'DrawioPopupBlockedError';
  }
}

export const drawioDraftKey = (draft: Pick<DrawioDraft, 'wi' | 'diagramId' | 'baseVersion' | 'baseCommitSha'>) =>
  `${DRAFT_PREFIX}${encodeURIComponent(draft.wi)}.${encodeURIComponent(draft.diagramId)}.v${draft.baseVersion}.${encodeURIComponent(draft.baseCommitSha)}`;

export function saveDrawioDraft(draft: DrawioDraft): string {
  const key = drawioDraftKey(draft);
  localStorage.setItem(key, JSON.stringify(draft));
  return key;
}

export function clearDrawioDraft(key: string): void {
  if (key.startsWith(DRAFT_PREFIX)) localStorage.removeItem(key);
}

export function loadDrawioDrafts(wi: string): DrawioDraft[] {
  const drafts: DrawioDraft[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (!key?.startsWith(DRAFT_PREFIX)) continue;
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? 'null') as Partial<DrawioDraft> | null;
      if (
        value
        && value.wi === wi
        && typeof value.diagramId === 'string'
        && typeof value.baseVersion === 'number'
        && typeof value.baseCommitSha === 'string'
        && typeof value.xml === 'string'
        && typeof value.savedAt === 'string'
        && (value.changeSummary === undefined || typeof value.changeSummary === 'string')
      ) {
        drafts.push(value as DrawioDraft);
      }
    } catch {
      // A malformed unrelated local value is not trusted as an editable draft.
    }
  }
  return drafts.sort((left, right) => right.savedAt.localeCompare(left.savedAt));
}

type EditorMessage = { event?: unknown; xml?: unknown };

function parseEditorMessage(value: unknown): EditorMessage | null {
  try {
    const parsed = typeof value === 'string' ? JSON.parse(value) : value;
    return parsed && typeof parsed === 'object' ? parsed as EditorMessage : null;
  } catch {
    return null;
  }
}

export interface DrawioEditorSession {
  popup: Window;
  dispose: () => void;
}

export function openDrawioEditorSession({
  wi,
  diagramId,
  baseVersion,
  baseCommitSha,
  xml,
  expectedOrigin = DRAWIO_EMBED_ORIGIN,
  openWindow = (url, name, features) => window.open(url, name, features),
  onDraft,
  onSave,
  onError,
  onCloseWithoutSave,
}: {
  wi: string;
  diagramId: string;
  baseVersion: number;
  baseCommitSha: string;
  xml: string;
  expectedOrigin?: string;
  openWindow?: (url: string, name: string, features: string) => Window | null;
  onDraft?: (draft: DrawioDraft, key: string) => void;
  onSave: (draft: DrawioDraft, key: string) => Promise<boolean> | boolean;
  onError?: (error: unknown) => void;
  onCloseWithoutSave?: () => void;
}): DrawioEditorSession {
  const popup = openWindow(
    DRAWIO_EMBED_URL,
    `firstflight-drawio-${Date.now()}`,
    'popup=yes,width=1280,height=840,resizable=yes,scrollbars=yes',
  );
  if (!popup) throw new DrawioPopupBlockedError();

  let active = true;
  let saveInFlight = false;
  let acceptedSave = false;
  const dispose = () => {
    if (!active) return;
    active = false;
    window.removeEventListener('message', receive);
    window.clearInterval(closeWatcher);
  };
  const send = (message: object) => {
    popup.postMessage(JSON.stringify(message), expectedOrigin);
  };
  const receive = (event: MessageEvent) => {
    if (!active || event.origin !== expectedOrigin || event.source !== popup) return;
    const message = parseEditorMessage(event.data);
    if (!message || typeof message.event !== 'string') return;
    if (message.event === 'init') {
      send({ action: 'load', xml, autosave: 0 });
      return;
    }
    if (message.event === 'exit') {
      dispose();
      if (!acceptedSave) onCloseWithoutSave?.();
      return;
    }
    if (message.event !== 'save' || typeof message.xml !== 'string' || saveInFlight) return;
    saveInFlight = true;
    const draft: DrawioDraft = {
      wi,
      diagramId,
      baseVersion,
      baseCommitSha,
      xml: message.xml,
      savedAt: new Date().toISOString(),
    };
    let key: string;
    try {
      key = saveDrawioDraft(draft);
      onDraft?.(draft, key);
    } catch (error) {
      saveInFlight = false;
      onError?.(error);
      return;
    }
    Promise.resolve(onSave(draft, key))
      .then((accepted) => {
        saveInFlight = false;
        if (!active || !accepted) return;
        acceptedSave = true;
        send({ action: 'exit' });
      })
      .catch((error) => {
        saveInFlight = false;
        onError?.(error);
      });
  };
  window.addEventListener('message', receive);
  const closeWatcher = window.setInterval(() => {
    if (!active || !popup.closed) return;
    dispose();
    if (!acceptedSave) onCloseWithoutSave?.();
  }, 500);
  return { popup, dispose };
}
