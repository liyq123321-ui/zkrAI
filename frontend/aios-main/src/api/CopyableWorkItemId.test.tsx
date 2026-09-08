// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CopyableWorkItemId } from './CopyableWorkItemId';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('CopyableWorkItemId', () => {
  it('copies the work item UUID without activating the parent card', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const onResult = vi.fn();
    const onParentClick = vi.fn();
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    render(
      <div onClick={onParentClick}>
        <CopyableWorkItemId id="root-1" onResult={onResult} />
      </div>,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制工单 UUID：root-1' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('root-1'));
    expect(onResult).toHaveBeenCalledWith('已复制 UUID：root-1');
    expect(onParentClick).not.toHaveBeenCalled();
  });

  it('reports a compact failure message when copying is unavailable', async () => {
    const onResult = vi.fn();
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });

    render(<CopyableWorkItemId id="root-1" onResult={onResult} />);
    fireEvent.click(screen.getByRole('button', { name: '复制工单 UUID：root-1' }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith('UUID 复制失败'));
  });
});
