// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { emptyPolicy, PrdPolicies } from './PrdPolicies';

beforeEach(() => { localStorage.clear(); localStorage.setItem('ff_auth_user', JSON.stringify({ username: 'alice' })); });
afterEach(cleanup);

it('recovers unsaved policy text without saving or consuming it', () => {
  const save = vi.fn();
  const first = render(<PrdPolicies policies={[]} documentId="doc" blocks={[]} busy={false} onSave={save} />);
  fireEvent.change(screen.getByLabelText('决定或偏好内容'), { target: { value: '游客只读' } });
  first.unmount();
  render(<PrdPolicies policies={[]} documentId="doc" blocks={[]} busy={false} onSave={save} />);
  expect((screen.getByLabelText('决定或偏好内容') as HTMLTextAreaElement).value).toBe('');
  fireEvent.click(screen.getByText('恢复到输入区'));
  expect((screen.getByLabelText('决定或偏好内容') as HTMLTextAreaElement).value).toBe('游客只读');
  expect(save).not.toHaveBeenCalled();
});

it('keeps stale policy recovery available for download instead of overwriting a newer decision', () => {
  const save = vi.fn();
  const original = { ...emptyPolicy(), id: 'p1', document_id: 'doc', text: '旧决定', reason: '确认', updated_at: '2026-10-01' };
  const first = render(<PrdPolicies policies={[original]} seed={original} documentId="doc" blocks={[]} busy={false} onSave={save} />);
  fireEvent.change(screen.getByLabelText('决定或偏好内容'), { target: { value: '未保存草稿' } });
  first.unmount();
  render(<PrdPolicies policies={[{ ...original, text: '新决定', updated_at: '2026-10-07' }]} documentId="doc" blocks={[]} busy={false} onSave={save} />);
  fireEvent.click(screen.getByText('恢复到输入区'));
  expect(screen.getByRole('alert').textContent).toContain('已被更新');
  expect(screen.getByText('下载恢复副本')).toBeTruthy();
  expect(save).not.toHaveBeenCalled();
});
