import { describe, expect, it } from 'vitest';
import type { SessionStateDto } from './dto';
import {
  actionPlacement,
  isSkipClarificationIntent,
  nextPrdConfirmationStep,
  normalizeSessionId,
  reviewTaskRecoveryAction,
  workItemLane,
  workItemPresentation,
  workItemStatusLabel,
} from './workflowUi';

const state = (overrides: Partial<SessionStateDto> = {}): SessionStateDto => ({
  session_id: 'session-1',
  project_id: 'project-1',
  phase: 'REVIEW',
  state_version: 4,
  current_spec_version_id: 'spec-1',
  current_spec_status: 'HUMAN_REVIEW',
  legal_actions: ['approve', 'reject', 'rework', 'restore_spec_version'],
  next_action: 'HUMAN_REVIEW',
  outstanding_questions: [],
  review_findings: [],
  ...overrides,
});

describe('API workspace workflow presentation', () => {
  it('keeps PRD decisions out of the generic sidebar action list', () => {
    const placement = actionPlacement(state().legal_actions);

    expect(placement.sidebar).toEqual(['restore_spec_version']);
    expect(placement.prd).toEqual(['approve', 'reject', 'rework']);
  });

  it('offers skip clarification as a normal pre-PRD workspace action', () => {
    const placement = actionPlacement(['message', 'skip_clarification']);

    expect(placement.sidebar).toEqual(['message', 'skip_clarification']);
    expect(placement.prd).toEqual([]);
  });

  it('recognizes only explicit natural-language requests to skip clarification', () => {
    expect(isSkipClarificationIntent('跳过澄清')).toBe(true);
    expect(isSkipClarificationIntent('我希望跳过澄清阶段')).toBe(true);
    expect(isSkipClarificationIntent('请直接生成 PRD！')).toBe(true);
    expect(isSkipClarificationIntent('不用澄清')).toBe(true);
    expect(isSkipClarificationIntent('我补充一下边界条件')).toBe(false);
    expect(isSkipClarificationIntent('')).toBe(false);
  });

  it('confirms an opinion-free PRD before starting decomposition', () => {
    expect(nextPrdConfirmationStep(state(), 0, false)).toBe('approve');
    expect(nextPrdConfirmationStep(
      state({ current_spec_status: 'APPROVED', legal_actions: ['convert_to_work_item'] }),
      0,
      false,
    )).toBe('convert_to_work_item');
    expect(nextPrdConfirmationStep(state(), 1, false)).toBe('publish_review');
    expect(nextPrdConfirmationStep(state(), 0, false, true)).toBe('submit_comments');
    expect(nextPrdConfirmationStep(state(), 0, true)).toBe('wait');
  });

  it('makes the root card the PRD entry and child cards the Agent Spec entry', () => {
    expect(workItemPresentation('ROOT', true)).toEqual({
      cardLabel: '打开 PRD 审核',
      detailKind: 'prd',
    });
    expect(workItemPresentation('TASK', true)).toEqual({
      cardLabel: '查看任务 Spec',
      detailKind: 'agent-spec',
    });
    expect(workItemPresentation('MILESTONE', false)).toEqual({
      cardLabel: '查看规划详情',
      detailKind: 'work-item',
    });
  });

  it('places backend work-item states into the three task lanes', () => {
    expect(workItemLane('todo')).toBe('todo');
    expect(workItemLane('queued')).toBe('todo');
    expect(workItemLane('in_progress')).toBe('in_progress');
    expect(workItemLane('blocked')).toBe('in_progress');
    expect(workItemLane('completed')).toBe('done');
    expect(workItemLane(null)).toBe('todo');
    expect(workItemStatusLabel('blocked')).toBe('受阻');
  });

  it('accepts an existing backend Session ID without creating a new Agent run', () => {
    expect(normalizeSessionId(' 976bb117-1434-43dd-a6bf-3a860b3935c8 '))
      .toBe('976bb117-1434-43dd-a6bf-3a860b3935c8');
    expect(() => normalizeSessionId('   ')).toThrow('Session ID');
  });

  it('refreshes the whole workspace when a recovered publish task already finished', () => {
    expect(reviewTaskRecoveryAction('pending')).toBe('poll');
    expect(reviewTaskRecoveryAction('processing')).toBe('poll');
    expect(reviewTaskRecoveryAction('done')).toBe('refresh');
    expect(reviewTaskRecoveryAction('error')).toBe('error');
  });
});
