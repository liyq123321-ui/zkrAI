import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { getDocumentVersion } from './api';
import type { DocumentVersion, DocumentVersionDetail } from './api';
import { BlockDiff } from './BlockHistory';
import { alignVersionBlocks, compareVersionBlocks, versionBlockDepth } from './versionDiffModel';
import type { VersionChange } from './versionDiffModel';
import type { Block } from './types';

const changeNames: Record<VersionChange, string> = {
  same: '', added: '新增', removed: '删除', structure: '结构调整', content: '内容改动',
};

function VersionDirectoryCell({ block, blocks, change, onView }: {
  block?: Block; blocks: Block[]; change: VersionChange; onView: (id: string) => void;
}) {
  if (!block) return <div className="prd-version-directory-cell is-empty" aria-hidden="true" />;
  return <div className={`prd-version-directory-cell is-${change}`} style={{ paddingLeft: `${12 + versionBlockDepth(block, blocks) * 19}px` }}>
    <span className="prd-version-directory-title">{block.title || '未命名章节'}</span>
    {change !== 'same' && <><small>{changeNames[change]}</small><button onClick={() => onView(block.id)} aria-label={`查看 ${block.title || '未命名章节'} 的正文差异`}>查看</button></>}
  </div>;
}

export function VersionCompare({ documentId, versions, formatTime, onClose }: {
  documentId: string; versions: DocumentVersion[]; formatTime: (value: string) => string; onClose: () => void;
}) {
  const orderedVersions = useMemo(() => [...versions].sort((a, b) => a.version - b.version), [versions]);
  const [selected, setSelected] = useState<number[]>(() => [...versions].sort((a, b) => b.version - a.version).slice(0, 2).map(item => item.version));
  const [details, setDetails] = useState<Record<number, DocumentVersionDetail>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [view, setView] = useState<'directory' | 'detail'>('directory');
  const [focusedBlockId, setFocusedBlockId] = useState<string | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const focusedDetailRef = useRef<HTMLDetailsElement>(null);
  const cacheRef = useRef<Record<number, DocumentVersionDetail>>({});
  const pair = useMemo(() => selected.length === 2 ? [...selected].sort((a, b) => a - b) : [], [selected]);

  useLayoutEffect(() => {
    if (stripRef.current) stripRef.current.scrollLeft = stripRef.current.scrollWidth;
  }, [orderedVersions]);

  useEffect(() => {
    if (view === 'detail' && focusedBlockId) focusedDetailRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [view, focusedBlockId]);

  useEffect(() => {
    if (pair.length !== 2) { setDetails({}); setError(''); return; }
    let active = true;
    const missing = pair.filter(version => !cacheRef.current[version]);
    if (!missing.length) { setDetails({ ...cacheRef.current }); setLoading(false); setError(''); return; }
    setLoading(true);
    setError('');
    void Promise.all(missing.map(version => getDocumentVersion(documentId, version))).then(rows => {
      if (!active) return;
      rows.forEach(row => { cacheRef.current[row.version] = row; });
      setDetails({ ...cacheRef.current });
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [documentId, pair]);

  const before = details[pair[0]];
  const after = details[pair[1]];
  const compared = useMemo(() => before && after
    ? compareVersionBlocks(before.snapshot.blocks, after.snapshot.blocks) : [], [before, after]);
  const byId = useMemo(() => new Map(compared.map(item => [item.id, item])), [compared]);
  const aligned = useMemo(() => before && after
    ? alignVersionBlocks(before.snapshot.blocks, after.snapshot.blocks) : [], [before, after]);
  const changed = compared.filter(item => item.change !== 'same');

  function toggle(version: number) {
    setView('directory');
    setFocusedBlockId(null);
    setSelected(current => current.includes(version)
      ? current.filter(value => value !== version)
      : current.length < 2 ? [...current, version] : current);
  }

  function openDetail(id: string) {
    setFocusedBlockId(id);
    setView('detail');
  }

  return <div className="prd-modal-backdrop prd-version-compare-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="prd-version-compare" role="dialog" aria-modal="true" aria-label="比较全文版本">
      <header><div><h2>比较全文版本</h2><p>选择两个版本，先看目录变化，再查看正文详情。</p></div><button aria-label="关闭版本比较" onClick={onClose}><X size={18} /></button></header>
      <div className="prd-version-picker">
        <button aria-label="查看更早版本" onClick={() => stripRef.current?.scrollBy({ left: -480, behavior: 'smooth' })}><ChevronLeft size={21} /></button>
        <div className="prd-version-strip" ref={stripRef}>{orderedVersions.map(version => <label key={version.id} className={selected.includes(version.version) ? 'is-selected' : ''}>
          <input type="checkbox" checked={selected.includes(version.version)} disabled={selected.length === 2 && !selected.includes(version.version)} onChange={() => toggle(version.version)} />
          <strong>全文版本 v{version.version}</strong><time>{formatTime(version.created_at)}</time>
        </label>)}</div>
        <button aria-label="查看更新版本" onClick={() => stripRef.current?.scrollBy({ left: 480, behavior: 'smooth' })}><ChevronRight size={21} /></button>
      </div>
      <div className="prd-version-compare-meta">
        <span>{selected.length === 2 ? `较早 v${pair[0]} → 较新 v${pair[1]}` : `已选 ${selected.length}/2 个版本，请选满两个`}</span>
        {before && after && !loading && <span>新增 {compared.filter(item => item.change === 'added').length} · 删除 {compared.filter(item => item.change === 'removed').length} · 结构调整 {compared.filter(item => item.change === 'structure').length} · 内容改动 {compared.filter(item => item.change === 'content').length}</span>}
      </div>
      <div className="prd-version-compare-body">
        {error ? <p role="alert">版本加载失败：{error}</p> : loading ? <p>正在读取两个版本…</p> : !before || !after ? <p>请选择两个版本查看目录差异。</p>
          : view === 'directory' ? <div className="prd-version-directories" aria-label="两版目录对比">
            <div className="prd-version-directory-headings"><h3>v{before.version} · {formatTime(before.created_at)}</h3><h3>v{after.version} · {formatTime(after.created_at)}</h3></div>
            <div className="prd-version-directory-rows">{aligned.map((row, index) => <div className="prd-version-directory-row" key={`${row.before?.id ?? 'blank'}-${row.after?.id ?? 'blank'}-${index}`}>
              <VersionDirectoryCell block={row.before} blocks={before.snapshot.blocks} change={row.before ? byId.get(row.before.id)?.change ?? 'same' : 'same'} onView={openDetail} />
              <VersionDirectoryCell block={row.after} blocks={after.snapshot.blocks} change={row.after ? byId.get(row.after.id)?.change ?? 'same' : 'same'} onView={openDetail} />
            </div>)}</div>
          </div> : <div className="prd-version-text-diff">{changed.length ? changed.map((item, index) => <details key={item.id} ref={item.id === focusedBlockId ? focusedDetailRef : undefined} open={focusedBlockId ? item.id === focusedBlockId : index === 0}>
            <summary><strong>{item.after?.title || item.before?.title || '未命名章节'}</strong><span className={`is-${item.change}`}>{changeNames[item.change]}</span></summary>
            {item.change === 'structure' && item.before?.title !== item.after?.title && <p>目录名称：{item.before?.title} → {item.after?.title}</p>}
            <BlockDiff before={item.before?.content ?? ''} after={item.after?.content ?? ''} />
          </details>) : <p>两个版本的目录与正文完全相同。</p>}</div>}
      </div>
      <footer><span>颜色：<i className="is-added">新增</i><i className="is-removed">删除</i><i className="is-structure">结构调整</i><i className="is-content">内容改动</i></span>
        <button className="prd-primary" disabled={!before || !after || loading} onClick={() => { setFocusedBlockId(null); setView(current => current === 'directory' ? 'detail' : 'directory'); }}>{view === 'directory' ? '查看详情' : '返回目录对比'}</button>
      </footer>
    </section>
  </div>;
}
