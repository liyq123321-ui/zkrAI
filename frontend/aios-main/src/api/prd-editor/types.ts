export type BlockStatus = 'pending' | 'generating' | 'ready' | 'editing' | 'outdated' | 'error';
export interface Block {
  id: string; document_id: string; title: string; parent_id: string | null; order: number;
  status: BlockStatus; version: number; content: string; summary: string; instruction: string;
  dependencies: string[]; fragment_version: number | null;
  fragment?: { er_diagrams?: Array<{ diagram_id: string; title: string;
    after_section: 'functional_requirements' | 'system_boundaries' | 'core_objects' | 'main_flows'; drawio_xml: string }> } | null;
}
export interface Document {
  id: string; session_id: string; title: string; background: string; global_rules: string;
  template: string; plan_status: string; revision: number; context_revision: number;
  latest_version?: number | null;
  submitted_revision?: number | null; submitted_spec_id?: string | null;
}
export interface DocumentAsset {
  id: string; document_id: string; block_id: string | null; type: 'image'; file_name: string;
  mime_type: string; size_bytes: number; width: number | null; height: number | null;
  metadata: Record<string, unknown>; created_at: string | null;
}
export interface DocumentReviewIssue {
  severity: 'blocker' | 'warning' | 'info';
  block_ids: string[];
  description: string;
  suggestion: string;
  alternative_suggestion?: string | null;
  evidence?: { block_id: string; quote: string }[];
}
export interface AgentRun {
  id: string; block_id: string | null; type: 'plan' | 'generate' | 'revise' | 'review' | 'initial' | 'document_review' | 'document_revision';
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; base_version: number;
  apply_status: 'pending' | 'applied' | 'conflict' | 'cancelled' | 'resolved'; error: string | null;
  request_id?: string;
  stale?: boolean;
  review_scope?: 'full' | 'affected';
  result: { content?: string; summary?: string; review_notes?: string[]; completed?: number; total?: number;
    stream_content?: string; stream_blocks?: Record<string, string>; stream_sentence_count?: number; stream_complete?: boolean;
    progress_stage?: 'queued' | 'reviewing' | 'revising' | 'verifying' | 'generating' | 'streaming';
    activity_message?: string; activity_steps?: string[];
    progress_unit?: 'scope' | 'block' | 'sentence'; current_block_id?: string | null; active_block_ids?: string[];
    completed_block_ids?: string[];
    resolution_round?: number;
    remaining_issues?: number;
    review_pending?: boolean;
    elapsed_seconds?: number;
    timing_events?: { message: string; elapsed_seconds: number }[];
    issues?: DocumentReviewIssue[] } | null;
}
export interface Comment {
  id: string; text: string; resolved: boolean;
  targets: { block_id: string; base_version: number; start_line?: number; end_line?: number; quote: string;
    selected_text?: string; action?: 'comment' | 'delete' }[];
}
export interface Snapshot { document: Document; blocks: Block[]; runs: AgentRun[]; comments: Comment[]; assets?: DocumentAsset[] }
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
