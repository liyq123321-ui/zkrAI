// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRef } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PRDEditor, type PRDEditorHandle } from './PRDEditor';
import { mergeDrafts, fromBlock } from './types';
import type { Snapshot } from './types';
import { diffLines } from './BlockHistory';

vi.mock('./univer/UniverEditor', async () => {
  const React = await import('react');
  return {
    UniverEditor: React.forwardRef(function MockUniverEditor({ blocks, selectedBlockId, onBlockContentChange, onViewportBlockChange }: {
      blocks: Snapshot['blocks']; selectedBlockId: string; onBlockContentChange: (id: string, content: string) => void;
      onViewportBlockChange?: (id: string) => void;
    }, ref: React.ForwardedRef<{ focusBlock: (id: string) => void }>) {
      React.useImperativeHandle(ref, () => ({ focusBlock: () => {} }));
      const block = blocks.find(item => item.id === selectedBlockId);
      return <><textarea aria-label="Univer PRD 文档" value={block?.content ?? ''} onChange={event => onBlockContentChange(selectedBlockId, event.target.value)} />
        <button aria-label="模拟正文滚到 B" onClick={() => onViewportBlockChange?.('B')} /></>;
    }),
  };
});

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
  HTMLElement.prototype.scrollIntoView = vi.fn();
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
  await screen.findByLabelText('PRD 图文阅读');
  fireEvent.click(screen.getByRole('button', { name: '编辑源码' }));
  return await screen.findByRole('textbox', { name: 'Univer PRD 文档' });
}

describe('Block editor draft isolation', () => {
  it('preserves dirty drafts when AI or another client supplies a new version', () => {
    const b = initial().blocks[0], draft = { ...fromBlock(b), content: 'my unsaved text', dirty: true };
    expect(mergeDrafts({ A: draft }, [{ ...b, content: 'AI text', version: 2 }]).A).toBe(draft);
  });
  it('never sends a whole-document generation request on entry', async () => {
    await editor();
    expect(fetchMock.mock.calls.some(([, options]) => String(options?.body).includes('create_spec'))).toBe(false);
    expect(screen.getByRole('button', { name: '展开 PRD 思维导图' })).toBeTruthy();
    expect(screen.getByLabelText('文档规划缩略图')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'PRD 思维导图与原文联动' })).toBeNull();
  });
  it('moves the compact mind map down and keeps it attached to the directory', async () => {
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      const element = this as HTMLElement;
      if (element.classList.contains('prd-columns')) return { top: 40, height: 800 } as DOMRect;
      if (element.classList.contains('prd-rendered-document')) return { top: 240, height: 620 } as DOMRect;
      if (element.classList.contains('prd-map-thumbnail')) return { top: 40, height: 116 } as DOMRect;
      return { top: 0, height: 0 } as DOMRect;
    });
    render(<PRDEditor sessionId="s" />);
    await screen.findByLabelText('PRD 图文阅读');

    const thumbnail = screen.getByRole('button', { name: '展开 PRD 思维导图' });
    const directory = screen.getByRole('navigation', { name: 'Block 目录' }).closest<HTMLElement>('.prd-directory');
    await waitFor(() => expect(thumbnail.style.marginTop).toBe('84px'));
    expect(directory?.style.marginTop).toBe('0px');
    expect(directory?.style.height).toBe('620px');
    rect.mockRestore();
  });
  it('expands the linked PRD mind map beside the source document', async () => {
    await editor();
    fireEvent.click(screen.getByRole('button', { name: '展开 PRD 思维导图' }));
    const map = screen.getByRole('region', { name: 'PRD 思维导图与原文联动' });
    expect(screen.queryByRole('region', { name: '生成队列' })).toBeNull();
    fireEvent.click(within(map).getByRole('button', { name: /B.*定位原文/ }));
    await waitFor(() => expect(screen.getByText('当前 Block · B')).toBeTruthy());
    expect((screen.getByRole('textbox', { name: 'Univer PRD 文档' }) as HTMLTextAreaElement).value).toBe('B original');
    fireEvent.click(within(map).getByRole('button', { name: '收起思维导图' }));
    expect(screen.getByRole('region', { name: '生成队列' })).toBeTruthy();
  });
  it('retains unsaved input when switching blocks', async () => {
    const input = await editor();
    fireEvent.change(input, { target: { value: 'A draft' } });
    const tree = screen.getByRole('navigation', { name: 'Block 目录' });
    fireEvent.click(within(tree).getByRole('button', { name: /B/ }));
    expect((screen.getByRole('textbox', { name: 'Univer PRD 文档' }) as HTMLTextAreaElement).value).toBe('B original');
    fireEvent.click(within(tree).getByRole('button', { name: /A/ }));
    expect((screen.getByRole('textbox', { name: 'Univer PRD 文档' }) as HTMLTextAreaElement).value).toBe('A draft');
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
  it('keeps edits local until the user explicitly clicks save', async () => {
    const input = await editor();
    fireEvent.change(input, { target: { value: 'Manual save only' } });
    await new Promise(resolve => setTimeout(resolve, 1000));
    expect(fetchMock.mock.calls.some(([url, options]) => new URL(String(url)).pathname.includes('/blocks/') && options?.method === 'PUT')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => new URL(String(url)).pathname.includes('/blocks/') && options?.method === 'PUT')).toBe(true));
  });
  it('keeps a reading selection across an unchanged background snapshot', async () => {
    render(<PRDEditor sessionId="s" />);
    await screen.findByLabelText('PRD 图文阅读');
    const paragraph = screen.getByText('A original');
    const range = document.createRange();
    range.selectNodeContents(paragraph);
    Object.defineProperty(range, 'getBoundingClientRect', { value: () => ({ left: 200, top: 160, width: 120, height: 20 }) });
    const browserSelection = window.getSelection()!;
    browserSelection.removeAllRanges();
    browserSelection.addRange(range);
    fireEvent.mouseUp(paragraph);
    expect(screen.getByRole('toolbar', { name: '选中文字操作' })).toBeTruthy();

    await act(async () => { Source.current.emit(structuredClone(snapshot)); });

    expect(window.getSelection()?.toString()).toBe('A original');
    expect(screen.getByRole('toolbar', { name: '选中文字操作' })).toBeTruthy();
  });
  it('asks whether to save when leaving with local edits', async () => {
    const input = await editor();
    fireEvent.change(input, { target: { value: 'Unsaved before leaving' } });
    fireEvent.click(screen.getByRole('link', { name: '返回项目' }));
    const dialog = screen.getByRole('dialog', { name: '未保存更改' });
    expect(within(dialog).getByText('尚未保存，是否保存？')).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: '保存并离开' })).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: '不保存并离开' })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '继续编辑' }));
    expect(screen.queryByRole('dialog', { name: '未保存更改' })).toBeNull();
  });
  it('closes the embedded editor straight away with an icon-only back control', async () => {
    const onClose = vi.fn();
    const ref = createRef<PRDEditorHandle>();
    render(<PRDEditor ref={ref} sessionId="s" embedded onClose={onClose} />);
    await screen.findByLabelText('PRD 图文阅读');

    // The header control is a bare arrow: the view it returns to is the project
    // top-level graph, so the label is carried by the accessible name instead.
    const back = screen.getByRole('button', { name: '关闭 PRD 编辑器' });
    expect(back.textContent).toBe('');

    fireEvent.click(back);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog', { name: '未保存更改' })).toBeNull();

    // The imperative path (tab switch, new task) takes the same shortcut.
    onClose.mockClear();
    act(() => ref.current!.requestClose(onClose));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog', { name: '未保存更改' })).toBeNull();
  });
  it('still stops to save local edits before closing the embedded editor', async () => {
    const onClose = vi.fn();
    render(<PRDEditor sessionId="s" embedded onClose={onClose} />);
    await screen.findByLabelText('PRD 图文阅读');
    fireEvent.click(screen.getByRole('button', { name: '编辑源码' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Univer PRD 文档' }), { target: { value: 'Unsaved before closing' } });

    fireEvent.click(screen.getByRole('button', { name: '关闭 PRD 编辑器' }));

    const dialog = screen.getByRole('dialog', { name: '未保存更改' });
    expect(within(dialog).getByText('尚未保存，是否保存？')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: '保存并离开' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
  it('reports the active PRD Agent stage and current Block to the host workspace', async () => {
    snapshot.runs = [{
      id: 'revision-run', block_id: null, type: 'document_revision', base_version: 1,
      status: 'running', apply_status: 'pending', error: null,
      result: { progress_stage: 'revising', resolution_round: 1, completed: 1, total: 2, current_block_id: 'B',
        stream_blocks: { B: '正在补充用户流程。继续写入验收范围。' }, stream_sentence_count: 2 },
    }];
    const onAgentProgress = vi.fn();
    render(<PRDEditor sessionId="s" embedded onAgentProgress={onAgentProgress} />);
    await screen.findByLabelText('PRD 图文阅读');

    await waitFor(() => expect(onAgentProgress.mock.calls.some(([progress]) => progress?.runId === 'revision-run')).toBe(true));
    const progress = onAgentProgress.mock.calls.map(([value]) => value).find(value => value?.runId === 'revision-run');
    expect(progress).toMatchObject({
      status: 'running', title: '全文自动修订', stage: '正在生成第 1 轮修订',
      completed: 1, total: 2, currentBlockId: 'B', currentBlockTitle: 'B',
      activityText: '实时写入：正在补充用户流程。继续写入验收范围。',
    });
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
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Univer PRD 文档' }) as HTMLTextAreaElement).value).toBe('AI result'));
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

it('enables the next stage only after the current whole-document review has no pending issues', async () => {
  await editor();
  const next = screen.getByRole('button', { name: '进入下一阶段' }) as HTMLButtonElement;
  expect(next.disabled).toBe(true);
  snapshot = structuredClone(snapshot);
  snapshot.runs = [{
    id: 'clean-review', block_id: null, type: 'document_review', base_version: snapshot.document.revision,
    status: 'completed', apply_status: 'applied', stale: false, error: null,
    result: { summary: '全文审核通过', issues: [] },
  }];
  Source.current.emit(snapshot);
  await waitFor(() => expect(next.disabled).toBe(false));
  fireEvent.click(next);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/submit'))).toBe(true));
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

it('always uses the continuous full-document canvas while keeping Block tools', async () => {
  await editor();
  expect(screen.queryByRole('button', { name: 'AI 修改' })).toBeNull();
  expect(screen.queryByRole('button', { name: '浏览全文' })).toBeNull();
  expect(await screen.findByText('全文 DOCX 编辑')).toBeTruthy();
  expect(screen.getByRole('textbox', { name: 'Univer PRD 文档' })).toBeTruthy();
  expect(screen.queryByRole('textbox', { name: 'PRD 全文' })).toBeNull();
  expect(screen.getByRole('navigation', { name: 'Block 目录' })).toBeTruthy();
  expect(screen.getByRole('region', { name: '当前 Block 工具' })).toBeTruthy();
  expect(screen.getByRole('region', { name: '生成队列' })).toBeTruthy();
  expect(screen.getByRole('textbox', { name: '修改意见' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '审核全文' })).toBeTruthy();
});

it('places the editor before the whole-document review findings', async () => {
  snapshot.runs = [{ id: 'review', block_id: null, type: 'document_review', base_version: 1, status: 'completed', apply_status: 'applied', error: null,
    result: { summary: '审核摘要', issues: [{ severity: 'warning', block_ids: ['A'], description: '审核项', suggestion: '调整' }] } }];
  await editor();
  const canvas = screen.getByRole('textbox', { name: 'Univer PRD 文档' });
  const report = screen.getByRole('region', { name: '全文审核意见' });
  expect(canvas.compareDocumentPosition(report) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('follows the visible document Block without requiring a directory click', async () => {
  await editor();
  fireEvent.click(screen.getByRole('button', { name: '模拟正文滚到 B' }));
  await waitFor(() => expect(screen.getByText('当前 Block · B')).toBeTruthy());
  const tree = screen.getByRole('navigation', { name: 'Block 目录' });
  expect(within(tree).getByRole('button', { name: /B/ }).parentElement?.classList.contains('is-selected')).toBe(true);
});

it('adds a whole-document comment to all blocks and requests one holistic review', async () => {
  await editor();
  fireEvent.click(screen.getByRole('checkbox', { name: '全文批注' }));
  expect((screen.getByRole('spinbutton', { name: '起始行' }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('spinbutton', { name: '结束行' }) as HTMLInputElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole('textbox', { name: '修改意见' }), { target: { value: '请统一全文术语' } });
  fireEvent.click(screen.getByRole('button', { name: '添加全文批注' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/comments'))).toBe(true));
  const comment = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/comments'))!;
  expect(JSON.parse(comment[1]!.body as string).targets).toEqual([{ block_id: 'A', base_version: 1 }, { block_id: 'B', base_version: 1 }]);
  fireEvent.click(screen.getByRole('button', { name: '审核全文' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/review'))).toBe(true));
  const review = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/review'))!;
  expect(JSON.parse(review[1]!.body as string)).toMatchObject({ expected_revision: 1 });
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/runs'))).toBe(false);
});

it('defaults comments to the current Block rather than the whole document', async () => {
  await editor();
  fireEvent.change(screen.getByRole('textbox', { name: '修改意见' }), { target: { value: '只改当前部分' } });
  fireEvent.click(screen.getByRole('button', { name: '添加批注 · 1 个 Block' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/comments'))).toBe(true));
  const comment = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/comments'))!;
  expect(JSON.parse(comment[1]!.body as string).targets).toEqual([{ block_id: 'A', base_version: 1 }]);
});

it('renders a validated sentence stream inside the active Block', async () => {
  const input = await editor();
  snapshot = structuredClone(snapshot);
  snapshot.runs = [{ id: 'stream', block_id: 'A', type: 'generate', base_version: 1, status: 'running', apply_status: 'pending', error: null,
    result: { stream_content: '第一句。第二句。', stream_sentence_count: 2, stream_complete: false } }];
  Source.current.emit(snapshot);
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe('第一句。第二句。'));
  expect(screen.getByText('正在写入全文 · 2 句')).toBeTruthy();
});
