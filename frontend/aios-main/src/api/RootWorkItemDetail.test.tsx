// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { WorkItemDto } from './dto';
import { RootWorkItemDetail } from './RootWorkItemDetail';

const rootItem: WorkItemDto = {
  id: 'root-1',
  parent_id: null,
  kind: 'ROOT',
  title: '交付一个仅在本机运行的单页温度换算器。',
  summary: '本地温度换算器',
  description: null,
  objective: '用户输入摄氏温度并换算。',
  status: 'in_progress',
  scope: [],
  exclusions: [],
  outputs: null,
  acceptance_criteria: null,
  required_skills: null,
  responsible_role: 'Owner',
  suggested_assignee: 'owner-1',
  dependency_work_item_ids: [],
};

afterEach(cleanup);

describe('RootWorkItemDetail', () => {
  it('shows UUID, summary, original detail, then PRD content in that order', () => {
    render(
      <RootWorkItemDetail item={rootItem} onCopyResult={() => undefined}>
        <div>PRD 审核占位</div>
      </RootWorkItemDetail>,
    );

    expect(screen.getByRole('button', { name: '复制工单 UUID：root-1' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '本地温度换算器' })).toBeTruthy();
    expect(screen.getByText('交付一个仅在本机运行的单页温度换算器。')).toBeTruthy();
    const article = screen.getByRole('article', { name: '项目需求信息' });
    const text = article.textContent ?? '';
    expect(text.indexOf('#root-1')).toBeLessThan(text.indexOf('本地温度换算器'));
    expect(text.indexOf('本地温度换算器')).toBeLessThan(text.indexOf('详情'));
    expect(text.indexOf('详情')).toBeLessThan(text.indexOf('PRD 审核占位'));
  });

  it('falls back safely when the runtime summary is invalid', () => {
    render(
      <RootWorkItemDetail
        item={{ ...rootItem, summary: '未修剪的摘要 ' }}
        onCopyResult={() => undefined}
      >
        <div>PRD 审核占位</div>
      </RootWorkItemDetail>,
    );

    expect(screen.getByRole('heading', { name: rootItem.title! })).toBeTruthy();
    expect(screen.queryByText('未修剪的摘要 ')).toBeNull();
  });
});
