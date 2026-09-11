import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Boxes,
  CheckCircle2,
  ChevronRight,
  Cloud,
  Code2,
  File,
  FileCode2,
  FileText,
  Folder,
  FolderKanban,
  HardDrive,
  Library,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
  UserRound,
  Wrench,
} from 'lucide-react';
import { appConfig } from './config';
import { normalizeNetworkError } from './errors';
import {
  getKnowledgeDocuments,
  getSkillFiles,
  getWorkspaceOverview,
  syncWorkspace,
  type WorkspaceFile,
  type WorkspaceOverview,
  type WorkspaceSpace,
} from './workspacePortalApi';

type PortalTab = 'spaces' | 'knowledge' | 'skills';
const CURRENT_ACTOR_ID = import.meta.env.VITE_LOCAL_ACTOR_HINT?.trim() || 'owner-1';

const tabCopy: Record<PortalTab, { label: string; subtitle: string }> = {
  spaces: { label: '工作空间', subtitle: '个人空间与项目共享空间' },
  knowledge: { label: '知识库', subtitle: '浏览远端资料与目录' },
  skills: { label: '技能库', subtitle: '查看 Agent Skills 目录' },
};

function formatBytes(value = 0) {
  if (!value) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** unit).toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}

function statusLabel(status: WorkspaceSpace['remote_status']) {
  return {
    ready: '已同步',
    not_synced: '待同步',
    workspace_only: '待建目录',
    degraded: '连接异常',
  }[status];
}

function assetIcon(kind: string) {
  if (kind === 'prototype') return <FileCode2 aria-hidden="true" />;
  if (kind === 'code') return <Code2 aria-hidden="true" />;
  if (kind === 'skill') return <Wrench aria-hidden="true" />;
  if (kind === 'knowledge') return <BookOpen aria-hidden="true" />;
  return <FileText aria-hidden="true" />;
}

function FileRows({ files, empty }: { files: WorkspaceFile[]; empty: string }) {
  if (!files.length) return <div className="wk-empty"><Folder aria-hidden="true" /><p>{empty}</p></div>;
  return <div className="wk-file-list">{files.map((item) => (
    <div className="wk-file-row" key={item.id}>
      <span><File aria-hidden="true" /></span>
      <div><strong>{item.name}</strong><small>{item.folder_path || '根目录'} · {item.file_type || 'file'}</small></div>
      <i className={`is-${item.parse_status || 'unknown'}`}>{item.parse_status || '已收录'}</i>
      <em>{item.chunk_count ? `${item.chunk_count} chunks` : '—'}</em>
    </div>
  ))}</div>;
}

export function WorkspacePortal() {
  const [tab, setTab] = useState<PortalTab>('spaces');
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [spaceKey, setSpaceKey] = useState('personal');
  const [knowledgeId, setKnowledgeId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<WorkspaceFile[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [skillId, setSkillId] = useState<string | null>(null);
  const [skillFiles, setSkillFiles] = useState<Array<{ path: string; size: number; type: string }>>([]);
  const [skillFilesLoading, setSkillFilesLoading] = useState(false);

  async function load(signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    try {
      const value = await getWorkspaceOverview(CURRENT_ACTOR_ID, signal);
      setOverview(value);
      if (!knowledgeId && value.knowledge_bases[0]) setKnowledgeId(value.knowledge_bases[0].id);
      if (!skillId && value.skills[0]) setSkillId(value.skills[0].id);
    } catch (reason) {
      if (!signal?.aborted) setError(normalizeNetworkError(reason).message);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, []);

  const selectedSpace = useMemo(() => overview?.spaces.find((space) =>
    (space.kind === 'personal' ? 'personal' : space.project_id) === spaceKey,
  ) ?? overview?.spaces[0] ?? null, [overview, spaceKey]);
  const selectedKnowledge = overview?.knowledge_bases.find((item) => item.id === knowledgeId) ?? null;
  const selectedSkill = overview?.skills.find((item) => item.id === skillId) ?? null;
  const filteredKnowledge = overview?.knowledge_bases.filter((item) =>
    `${item.name} ${item.description}`.toLowerCase().includes(query.toLowerCase())) ?? [];
  const filteredSkills = overview?.skills.filter((item) =>
    `${item.name} ${item.description}`.toLowerCase().includes(query.toLowerCase())) ?? [];

  useEffect(() => {
    if (!selectedKnowledge) return;
    const controller = new AbortController();
    setDocumentsLoading(true);
    setDocuments([]);
    void getKnowledgeDocuments(selectedKnowledge.id, selectedKnowledge.tenant_id, controller.signal)
      .then((value) => setDocuments(value.items))
      .catch((reason) => { if (!controller.signal.aborted) setError(normalizeNetworkError(reason).message); })
      .finally(() => { if (!controller.signal.aborted) setDocumentsLoading(false); });
    return () => controller.abort();
  }, [selectedKnowledge?.id]);

  useEffect(() => {
    if (!selectedSkill) return;
    const controller = new AbortController();
    setSkillFilesLoading(true);
    setSkillFiles([]);
    void getSkillFiles(selectedSkill.id, controller.signal)
      .then((value) => setSkillFiles(value.items))
      .catch((reason) => { if (!controller.signal.aborted) setError(normalizeNetworkError(reason).message); })
      .finally(() => { if (!controller.signal.aborted) setSkillFilesLoading(false); });
    return () => controller.abort();
  }, [selectedSkill?.id]);

  async function synchronize() {
    setSyncing(true);
    setError(null);
    try {
      const value = await syncWorkspace(CURRENT_ACTOR_ID);
      setOverview(value);
    } catch (reason) {
      setError(normalizeNetworkError(reason).message);
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="wk-shell">
      <header className="wk-topbar">
        <div className="wk-brand"><span><Sparkles aria-hidden="true" /></span><div><strong>firstFlight</strong><small>SHARED WORKSPACE</small></div></div>
        <nav aria-label="共享工作空间导航">
          {(Object.keys(tabCopy) as PortalTab[]).map((key) => <button type="button" key={key} className={tab === key ? 'is-active' : ''} onClick={() => { setTab(key); setQuery(''); }}>
            {key === 'spaces' ? <Boxes aria-hidden="true" /> : key === 'knowledge' ? <Library aria-hidden="true" /> : <Wrench aria-hidden="true" />}
            <span><strong>{tabCopy[key].label}</strong><small>{tabCopy[key].subtitle}</small></span>
          </button>)}
        </nav>
        <div className="wk-top-actions">
          <span className={`wk-connection is-${overview?.connection.status ?? 'offline'}`}><i />{overview?.connection.message ?? '正在连接'}</span>
          <a href="/" aria-label="返回 Dashboard"><ArrowLeft aria-hidden="true" />返回 Dashboard</a>
        </div>
      </header>

      <main className="wk-main">
        <section className="wk-heading">
          <div><small>OWNER WORKSPACE / {tabCopy[tab].label.toUpperCase()}</small><h1>{tabCopy[tab].label}</h1><p>{tabCopy[tab].subtitle}，数据来自远端 192.168.240.70 WeKnora。</p></div>
          <div className="wk-heading-actions">
            {tab !== 'spaces' && <label><Search aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索${tabCopy[tab].label}`} /></label>}
            <button type="button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? 'ff-spin' : ''} aria-hidden="true" />刷新</button>
            <button type="button" className="is-primary" onClick={() => void synchronize()} disabled={syncing || overview?.connection.status !== 'online'}><Cloud className={syncing ? 'ff-pulse' : ''} aria-hidden="true" />{syncing ? '正在同步…' : '同步项目空间'}</button>
          </div>
        </section>

        {error && <div className="wk-alert" role="alert">{error}</div>}
        {overview?.sync_result && <div className="wk-success"><CheckCircle2 aria-hidden="true" />已检查 {overview.sync_result.project_count} 个项目，新建 {overview.sync_result.created_spaces} 个空间、上传 {overview.sync_result.uploaded_files} 个文件。</div>}
        {loading && !overview ? <div className="wk-loading"><Loader2 className="ff-spin" aria-hidden="true" /><p>正在加载远端工作空间…</p></div> : null}

        {overview && tab === 'spaces' && <div className="wk-space-layout">
          <aside className="wk-space-list">
            <header><span>空间列表</span><i>{overview.spaces.length}</i></header>
            {overview.spaces.map((space) => {
              const key = space.kind === 'personal' ? 'personal' : space.project_id!;
              return <button type="button" key={key} className={spaceKey === key ? 'is-active' : ''} onClick={() => setSpaceKey(key)}>
                <span className={space.kind === 'personal' ? 'is-personal' : ''}>{space.kind === 'personal' ? <UserRound aria-hidden="true" /> : <FolderKanban aria-hidden="true" />}</span>
                <div><strong>{space.name}</strong><small>{space.kind === 'personal' ? '个人空间' : '项目共享空间'}</small></div>
                <i className={`is-${space.remote_status}`}>{statusLabel(space.remote_status)}</i><ChevronRight aria-hidden="true" />
              </button>;
            })}
          </aside>
          {selectedSpace && <section className="wk-space-detail">
            <header className="wk-detail-hero"><div className="wk-detail-icon">{selectedSpace.kind === 'personal' ? <UserRound aria-hidden="true" /> : <FolderKanban aria-hidden="true" />}</div><div><span>{selectedSpace.kind === 'personal' ? 'PERSONAL SPACE' : 'SHARED PROJECT SPACE'}</span><h2>{selectedSpace.name}</h2><p>{selectedSpace.description}</p></div><dl><div><dt>远端空间</dt><dd>{selectedSpace.tenant_id ?? '待创建'}</dd></div><div><dt>状态</dt><dd>{statusLabel(selectedSpace.remote_status)}</dd></div></dl></header>
            <div className="wk-directory-head"><div><Folder aria-hidden="true" /><span><strong>空间目录</strong><small>WeKnora 项目资产与预留代码目录</small></span></div>{selectedSpace.session_id && <code>{selectedSpace.session_id.slice(0, 8)}</code>}</div>
            <div className="wk-asset-list">
              {selectedSpace.assets.map((asset) => <div className={`wk-asset-row is-${asset.status}`} key={`${asset.folder}/${asset.name}`}>
                <span>{assetIcon(asset.kind)}</span><div><strong>{asset.folder}/{asset.name}</strong><small>{asset.status === 'ready' ? '文件已生成，可查看' : asset.status === 'reserved' ? '为后续生成代码预留' : '当前项目尚未生成此文件'}</small></div>
                {asset.source_url ? <a href={`${appConfig.apiBaseUrl}${asset.source_url}`} target="_blank" rel="noreferrer">打开<ArrowUpRight aria-hidden="true" /></a> : <i>{asset.status === 'reserved' ? 'EMPTY' : 'PENDING'}</i>}
              </div>)}
            </div>
            {selectedSpace.files.length > 0 && <div className="wk-remote-files"><h3>远端已收录文件</h3><FileRows files={selectedSpace.files} empty="尚未同步文件" /></div>}
            {selectedSpace.kind === 'personal' && <div className="wk-storage"><HardDrive aria-hidden="true" /><div><strong>{formatBytes(selectedSpace.storage_used)} / {formatBytes(selectedSpace.storage_quota)}</strong><span><i style={{ width: `${Math.min(100, (selectedSpace.storage_used || 0) / Math.max(1, selectedSpace.storage_quota || 1) * 100)}%` }} /></span><small>远端存储用量</small></div></div>}
          </section>}
        </div>}

        {overview && tab === 'knowledge' && <div className="wk-catalog-layout">
          <aside className="wk-catalog-list"><header><span>知识库目录</span><i>{filteredKnowledge.length}</i></header>{filteredKnowledge.map((item) => <button type="button" key={item.id} className={knowledgeId === item.id ? 'is-active' : ''} onClick={() => setKnowledgeId(item.id)}><span><BookOpen aria-hidden="true" /></span><div><strong>{item.name}</strong><small>{item.knowledge_count} 文档 · {item.chunk_count} 切片</small></div><ChevronRight aria-hidden="true" /></button>)}</aside>
          <CatalogDetail title={selectedKnowledge?.name} description={selectedKnowledge?.description} eyebrow="KNOWLEDGE BASE" metrics={selectedKnowledge ? [`${selectedKnowledge.knowledge_count} 文档`, `${selectedKnowledge.chunk_count} 切片`, selectedKnowledge.processing_count ? `${selectedKnowledge.processing_count} 处理中` : '索引就绪'] : []}>
            {documentsLoading ? <InlineLoading label="正在读取知识库目录…" /> : <FileRows files={documents} empty="这个知识库当前没有文档" />}
          </CatalogDetail>
        </div>}

        {overview && tab === 'skills' && <div className="wk-catalog-layout">
          <aside className="wk-catalog-list"><header><span>技能目录</span><i>{filteredSkills.length}</i></header>{filteredSkills.map((item) => <button type="button" key={item.id} className={skillId === item.id ? 'is-active' : ''} onClick={() => setSkillId(item.id)}><span className="is-skill"><Wrench aria-hidden="true" /></span><div><strong>{item.capability}</strong><small>{item.domain} · {item.installed ? '已安装' : '目录已登记'}</small></div><ChevronRight aria-hidden="true" /></button>)}</aside>
          <CatalogDetail title={selectedSkill?.capability} description={selectedSkill?.description} eyebrow={selectedSkill?.domain || 'AGENT SKILL'} metrics={selectedSkill ? [selectedSkill.installed ? '已安装' : '仅目录可用', selectedSkill.version ? `v${selectedSkill.version}` : '版本跟随目录', `${skillFiles.length || selectedSkill.file_count} 个文件`] : []}>
            {skillFilesLoading ? <InlineLoading label="正在读取技能文件…" /> : <div className="wk-skill-files">{skillFiles.length ? skillFiles.map((item) => <div key={item.path}><span><FileCode2 aria-hidden="true" /></span><strong>{item.path}</strong><small>{formatBytes(item.size)}</small></div>) : <div className="wk-empty"><Wrench aria-hidden="true" /><p>技能目录暂未返回文件</p></div>}</div>}
          </CatalogDetail>
        </div>}
      </main>
    </div>
  );
}

function InlineLoading({ label }: { label: string }) {
  return <div className="wk-inline-loading"><Loader2 className="ff-spin" aria-hidden="true" />{label}</div>;
}

function CatalogDetail({ title, description, eyebrow, metrics, children }: { title?: string; description?: string; eyebrow: string; metrics: string[]; children: React.ReactNode }) {
  return <section className="wk-catalog-detail"><header><div><small>{eyebrow}</small><h2>{title || '请选择一项'}</h2><p>{description || '从左侧目录选择后查看内容。'}</p></div><div>{metrics.map((metric) => <span key={metric}>{metric}</span>)}</div></header><div className="wk-directory-head"><div><Folder aria-hidden="true" /><span><strong>目录内容</strong><small>只读浏览远端 WeKnora 内容</small></span></div></div>{children}</section>;
}
