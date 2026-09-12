// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PRDEditor } from './PRDEditor';
import { mergeDrafts, fromBlock } from './types';
import type { Snapshot } from './types';
import { diffLines } from './BlockHistory';

let snapshot: Snapshot;
let deferredSave: (() => void) | undefined;
let delaySave = false;
let fetchMock: ReturnType<typeof vi.fn>;
class Source {
  static current: Source;
  onerror: (() => void) | null = null;
  listener: ((event: { data: string }) => void) | undefined;
  constructor() { Source.current = this; }
  addEventListener(_name: string, listener: (event: { data: string }) => void) { this.listener = listener; }
  close() {}
  emit(value: Snapshot) { this.listener?.({ data: JSON.stringify(value) }); }
}
function initial(): Snapshot {
  return { document: { id: 'd', session_id: 's', title: '测试 PRD', background: 'brief', global_rules: '', template: 'custom', plan_status: 'ready', revision: 1, context_revision: 1 },
    blocks: ['A', 'B'].map((title, order) => ({ id: title, document_id: 'd', title, parent_id: null, order, status: 'ready', version: 1, content: title + ' original', summary: '', instruction: '', dependencies: [], fragment_version: null })), runs: [], comments: [] };
}
beforeEach(() => {
  snapshot = initial(); delaySave = false; deferredSave = undefined;
  vi.stubGlobal('EventSource', Source);
  fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    const path = new URL(url).pathname;
    if (options?.method === 'PUT') {
      const body = JSON.parse(options.body as string);
      if (delaySave) await new Promise<void>(resolve => { deferredSave = resolve; });
      const id = path.split('/').at(-1)!;
      snapshot = structuredClone(snapshot);
      const block = snapshot.blocks.find(b => b.id === id)!;
      Object.assign(block, body, { version: block.version + 1 });
      snapshot.document.revision++;
    }
    if (path.endsWith('/apply')) {
      snapshot = structuredClone(snapshot);
      snapshot.runs[0].apply_status = 'applied';
      snapshot.blocks[0].content = 'AI result'; snapshot.blocks[0].version++; snapshot.document.revision++;
    }
    return new Response(JSON.stringify(snapshot), { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function editor() {
  render(<PRDEditor sessionId="s" />);
  return await screen.findByRole('textbox', { name: 'Block 正文' });
}

describe('Block editor draft isolation', () => {
  it('preserves dirty drafts when AI or another client supplies a new version', () => {
    const b = initial().blocks[0], draft = { ...fromBlock(b), content: 'my unsaved text', dirty: true };
    expect(mergeDrafts({ A: draft }, [{ ...b, content: 'AI text', version: 2 }]).A).toBe(draft);
  });
  it('never sends a whole-document generation request on entry', async () => {
    await editor();
    expect(fetchMock.mock.calls.some(([, options]) => String(options?.body).includes('create_spec'))).toBe(false);
    expect(screen.getByLabelText('水平文档规划图')).toBeTruthy();
  });
  it('retains unsaved input when switching blocks', async () => {
    const input = await editor();
    fireEvent.change(input, { target: { value: 'A draft' } });
    const tree = screen.getByRole('navigation', { name: 'Block 目录' });
    fireEvent.click(within(tree).getByRole('button', { name: /B/ }));
    expect((screen.getByRole('textbox', { name: 'Block 正文' }) as HTMLTextAreaElement).value).toBe('B original');
    fireEvent.click(within(tree).getByRole('button', { name: /A/ }));
    expect((screen.getByRole('textbox', { name: 'Block 正文' }) as HTMLTextAreaElement).value).toBe('A draft');
  });
  it('keeps keystrokes entered while a save is in flight', async () => {
    const input = await editor(); delaySave = true;
    fireEvent.change(input, { target: { value: 'First edit' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(deferredSave).toBeTypeOf('function'));
    fireEvent.change(input, { target: { value: 'Second edit while saving' } });
    deferredSave!();
    await waitFor(() => expect(screen.getByText('未保存')).toBeTruthy());
    expect((input as HTMLTextAreaElement).value).toBe('Second edit while saving');
  });
  it('does not apply an AI candidate over unsaved local input', async () => {
    const input = await editor();
    fireEvent.change(input, { target: { value: 'Human local edit' } });
    snapshot.runs = [{ id: 'run', block_id: 'A', type: 'generate', base_version: 1, status: 'completed', apply_status: 'pending', error: null, result: { content: 'AI result' } }];
    Source.current.emit(snapshot);
    await screen.findByText('AI 已完成，等待你保存当前输入');
    expect((input as HTMLTextAreaElement).value).toBe('Human local edit');
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/apply'))).toBe(false);
  });
  it('applies a current candidate when there are no local changes', async () => {
    await editor();
    snapshot.runs = [{ id: 'run', block_id: 'A', type: 'generate', base_version: 1, status: 'completed', apply_status: 'pending', error: null, result: { content: 'AI result' } }];
    Source.current.emit(snapshot);
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Block 正文' }) as HTMLTextAreaElement).value).toBe('AI result'));
  });
  it('produces a line diff with unchanged lines intact', () => {
    expect(diffLines('one\ntwo\nthree', 'one\nnew\nthree')).toEqual([
      { kind: 'same', text: 'one' }, { kind: 'remove', text: 'two' }, { kind: 'add', text: 'new' }, { kind: 'same', text: 'three' },
    ]);
  });
});
