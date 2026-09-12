export type BlockStatus = 'pending' | 'generating' | 'ready' | 'editing' | 'outdated' | 'error';
export interface Block {
  id: string; document_id: string; title: string; parent_id: string | null; order: number;
  status: BlockStatus; version: number; content: string; summary: string; instruction: string;
  dependencies: string[]; fragment_version: number | null;
}
export interface Document {
  id: string; session_id: string; title: string; background: string; global_rules: string;
  template: string; plan_status: string; revision: number; context_revision: number;
}
export interface AgentRun {
  id: string; block_id: string | null; type: 'plan' | 'generate' | 'revise' | 'review';
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; base_version: number;
  apply_status: 'pending' | 'applied' | 'conflict' | 'cancelled'; error: string | null;
  result: { content?: string; summary?: string; review_notes?: string[] } | null;
}
export interface Comment {
  id: string; text: string; resolved: boolean;
  targets: { block_id: string; base_version: number; start_line?: number; end_line?: number; quote: string }[];
}
export interface Snapshot { document: Document; blocks: Block[]; runs: AgentRun[]; comments: Comment[] }
export interface Draft {
  content: string; title: string; instruction: string; parent_id: string | null; dependencies: string[];
  version: number; dirty: boolean; error?: string;
}
export function fromBlock(b: Block): Draft {
  return { content: b.content, title: b.title, instruction: b.instruction, parent_id: b.parent_id,
    dependencies: b.dependencies, version: b.version, dirty: false };
}
export function mergeDrafts(current: Record<string, Draft>, blocks: Block[]) {
  const next = { ...current };
  for (const b of blocks) {
    const old = next[b.id];
    if (!old || (!old.dirty && old.version <= b.version)) next[b.id] = fromBlock(b);
  }
  return next;
}
export const statusNames: Record<BlockStatus, string> = {
  pending: '待生成', generating: '生成中', ready: '已就绪', editing: '编辑中', outdated: '待更新', error: '失败',
};
