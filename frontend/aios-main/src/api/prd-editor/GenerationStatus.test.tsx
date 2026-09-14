// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { GenerationStatus } from './GenerationStatus';
import type { AgentRun, Block } from './types';

const blocks = Array.from({ length: 10 }, (_, i) => ({ id: `b${i}`, title: `模块${i}` } as Block));
function run(i: number, status: AgentRun['status']): AgentRun {
  return { id: `r${i}`, block_id: `b${i}`, type: 'generate', status, base_version: 1,
    apply_status: status === 'completed' ? 'applied' : 'pending', error: status === 'failed' ? '调用失败' : null, result: null };
}
beforeEach(() => localStorage.clear());
afterEach(cleanup);

it('shows all messages beyond six with successful and failed outcomes', () => {
  const runs = Array.from({ length: 10 }, (_, i) => run(i, i % 2 ? 'failed' : 'completed'));
  render(<GenerationStatus documentId="d" runs={runs} blocks={blocks} onCancel={vi.fn()} />);
  expect(screen.getByRole('region', { name: '生成消息列表' }).tabIndex).toBe(0);
  expect(screen.getAllByRole('button', { name: /删除模块/ })).toHaveLength(10);
  expect(screen.getAllByText('生成成功')).toHaveLength(5);
  expect(screen.getAllByText('调用失败')).toHaveLength(5);
});

it('dismisses individual messages persistently without changing jobs or other documents', () => {
  const runs = [run(0, 'completed'), run(1, 'failed')], onCancel = vi.fn();
  const view = render(<GenerationStatus documentId="d" runs={runs} blocks={blocks} onCancel={onCancel} />);
  fireEvent.click(screen.getByRole('button', { name: '删除模块0消息' }));
  expect(screen.queryByText('模块0')).toBeNull();
  expect(screen.getByText('模块1')).toBeTruthy();
  expect(onCancel).not.toHaveBeenCalled();
  expect(runs[0].status).toBe('completed');
  view.unmount();
  const restored = render(<GenerationStatus documentId="d" runs={runs} blocks={blocks} onCancel={onCancel} />);
  expect(screen.queryByText('模块0')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '删除模块1消息' }));
  expect(screen.getByText('暂无生成消息')).toBeTruthy();
  restored.unmount();
  render(<GenerationStatus documentId="other" runs={runs} blocks={blocks} onCancel={onCancel} />);
  expect(screen.getByText('模块0')).toBeTruthy();
});

it('keeps running jobs visible and cancellable but never deletable', () => {
  localStorage.setItem('prd-dismissed-messages:d', JSON.stringify(['r1']));
  const active = run(1, 'running'), onCancel = vi.fn();
  render(<GenerationStatus documentId="d" runs={[run(0, 'failed'), active]} blocks={blocks} onCancel={onCancel} />);
  expect(screen.getByRole('region', { name: '生成消息列表' }).firstElementChild?.textContent).toContain('模块1');
  expect(screen.queryByRole('button', { name: '删除模块1消息' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '取消模块1' }));
  expect(onCancel).toHaveBeenCalledWith(active);
});
