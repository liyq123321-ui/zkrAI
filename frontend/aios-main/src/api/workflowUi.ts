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
