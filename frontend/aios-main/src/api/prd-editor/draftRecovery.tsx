import { useEffect, useState } from 'react';
import { currentActorId } from '../../auth/session';
import { appConfig } from '../config';

export function useDraftRecovery<T>(scope: string | null, value: T | null) {
  const actor = currentActorId();
  const key = scope ? `ff-prd-recovery:${encodeURIComponent(appConfig.apiBaseUrl)}:${encodeURIComponent(actor)}:${scope}` : '';
  const [loadedKey, setLoadedKey] = useState('');
  const [pending, setPending] = useState<{ value: T; at: string } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setPending(null); setError('');
    if (key) try {
      const raw = localStorage.getItem(key);
      if (raw) { const stored = JSON.parse(raw); if (stored.actor === actor && stored.schema === 1) setPending(stored); }
    } catch { setError('无法读取本机恢复副本，请保留当前输入。'); }
    setLoadedKey(key);
  }, [key, actor]);
  useEffect(() => {
    if (!key || loadedKey !== key || pending) return;
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify({ schema: 1, actor, value, at: new Date().toISOString() }));
      setError('');
    } catch { setError('本机恢复副本保存失败，请及时显式保存或复制输入。'); }
  }, [key, actor, loadedKey, pending, value]);
  function clear() {
    try { localStorage.removeItem(key); setPending(null); setError(''); }
    catch { setError('恢复副本清理失败，请检查浏览器存储权限。'); }
  }
  return { pending: loadedKey === key ? pending : null, error, clear };
}

export function RecoveryNotice<T>({ recovery, onRestore }: { recovery: ReturnType<typeof useDraftRecovery<T>>; onRestore: (value: T) => void | boolean }) {
  return <>{recovery.error && <p role="alert">{recovery.error}</p>}{recovery.pending && <section className="prd-conflict" aria-label="发现未保存草稿">
    <strong>发现未保存的本机草稿</strong><p>{new Date(recovery.pending.at).toLocaleString('zh-CN')} · 恢复只放回输入区，不会保存或发布正文。</p>
    <button onClick={() => { if (onRestore(recovery.pending!.value) !== false) recovery.clear(); }}>恢复到输入区</button>
    <button onClick={() => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(recovery.pending, null, 2)], { type: 'application/json;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.download = 'PRD未保存草稿.json'; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }}>下载恢复副本</button><button onClick={recovery.clear}>丢弃恢复副本</button>
  </section>}</>;
}
