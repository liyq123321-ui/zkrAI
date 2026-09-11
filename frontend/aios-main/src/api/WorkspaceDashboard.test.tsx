// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceDashboard } from './WorkspaceDashboard';

describe('WorkspaceDashboard', () => {
  it('opens the shared workspace from the green dashboard action', () => {
    const onOpenWorkspace = vi.fn();

    render(
      <WorkspaceDashboard
        projects={[]}
        currentUserId="owner-1"
        loading={false}
        onRefresh={vi.fn()}
        onOpenWorkspace={onOpenWorkspace}
        onViewAllProjects={vi.fn()}
        onOpenProject={vi.fn()}
        onOpenWorkItem={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '进入工作空间' }));

    expect(onOpenWorkspace).toHaveBeenCalledOnce();
  });
});
