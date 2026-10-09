// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BlockTree } from './BlockTree';
import type { Block } from './types';

function block(id: string, title: string): Block {
  return {
    id, document_id: 'doc', title, parent_id: null, order: 1, status: 'ready', version: 1,
    content: '', summary: '', instruction: '', dependencies: [], fragment_version: null,
  };
}

afterEach(cleanup);

describe('directory click only views', () => {
  it('selects a chapter on the first click', () => {
    const onSelect = vi.fn();
    render(<BlockTree blocks={[block('a', '产品背景'), block('b', '用户问题')]} selected=""
      checked={new Set()} onSelect={onSelect} onCheck={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /用户问题/ }));
    expect(onSelect).toHaveBeenCalledWith('b');
  });

  it('clicking the already selected chapter clears it again', () => {
    const onSelect = vi.fn();
    render(<BlockTree blocks={[block('a', '产品背景'), block('b', '用户问题')]} selected="b"
      checked={new Set()} onSelect={onSelect} onCheck={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /用户问题/ }));
    expect(onSelect).toHaveBeenCalledWith('');
  });

  it('keeps the tick box independent from the click', () => {
    // 勾选＝划定命令范围，点击＝只是查看：两者互不干扰。
    const onSelect = vi.fn(), onCheck = vi.fn();
    render(<BlockTree blocks={[block('a', '产品背景'), block('b', '用户问题')]} selected="b"
      checked={new Set(['b'])} onSelect={onSelect} onCheck={onCheck} />);
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 用户问题' }));
    expect(onCheck).toHaveBeenCalledWith('b');
    expect(onSelect).not.toHaveBeenCalled();
  });
});
