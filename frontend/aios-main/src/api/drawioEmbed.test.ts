// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  DRAWIO_EMBED_ORIGIN,
  DrawioPopupBlockedError,
  clearDrawioDraft,
  loadDrawioDrafts,
  openDrawioEditorSession,
  saveDrawioDraft,
} from './drawioEmbed';

function popup() {
  return {
    closed: false,
    postMessage: vi.fn(),
    close: vi.fn(),
  } as unknown as Window;
}

const base = {
  wi: 'root-1',
  diagramId: 'data-model',
  baseVersion: 1,
  baseCommitSha: 'abc123',
  xml: '<mxfile/>',
};

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  localStorage.clear();
});

it('accepts init and save only from the exact popup and origin', async () => {
  const editor = popup();
  const onSave = vi.fn(async () => true);
  const onDraft = vi.fn();
  const session = openDrawioEditorSession({
    ...base,
    openWindow: () => editor,
    onDraft,
    onSave,
  });

  window.dispatchEvent(new MessageEvent('message', {
    origin: 'https://evil.example', source: editor, data: JSON.stringify({ event: 'save', xml: '<mxfile>attack</mxfile>' }),
  }));
  window.dispatchEvent(new MessageEvent('message', {
    origin: DRAWIO_EMBED_ORIGIN, source: window, data: JSON.stringify({ event: 'save', xml: '<mxfile>wrong window</mxfile>' }),
  }));
  expect(onSave).not.toHaveBeenCalled();

  window.dispatchEvent(new MessageEvent('message', {
    origin: DRAWIO_EMBED_ORIGIN, source: editor, data: JSON.stringify({ event: 'init' }),
  }));
  expect(editor.postMessage).toHaveBeenCalledWith(
    JSON.stringify({ action: 'load', xml: base.xml, autosave: 0 }),
    DRAWIO_EMBED_ORIGIN,
  );

  const savedXml = '<mxfile><diagram/></mxfile>';
  window.dispatchEvent(new MessageEvent('message', {
    origin: DRAWIO_EMBED_ORIGIN, source: editor, data: JSON.stringify({ event: 'save', xml: savedXml }),
  }));
  await vi.waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onDraft.mock.calls[0][0].xml).toBe(savedXml);
  expect(loadDrawioDrafts('root-1')[0].xml).toBe(savedXml);
  await vi.waitFor(() => expect(editor.postMessage).toHaveBeenCalledWith(
    JSON.stringify({ action: 'exit' }), DRAWIO_EMBED_ORIGIN,
  ));
  session.dispose();
});

it('ignores malformed messages and all messages after disposal', () => {
  const editor = popup();
  const onSave = vi.fn(async () => true);
  const session = openDrawioEditorSession({ ...base, openWindow: () => editor, onSave });

  window.dispatchEvent(new MessageEvent('message', {
    origin: DRAWIO_EMBED_ORIGIN, source: editor, data: '{not json',
  }));
  session.dispose();
  window.dispatchEvent(new MessageEvent('message', {
    origin: DRAWIO_EMBED_ORIGIN, source: editor, data: JSON.stringify({ event: 'save', xml: '<mxfile/>' }),
  }));

  expect(onSave).not.toHaveBeenCalled();
});

it('reports popup blocking and close without a save', () => {
  expect(() => openDrawioEditorSession({ ...base, openWindow: () => null, onSave: async () => true }))
    .toThrow(DrawioPopupBlockedError);

  vi.useFakeTimers();
  const editor = popup();
  const onCloseWithoutSave = vi.fn();
  openDrawioEditorSession({
    ...base,
    openWindow: () => editor,
    onSave: async () => true,
    onCloseWithoutSave,
  });
  Object.defineProperty(editor, 'closed', { value: true });
  vi.advanceTimersByTime(600);
  expect(onCloseWithoutSave).toHaveBeenCalledOnce();
});

it('round-trips durable drafts by key until explicitly cleared', () => {
  const draft = {
    wi: 'root-1', diagramId: 'data-model', baseVersion: 1,
    baseCommitSha: 'abc123', xml: '<mxfile/>', savedAt: '2026-09-06T00:00:00.000Z',
  };
  const key = saveDrawioDraft(draft);
  expect(loadDrawioDrafts('root-1')).toEqual([draft]);
  clearDrawioDraft(key);
  expect(loadDrawioDrafts('root-1')).toEqual([]);
});
