import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { SCROLL_PIN_THRESHOLD_PX, isNearBottom } from './scrollPin';

describe('isNearBottom', () => {
  it('keeps a pane pinned at the bottom and just above it', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 600, clientHeight: 400 })).toBe(true); // 0px
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 570, clientHeight: 400 })).toBe(true); // 30px
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 553, clientHeight: 400 })).toBe(true); // 47px
  });

  it('unpins once the reader scrolls away from the bottom', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 552, clientHeight: 400 })).toBe(false); // 48px boundary
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 0, clientHeight: 400 })).toBe(false);
  });

  it('exposes a small threshold so a stray pixel does not unpin', () => {
    expect(SCROLL_PIN_THRESHOLD_PX).toBeGreaterThan(0);
    expect(SCROLL_PIN_THRESHOLD_PX).toBeLessThan(120);
  });
});

const workspaceSource = readFileSync(new URL('./ApiWorkspace.tsx', import.meta.url), 'utf8');
const readPaneSource = readFileSync(
  new URL('./prd-editor/RenderedPrdDocument.tsx', import.meta.url), 'utf8',
);

describe('chat auto-scroll wiring', () => {
  it('only scrolls to the bottom while the conversation is pinned', () => {
    // Regression: background sync used to yank the view to the bottom on every
    // render, even while the reader was browsing history.
    expect(workspaceSource).toMatch(/if \(scroll && chatPinnedRef\.current\)\s*scroll\.scrollTop = scroll\.scrollHeight;/);
    expect(workspaceSource).toMatch(/onScroll=\{handleChatScroll\}/);
    expect(workspaceSource).toMatch(/chatPinnedRef\.current = isNearBottom\(event\.currentTarget\)/);
  });
});

describe('reading-pane streaming follow wiring', () => {
  it('reveals a block only on explicit navigation, not on in-body selection', () => {
    // Regression: 在正文里点选章节也会触发这个 effect，把读者拽到章节开头。现在只有
    // 显式导航（navSeq 递增，来自点目录/导图/批注/进度）才滚动。
    expect(readPaneSource).toMatch(/if \(!navSeq \|\| !selectedBlockId\) return;/);
    expect(readPaneSource).toMatch(/\}, \[navSeq\]\);/);
  });

  it('only follows the streaming block while the reader is pinned', () => {
    expect(readPaneSource).toMatch(/if \(!streamingBlockId \|\| !readPinnedRef\.current\) return;/);
    expect(readPaneSource).toMatch(/readPinnedRef\.current = isNearBottom\(event\.currentTarget\)/);
    expect(readPaneSource).toMatch(/onScroll=\{handleReadScroll\}/);
  });
});
