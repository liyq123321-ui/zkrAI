// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { assetIdFromSrc, assetUrlTransform } from './DocumentAssetImage';

/**
 * PRD 插图曾经两次「破图」，原因不同、症状完全一样，都是「破图 + alt 文本」：
 *   1. 把后端 URL 直接写进 `<img src>` —— 浏览器带不上 Authorization，稳定 401；
 *   2. 让组件去解析 `asset://` —— 但 react-markdown 的默认 urlTransform 先把协议清空了。
 * 这里把第 2 条钉死，因为它不报错、不留痕，只体现为一张破图。
 */

const ASSET_ID = 'asset_6ae63956d6774bb2bdf2f3d405f0d6e5';
const ASSET_URL = `asset://${ASSET_ID}`;
const MARKDOWN = `![image-21.png](${ASSET_URL})`;

afterEach(cleanup);

/** 渲染 markdown，返回 `img` 组件实际收到的 src。 */
function captureImgSrc(markdown: string, urlTransform?: (url: string) => string): string[] {
  const captured: string[] = [];
  render(
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={urlTransform}
      components={{
        img({ src }) {
          captured.push(String(src ?? ''));
          return null;
        },
      }}
    >
      {markdown}
    </ReactMarkdown>,
  );
  return captured;
}

describe('PRD 插图的 asset:// 引用', () => {
  it('能从裸 markdown src 里解析出 asset id', () => {
    expect(assetIdFromSrc(ASSET_URL)).toBe(ASSET_ID);
    expect(assetIdFromSrc('')).toBeNull();
    expect(assetIdFromSrc('/prd-documents/x/assets/y')).toBeNull();
  });

  it('放行 asset: 协议，同时继续拦截 javascript:', () => {
    expect(assetUrlTransform(ASSET_URL)).toBe(ASSET_URL);
    expect(assetUrlTransform('https://example.com/a.png')).toBe('https://example.com/a.png');
    expect(assetUrlTransform('javascript:alert(1)')).toBe('');
  });

  it('react-markdown 默认会把 asset: 静默清成空串 —— 这就是本坑的成因', () => {
    // 若这条断言变红，说明依赖库已放行 asset:，assetUrlTransform 可以退化成直通了。
    expect(defaultUrlTransform(ASSET_URL)).not.toBe(ASSET_URL);
    expect(captureImgSrc(MARKDOWN)).toEqual(['']);
  });

  it('挂上 assetUrlTransform 后 img 能拿到 asset://，而不是空串', () => {
    expect(captureImgSrc(MARKDOWN, assetUrlTransform)).toEqual([ASSET_URL]);
  });
});
