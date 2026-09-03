import type { AuditEventDto, SessionStateDto, SpecVersionDto } from './dto';

const labels: Record<string, string> = {
  INTAKE:'需求收集', NEED_CLARIFICATION:'需求澄清', CLARIFICATION:'需求澄清', SPECIFICATION:'生成需求文档',
  REVIEW:'PRD 审核', HUMAN_REVIEW:'等待人工审核', REWORK:'需要修改', APPROVED:'已确认',
  REJECTED:'已驳回', CONVERTED:'已完成拆解', AGENT_SPECS_READY:'任务规格已就绪',
  DECOMPOSITION:'任务拆解', DRAFT:'草稿', GENERATING:'生成中',
  ANSWER_CLARIFICATION:'补充需求信息', CREATE_SPEC:'生成 PRD', REVISE_SPEC:'修改 PRD',
  CONVERT_TO_WORK_ITEM:'开始任务拆解', NONE:'当前无待执行操作',
  analyze_brief:'分析需求完整性', generate_spec:'生成 PRD', review_spec:'审核 PRD 质量',
  decompose_spec:'拆解任务', review_breakdown:'审核任务拆解', rewrite_prd:'根据批注修订 PRD',
  plan_task:'细化任务实现方案',
  restore_spec:'从历史版本创建新版', done:'已完成', error:'失败', running:'执行中',
  PROJECT_INTAKE_CREATED:'创建项目', PM_BRIEF_ANALYZED:'需求分析完成',
  CLARIFICATION_SKIPPED:'跳过澄清', COMMAND_APPLIED:'操作已保存',
  AGENT_SPEC_DETAILS_ENRICHED:'任务实现方案已补齐',
  message:'提交澄清', skip_clarification:'跳过澄清', create_spec:'生成 PRD', revise:'生成修订版',
  approve:'确认 PRD', reject:'驳回 PRD', rework:'要求修改', publish_review:'发布审核',
  convert_to_work_item:'拆解任务', restore_spec_version:'从历史版本创建新版',
};
export const displayLabel = (value: string) => labels[value] || value;

export function progressDescription(state: SessionStateDto): string {
  if (state.current_spec_version_id && state.legal_actions.includes('skip_clarification')) return '可补充下方澄清问题；也可跳过澄清，确认当前 PRD 并直接拆分子任务。';
  if (state.current_spec_status === 'REWORK') return 'PRD 已生成，审核发现仍有待处理问题。请打开 PRD 查看意见，在相关行添加修改要求，再提交批注生成新版。';
  if (state.outstanding_questions.length) return '请回答下方问题，帮助系统明确需求；确认信息后会生成 PRD。';
  if (state.legal_actions.includes('convert_to_work_item')) return 'PRD 已确认，可以开始拆解任务及各任务的执行规格。';
  if (state.legal_actions.includes('approve')) return '请打开 PRD 阅读并审核。需要调整时添加批注；确认后可以开始任务拆解。';
  if (state.legal_actions.includes('create_spec')) return '需求已准备好，可以生成 PRD。';
  return '这里显示这个项目当前所处的阶段，以及现在可以执行的操作。';
}

export function apiDate(value: string): Date {
  // SQLite/API timestamps without offsets represent UTC, not browser-local time.
  return new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);
}
export const displayTime = (value: string) => apiDate(value).toLocaleString('zh-CN', { hour12:false });

export function auditTitle(event: AuditEventDto): string {
  if (event.event_type === 'AGENT_TRACE') return displayLabel(String(event.payload.phase || event.payload.summary || 'Agent 执行'));
  if (event.event_type === 'COMMAND_APPLIED' && event.payload.action) return `${displayLabel(String(event.payload.action))} · 已保存`;
  return displayLabel(event.event_type);
}

export function relatedEventSpecs(event: AuditEventDto, specs: SpecVersionDto[]): SpecVersionDto[] {
  return specs.filter((spec) => spec.id === event.payload.spec_version_id
    || (Boolean(event.payload.agent_call_id) && spec.generator_call_id === event.payload.agent_call_id)
    || (Boolean(event.payload.trace_id) && spec.reviews.some((review) => review.command_id === event.payload.trace_id)));
}
