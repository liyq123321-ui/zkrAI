// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PrdErDiagramDto } from './dto';

vi.mock('./DrawioErDiagram', () => ({
  DrawioErDiagram: ({ diagram }: { diagram: PrdErDiagramDto }) => (
    <section aria-label={`ER 图：${diagram.title}`}>interactive diagram</section>
  ),
}));

import { PrdDocumentViews } from './PrdDocumentViews';

const diagram: PrdErDiagramDto = {
  diagram_id: 'data-model',
  title: '核心数据模型',
  after_section: 'core_objects',
  anchor: 'firstflight-er-data-model-deadbeef',
  drawio_xml: '<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>',
};

afterEach(cleanup);

it('replaces only a matching body anchor and never mounts a diagram in Diff', () => {
  const link = `[ER 图：核心数据模型](#${diagram.anchor})`;
  render(
    <PrdDocumentViews
      content={`# PRD\n\n${link}`}
      diagrams={[diagram]}
      version={2}
      patch={`@@ -1,1 +1,3 @@\n # PRD\n+\n+${link}\n`}
      commentable={null}
      ready={false}
      selectedLine={null}
      onSelectLine={() => undefined}
    />,
  );

  expect(screen.queryByRole('region', { name: 'ER 图：核心数据模型' })).toBeNull();
  expect(within(screen.getByRole('region', { name: 'PRD Diff' })).getByText(`+${link}`)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '正文' }));
  expect(screen.getByRole('region', { name: 'ER 图：核心数据模型' })).toBeTruthy();
  expect(screen.queryByText(/mxGraphModel/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Diff' }));
  expect(screen.queryByRole('region', { name: 'ER 图：核心数据模型' })).toBeNull();
});

it('keeps ordinary and unresolved diagram links as safe external links', () => {
  render(
    <PrdDocumentViews
      content={'[文档](https://example.com)\n\n[ER 图：缺失](#firstflight-er-missing-deadbeef)'}
      diagrams={[]}
      version={1}
      patch={null}
      commentable={null}
      ready={false}
      selectedLine={null}
      onSelectLine={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '正文' }));

  expect(screen.getByRole('link', { name: '文档' }).getAttribute('target')).toBe('_blank');
  expect(screen.getByRole('link', { name: 'ER 图：缺失' }).getAttribute('target')).toBe('_blank');
});

it('renders multiple matching diagrams in their Markdown order', () => {
  const second = {
    ...diagram,
    diagram_id: 'audit-model',
    title: '审计数据模型',
    anchor: 'firstflight-er-audit-model-cafebabe',
  };
  render(
    <PrdDocumentViews
      content={`[ER 图：${diagram.title}](#${diagram.anchor})\n\n[ER 图：${second.title}](#${second.anchor})`}
      diagrams={[diagram, second]}
      version={1}
      patch={null}
      commentable={null}
      ready={false}
      selectedLine={null}
      onSelectLine={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '正文' }));

  expect(screen.getByRole('region', { name: 'ER 图：核心数据模型' })).toBeTruthy();
  expect(screen.getByRole('region', { name: 'ER 图：审计数据模型' })).toBeTruthy();
});

it('mounts an escaped diagram title as one body anchor without creating a forged external link', () => {
  const unusual = {
    ...diagram,
    title: 'Orders](https://evil.example) [model',
  };
  render(
    <PrdDocumentViews
      content={`[ER 图：Orders\\](https://evil.example) \\[model](#${diagram.anchor})`}
      diagrams={[unusual]}
      version={1}
      patch={null}
      commentable={null}
      ready={false}
      selectedLine={null}
      onSelectLine={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '正文' }));

  expect(screen.getByRole('region', { name: `ER 图：${unusual.title}` })).toBeTruthy();
  expect(screen.queryByRole('link', { name: /Orders/ })).toBeNull();
});

it('explains a skipped prototype and lets the user start it manually', () => {
  const generate = vi.fn();
  render(
    <PrdDocumentViews
      content="# PRD"
      version={1}
      patch={null}
      commentable={null}
      prototypeSkippedForReview
      ready={false}
      selectedLine={null}
      onSelectLine={() => undefined}
      onGeneratePrototype={generate}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'HTML 原型' }));
  expect(screen.getByText(/存在待处理审核项/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '手动生成 HTML 原型' }));
  expect(generate).toHaveBeenCalledOnce();
});
