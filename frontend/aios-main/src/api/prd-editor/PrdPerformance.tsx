import { useEffect, useState } from 'react';
import { apiClient } from '../client';
import { documentPath } from './api';
type Timing = { operation: string; skills: string[]; scope_ids: string[]; prepare_seconds: number; seconds: number; cache_hit: boolean; success: boolean; model_calls: number };
type Report = { note: string; candidate_jobs: { id: string; title: string; status: string; error?: string; timings: { queue_seconds?: number; generation_seconds?: number; save_seconds?: number; total_seconds?: number; operations?: Timing[] } }[];
  agent_runs: { id: string; type: string; status: string; error?: string; timings: Timing[] }[] };
export function PrdPerformance({ documentId }: { documentId: string }) {
  const [data, setData] = useState<Report | null>(null), [error, setError] = useState('');
  const load = () => apiClient.request<Report>(documentPath(documentId) + '/workbench/performance').then(v => { setData(v); setError(''); }).catch(e => setError(String(e)));
  useEffect(() => { void load(); }, [documentId]);
  const operations = [...(data?.candidate_jobs.flatMap(j => j.timings.operations ?? []) ?? []), ...(data?.agent_runs.flatMap(r => r.timings) ?? [])];
  const table = (rows: Timing[]) => <table><thead><tr><th>步骤 / Skill 组合</th><th>范围</th><th>准备 / 总耗时</th><th>模型调用</th><th>结果</th></tr></thead><tbody>{rows.map((r, i) => <tr key={i}><td>{r.operation}<small>{r.skills.join('、')}</small></td><td>{r.scope_ids.length} 个章节</td><td>{r.prepare_seconds}s / {r.seconds}s</td><td>{r.model_calls}{r.cache_hit ? '（复用）' : ''}</td><td>{r.success ? '完成' : '未完成'}</td></tr>)}</tbody></table>;
  return <section aria-label="执行耗时与复用记录"><button onClick={() => void load()}>刷新实际耗时</button>{error && <p role="alert">{error}</p>}<p>{data?.note}</p><p>最近记录：{operations.reduce((sum, r) => sum + r.model_calls, 0)} 次模型调用，{operations.filter(r => r.cache_hit).length} 次有效结果复用。</p>
    <p>只有正文、关联内容、规则、提示词、显式模型与 Skill 校验信息一致才复用只读审核结果；缓存限本进程 15 分钟。进入下一阶段的审核强制重新调用。一次调用包含多个 Skill 时显示组合耗时，不伪造每个 Skill 的独立耗时。</p>
    {data?.candidate_jobs.map(j => <article className="prd-workbench-card" key={j.id}><h4>{j.title} · {j.status}</h4><p>排队 {j.timings.queue_seconds ?? '—'}s · 生成与校验 {j.timings.generation_seconds ?? '—'}s · 保存准备 {j.timings.save_seconds ?? '—'}s · 总计 {j.timings.total_seconds ?? '—'}s</p>{j.error && <p>{j.error}</p>}{table(j.timings.operations ?? [])}</article>)}
    {data?.agent_runs.map(r => <article className="prd-workbench-card" key={r.id}><h4>{r.type} · {r.status}</h4>{r.error && <p>{r.error}</p>}{r.timings.length ? table(r.timings) : <p>此历史任务未记录分阶段耗时。</p>}</article>)}
  </section>;
}
