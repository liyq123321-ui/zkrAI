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
    expect(screen.getByRole('button', { name: '展开文档规划图' })).toBeTruthy();
    expect(screen.getByLabelText('文档规划缩略图')).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: '文档规划图' })).toBeNull();
  });
  it('opens the full document plan in a modal and closes it again', async () => {
    await editor();
    fireEvent.click(screen.getByRole('button', { name: '展开文档规划图' }));
    const dialog = screen.getByRole('dialog', { name: '文档规划图' });
    expect(within(dialog).getByLabelText('水平文档规划图')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭文档规划图' }));
    expect(screen.queryByRole('dialog', { name: '文档规划图' })).toBeNull();
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

it('requests first-draft automation on entry, leaving eligibility to the server', async () => {
  await editor();
  const request = fetchMock.mock.calls.find(([url, options]) => new URL(url).pathname === '/prd-documents' && options?.method === 'POST');
  expect(JSON.parse(request![1]!.body as string)).toMatchObject({ session_id: 's', auto_generate: true });
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/runs'))).toBe(false);
  expect(screen.queryByText(/首版正在按模块连续生成/)).toBeNull();
});

it('shows first-draft progress and lets the user stop the entire sequence', async () => {
  snapshot.runs = [{ id: 'initial', block_id: null, type: 'initial', base_version: 1, status: 'running', apply_status: 'pending', error: null, result: { completed: 1, total: 2 } }];
  await editor();
  expect(screen.getByText(/首版正在按模块连续生成（1\/2）/)).toBeTruthy();
  expect((screen.getByRole('button', { name: '重新生成' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '停止自动生成' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/runs/initial/cancel'))).toBe(true));
});

it('preserves a local draft when the server applies a first-generation result', async () => {
  const input = await editor();
  fireEvent.change(input, { target: { value: 'Human input while auto-generating' } });
  snapshot = structuredClone(snapshot);
  snapshot.blocks[0].content = 'Generated first draft'; snapshot.blocks[0].version = 2; snapshot.document.revision++;
  Source.current.emit(snapshot);
  await screen.findByText('保存版本已变化，人工输入已保留');
  expect((input as HTMLTextAreaElement).value).toBe('Human input while auto-generating');
});

it('does not auto-apply a candidate rejected by first-draft automation', async () => {
  snapshot.runs = [
    { id: 'initial', block_id: null, type: 'initial', base_version: 1, status: 'failed', apply_status: 'pending', error: '自动生成已停止', result: { completed: 0, total: 2 } },
    { id: 'child', request_id: 'initial:A', block_id: 'A', type: 'generate', base_version: 1, status: 'completed', apply_status: 'pending', error: null, result: { content: '' } },
  ];
  await editor();
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/apply'))).toBe(false);
  expect((screen.getByRole('button', { name: '重新生成' }) as HTMLButtonElement).disabled).toBe(false);
});

it('replaces AI modify with full-document browsing and keeps the three-column tools', async () => {
  await editor();
  expect(screen.queryByRole('button', { name: 'AI 修改' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '浏览全文' }));
  const full = await screen.findByRole('textbox', { name: 'PRD 全文' });
  expect((full as HTMLTextAreaElement).value).toContain('A original');
  expect((full as HTMLTextAreaElement).value).toContain('B original');
  expect((full as HTMLTextAreaElement).readOnly).toBe(true);
  expect(screen.getByRole('navigation', { name: 'Block 目录' })).toBeTruthy();
  expect(screen.getByRole('region', { name: '生成队列' })).toBeTruthy();
  expect(screen.getByRole('textbox', { name: '修改意见' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '审核全文' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '返回分块编辑' }));
  await screen.findByRole('textbox', { name: 'Block 正文' });
});

it('adds a whole-document comment to all blocks and requests one holistic review', async () => {
  await editor();
  fireEvent.click(screen.getByRole('button', { name: '浏览全文' }));
  await screen.findByRole('textbox', { name: 'PRD 全文' });
  fireEvent.change(screen.getByRole('textbox', { name: '修改意见' }), { target: { value: '请统一全文术语' } });
  fireEvent.click(screen.getByRole('button', { name: '添加批注 · 2 个块' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/comments'))).toBe(true));
  const comment = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/comments'))!;
  expect(JSON.parse(comment[1]!.body as string).targets).toEqual([{ block_id: 'A', base_version: 1 }, { block_id: 'B', base_version: 1 }]);
  fireEvent.click(screen.getByRole('button', { name: '审核全文' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/review'))).toBe(true));
  const review = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/review'))!;
  expect(JSON.parse(review[1]!.body as string)).toMatchObject({ expected_revision: 1 });
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/runs'))).toBe(false);
});

it('prevents stale full-text line comments when another writer changes a block', async () => {
  await editor();
  fireEvent.click(screen.getByRole('button', { name: '浏览全文' }));
  await screen.findByRole('textbox', { name: 'PRD 全文' });
  snapshot = structuredClone(snapshot); snapshot.blocks[0].content = 'Changed remotely'; snapshot.blocks[0].version++; snapshot.document.revision++;
  Source.current.emit(snapshot);
  await screen.findByText('正文或背景已更新，请更新全文后再批注或审核。');
  fireEvent.change(screen.getByRole('textbox', { name: '修改意见' }), { target: { value: 'comment on old text' } });
  fireEvent.click(screen.getByRole('button', { name: '添加批注 · 2 个块' }));
  await screen.findByRole('alert');
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/comments'))).toBe(false);
});
