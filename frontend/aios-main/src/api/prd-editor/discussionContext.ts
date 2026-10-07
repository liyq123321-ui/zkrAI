import { useEffect, useRef, useState } from 'react';
import { currentActorId } from '../../auth/session';
import { appConfig } from '../config';
import type { Block, Snapshot } from './types';

export type ObjectTarget = {
  kind: 'cell' | 'row' | 'column' | 'mermaid'; block_id: string; block_version: number;
  table_index?: number; headers?: string[]; key_column?: string; key_value?: string; column?: string;
  diagram_index?: number; source?: string; object_id?: string; label: string;
};
export type Discussion = { blockId: string; version: number; quote: string; pinned: boolean; constraints: string; object?: ObjectTarget };

export function discussionIssue(value: Discussion, blocks: Block[]) {
  const block = blocks.find(b => b.id === value.blockId);
  if (!block) return '原章节已删除，请重新选择目标。';
  if (block.version !== value.version) return '来源章节已有新版本，请核对并重新选择目标。';
  if (value.quote && block.content.split(value.quote).length !== 2) return '原文不能唯一定位，请重新选择。';
  return '';
}

export function useDiscussion(snapshot: Snapshot | null) {
  const key = snapshot ? `ff-prd-context:${encodeURIComponent(appConfig.apiBaseUrl)}:${encodeURIComponent(currentActorId())}:${snapshot.document.id}` : '';
  const keyRef = useRef(key); keyRef.current = key;
  const [stored, setStored] = useState<{ key: string; value: Discussion | null }>({ key: '', value: null });
  const [error, setError] = useState('');
  useEffect(() => {
    let value: Discussion | null = null;
    try { const raw = key && localStorage.getItem(key); if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed.blockId === 'string' && Number.isInteger(parsed.version) && typeof parsed.quote === 'string' && typeof parsed.constraints === 'string') value = parsed;
    } setError(''); } catch { setError('讨论对象无法从本机恢复，请重新选择。'); }
    setStored({ key, value });
  }, [key]);
  const value = stored.key === key ? stored.value : null;
  function update(next: Discussion | null) {
    const currentKey = keyRef.current; if (!currentKey) return;
    setStored({ key: currentKey, value: next });
    try { if (next) localStorage.setItem(currentKey, JSON.stringify(next)); else localStorage.removeItem(currentKey); setError(''); }
    catch { setError('讨论对象本机保存失败，刷新后可能需要重选。'); }
  }
  return { value, update, error, issue: value && snapshot ? discussionIssue(value, snapshot.blocks) : '' };
}
