import type { CommandAction, ReviewTaskDto, SessionStateDto, WorkItemDto } from './dto';

const PRD_ACTIONS = new Set<CommandAction>([
  'approve',
  'reject',
  'rework',
  'revise',
  'publish_review',
  'convert_to_work_item',
]);

export function normalizeSessionId(value: string): string {
  const sessionId = value.trim();
  if (!sessionId) throw new Error('Session ID 不能为空。');
  return sessionId;
}

export function isSkipClarificationIntent(value: string): boolean {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[\s，。！？、,.!?]/g, '');
  if (!normalized) return false;

  return [
    '跳过',
    '跳过澄清',
    '跳过澄清阶段',
    '希望跳过澄清',
    '希望跳过澄清阶段',
    '我希望跳过澄清',
    '我希望跳过澄清阶段',
    '不需要澄清',
    '不用澄清',
    '无需澄清',
    '直接生成prd',
    '请直接生成prd',
    '直接进入生成prd',
    '不想澄清直接生成prd',
    '模糊地带由agent自行掌握',
  ].includes(normalized);
}

export function reviewTaskRecoveryAction(
  status: ReviewTaskDto['status'],
): 'poll' | 'refresh' | 'error' {
  if (status === 'done') return 'refresh';
  if (status === 'error') return 'error';
  return 'poll';
}

export function actionPlacement(actions: CommandAction[]): {
  sidebar: CommandAction[];
  prd: CommandAction[];
} {
  return {
    sidebar: actions.filter((action) => !PRD_ACTIONS.has(action)),
    prd: actions.filter((action) => PRD_ACTIONS.has(action)),
  };
}

export type PrdConfirmationStep =
  | 'approve'
  | 'convert_to_work_item'
  | 'publish_review'
  | 'submit_comments'
  | 'wait'
  | 'none';

export function nextPrdConfirmationStep(
  state: SessionStateDto,
  unresolvedCommentCount: number,
  reviewTaskActive: boolean,
  hasPendingDrafts = false,
): PrdConfirmationStep {
  if (reviewTaskActive) return 'wait';
  if (hasPendingDrafts) return 'submit_comments';
  if (unresolvedCommentCount > 0) return 'publish_review';
  if (state.legal_actions.includes('convert_to_work_item')) return 'convert_to_work_item';
  if (state.legal_actions.includes('approve')) return 'approve';
  return 'none';
}

export function workItemPresentation(
  kind: WorkItemDto['kind'],
  hasAgentSpec: boolean,
): { cardLabel: string; detailKind: 'prd' | 'agent-spec' | 'work-item' } {
  if (kind === 'ROOT') return { cardLabel: '打开 PRD 审核', detailKind: 'prd' };
  if (kind === 'TASK' && hasAgentSpec) {
    return { cardLabel: '查看任务 Spec', detailKind: 'agent-spec' };
  }
  return { cardLabel: '查看规划详情', detailKind: 'work-item' };
}

export type WorkItemLane = 'todo' | 'in_progress' | 'done' | 'failed';

export function workItemLane(status: string | null | undefined): WorkItemLane {
  const normalized = (status ?? 'todo').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (['done', 'completed', 'complete', 'success', 'succeeded', 'closed'].includes(normalized)) {
    return 'done';
  }
  // 'failed' must be matched before the fallthrough, otherwise a failed task
  // is silently rendered as if it had never started.
  if (['failed', 'failure', 'error', 'rejected'].includes(normalized)) {
    return 'failed';
  }
  if (['in_progress', 'active', 'running', 'processing', 'in_review', 'review', 'blocked'].includes(normalized)) {
    return 'in_progress';
  }
  return 'todo';
}

export function workItemStatusLabel(status: string | null | undefined): string {
  const normalized = (status ?? 'todo').trim().toLowerCase().replace(/[\s-]+/g, '_');
  const labels: Record<string, string> = {
    backlog: '待开始',
    todo: '待开始',
    pending: '待开始',
    ready: '待开始',
    queued: '待开始',
    in_progress: '进行中',
    active: '进行中',
    running: '执行中',
    processing: '处理中',
    in_review: '评审中',
    review: '评审中',
    blocked: '受阻',
    failed: '已失败',
    failure: '已失败',
    error: '已失败',
    rejected: '已失败',
    done: '已完成',
    completed: '已完成',
    complete: '已完成',
    success: '已完成',
    succeeded: '已完成',
    closed: '已完成',
  };
  return labels[normalized] ?? status?.trim() ?? '待开始';
}
