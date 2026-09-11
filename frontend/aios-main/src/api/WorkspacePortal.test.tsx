// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspacePortal } from './WorkspacePortal';

const overview = {
  actor_id: 'owner-1',
  connection: {
    status: 'online',
    base_url: 'http://192.168.240.70:9971/api/v1',
    web_url: 'http://192.168.240.70:9972',
    message: '远程 WeKnora 已连接',
  },
  spaces: [
    {
      kind: 'personal', project_id: null, session_id: null, name: 'owner-1 的个人空间',
      description: '个人知识入口', phase: 'ACTIVE', tenant_id: 10000, remote_status: 'ready',
      knowledge_base_id: '44444444-4444-4444-8444-444444444444', files: [
        { id: 'personal-doc-1', name: '新建文档.md', folder_path: '我的文档', file_type: 'md', parse_status: 'completed', chunk_count: 0, updated_at: null },
        { id: 'personal-code-1', name: '代码草稿.md', folder_path: 'Code', file_type: 'md', parse_status: 'completed', chunk_count: 0, updated_at: null },
      ], assets: [
        { kind: 'knowledge', name: '9 个知识库', folder: 'knowledge', status: 'ready', revision: null, source_url: null },
      ], created_at: '2026-09-11T00:00:00Z', storage_used: 20, storage_quota: 100,
    },
    {
      kind: 'project', project_id: 'project-1', session_id: 'session-1', name: '项目管理系统',
      description: '共享项目空间', phase: 'REVIEW', tenant_id: 10001, remote_status: 'ready',
      knowledge_base_id: '11111111-1111-4111-8111-111111111111', files: [], assets: [
        { kind: 'prd', name: 'PRD-v9.md', folder: 'docs', status: 'ready', revision: 9, source_url: '/prd/root-1/v/9' },
        { kind: 'prototype', name: 'frontend-prototype-v9.html', folder: 'prototype', status: 'not_generated', revision: 9, source_url: null },
        { kind: 'code', name: 'code/', folder: 'code', status: 'reserved', revision: null, source_url: null },
      ], created_at: '2026-09-11T00:00:00Z',
    },
  ],
  knowledge_bases: [
    { id: '22222222-2222-4222-8222-222222222222', tenant_id: 10000, name: 'AI Agent', description: '经典资料', knowledge_count: 17, chunk_count: 3456, processing_count: 0, updated_at: null },
  ],
  skills: [
    { id: '33333333-3333-4333-8333-333333333333', name: '代码工程-单元测试生成', domain: '代码工程', capability: '单元测试生成', description: '生成测试', version: null, installed: false, file_count: 1 },
  ],
};

describe('WorkspacePortal', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, options?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      if (path === '/workspace/overview') return new Response(JSON.stringify(overview), { status: 200 });
      if (path.includes('/knowledge-bases/')) return new Response(JSON.stringify({ items: [{ id: 'doc-1', name: 'ReAct.html', folder_path: 'papers', file_type: 'html', parse_status: 'completed', chunk_count: 220, updated_at: null }], total: 1 }), { status: 200 });
      if (path.includes('/skills/')) return new Response(JSON.stringify({ items: [{ path: 'SKILL.md', size: 128, type: 'file' }] }), { status: 200 });
      if (path === '/workspace/sync' && options?.method === 'POST') return new Response(JSON.stringify({ ...overview, sync_result: { created_spaces: 1, uploaded_files: 2, project_count: 1 } }), { status: 200 });
      return new Response('{}', { status: 404 });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('browses owner spaces, knowledge documents, and skill files', async () => {
    render(<WorkspacePortal />);

    expect((await screen.findAllByText('owner-1 的个人空间')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /我的文档/ }));
    expect(await screen.findByText('新建文档.md')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '返回上一级' }));
    expect(screen.getByRole('button', { name: /Code/ })).toBeTruthy();
    fireEvent.click(screen.getByText('项目管理系统'));
    expect(screen.getByText('docs/PRD-v9.md')).toBeTruthy();
    expect(screen.getByText('当前项目尚未生成此文件')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /知识库/ }));
    expect(await screen.findByText('ReAct.html')).toBeTruthy();
    expect(screen.getByText('3456 切片')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /技能库/ }));
    expect(await screen.findByText('SKILL.md')).toBeTruthy();
    expect(screen.getByText('仅目录可用')).toBeTruthy();
  });

  it('shows the result of an explicit remote sync', async () => {
    render(<WorkspacePortal />);
    const button = await screen.findByRole('button', { name: '同步项目空间' });
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText(/新建 1 个空间、上传 2 个文件/)).toBeTruthy());
  });
});
