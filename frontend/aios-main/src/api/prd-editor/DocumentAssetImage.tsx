import { useEffect, useState } from 'react';
import { defaultUrlTransform } from 'react-markdown';
import { appConfig } from '../config';
import { getToken } from '../../auth/session';
import { documentPath } from './api';

/**
 * PRD 插图（prd_document_assets）只能用「带鉴权的 fetch」取回。
 *
 * 端点 `GET /prd-documents/{id}/assets/{asset_id}` 会经过 `ActorResolver.resolve()`；
 * 在 `FIRSTFLIGHT_IDENTITY_MODE=auth` 下它要求 `Authorization: Bearer <token>`，
 * 而浏览器为 `<img src>` 发起的请求**永远带不上这个头** —— 于是把后端 URL 直接写进
 * `src` 会稳定拿到 401 `IDENTITY_REQUIRED`，页面上表现为「破图 + alt 文本」。
 *
 * 这里统一 fetch → blob → objectURL，再交给 `<img>`；与既有的
 * `loadWorkspaceChatImage()`（对话图片）是同一套解法。
 */

const ASSET_SRC = /^asset:\/\/([A-Za-z0-9_-]+)$/;

/**
 * 必须显式挂到每个 `<ReactMarkdown urlTransform={…}>` 上。
 *
 * react-markdown v10 的默认 URL 白名单只放行 `http/https/irc/ircs/mailto/xmpp`，
 * 其余协议一律**静默清成空串**（`defaultUrlTransform('asset://x') === ''`）。
 * 于是 `img` 组件收到的是 `src=""`，`assetIdFromSrc()` 返回 null、落到 fallback，
 * 页面表现为「破图 + alt 文本」—— 与「裸 <img> 打后端 401」的症状一模一样，极易误判。
 *
 * 放行 `asset:` 的同时保留对 javascript: 等其它协议的过滤。
 */
export function assetUrlTransform(url: string): string {
  return url.startsWith('asset://') ? url : defaultUrlTransform(url);
}

/** documentId/assetId → objectURL。同一张图被多个视图引用时只取一次。 */
const objectUrls = new Map<string, string>();
/** 同一张图并发加载时共享同一个请求，避免重复 fetch。 */
const inflight = new Map<string, Promise<string>>();

const keyOf = (documentId: string, assetId: string) => `${documentId}/${assetId}`;

/** 把 markdown 里的 `asset://<id>` 引用解析成裸的 asset id。 */
export function assetIdFromSrc(src: string | undefined | null): string | null {
  if (!src) return null;
  const matched = ASSET_SRC.exec(src.trim());
  return matched ? matched[1] : null;
}

export function documentAssetUrl(documentId: string, assetId: string): Promise<string> {
  const key = keyOf(documentId, assetId);
  const cached = objectUrls.get(key);
  if (cached) return Promise.resolve(cached);
  const running = inflight.get(key);
  if (running) return running;
  const request = (async () => {
    const token = getToken();
    const response = await fetch(
      `${appConfig.apiBaseUrl}${documentPath(documentId)}/assets/${encodeURIComponent(assetId)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    );
    if (!response.ok) throw new Error(`asset ${assetId} -> HTTP ${response.status}`);
    const url = URL.createObjectURL(await response.blob());
    objectUrls.set(key, url);
    return url;
  })();
  inflight.set(key, request);
  // 成功后由 objectUrls 继续持有，失败要清掉在途记录，否则会缓存一个 rejected promise。
  request.then(() => inflight.delete(key), () => inflight.delete(key));
  return request;
}

export function useDocumentAssetUrl(documentId: string, assetId: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(
    () => (assetId ? objectUrls.get(keyOf(documentId, assetId)) ?? null : null),
  );
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!assetId) return;
    let live = true;
    setFailed(false);
    documentAssetUrl(documentId, assetId).then(
      value => { if (live) setUrl(value); },
      () => { if (live) setFailed(true); },
    );
    return () => { live = false; };
  }, [documentId, assetId]);
  return { url, failed };
}

interface DocumentAssetImageProps {
  documentId: string;
  assetId: string;
  alt?: string;
  title?: string;
  className?: string;
}

export function DocumentAssetImage({ documentId, assetId, alt = '', title, className }: DocumentAssetImageProps) {
  const { url, failed } = useDocumentAssetUrl(documentId, assetId);
  if (failed) {
    return <span className="prd-asset-unavailable" data-asset-id={assetId}>图片未能加载{alt ? `：${alt}` : ''}</span>;
  }
  if (!url) {
    return <span className="prd-asset-unavailable" aria-busy="true" data-asset-id={assetId}>图片加载中…</span>;
  }
  return <img src={url} alt={alt} title={title} className={className} data-asset-id={assetId} />;
}
