import type { AgentRun, Block } from './types';
export function GenerationStatus({ runs, blocks, onCancel }: { runs: AgentRun[]; blocks: Block[]; onCancel: (run: AgentRun) => void }) {
  return <div className="prd-runs">{runs.filter(r => ['queued', 'running', 'failed'].includes(r.status)).slice(-6).map(r =>
    <div key={r.id}><span className={`prd-dot is-${r.status === 'failed' ? 'error' : 'generating'}`} />
      <span>{blocks.find(b => b.id === r.block_id)?.title ?? '结构规划'}<small>{r.status === 'failed' ? r.error : r.status === 'queued' ? '排队中' : 'AI 正在处理'}</small></span>
      {r.status !== 'failed' && <button onClick={() => onCancel(r)}>取消</button>}</div>)}</div>;
}
