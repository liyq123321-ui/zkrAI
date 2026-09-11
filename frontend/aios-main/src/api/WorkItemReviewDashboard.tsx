import { useMemo, useState } from 'react';
import { ArrowUpRight, CheckCircle2, ClipboardCheck, Network, PackageCheck, ShieldCheck, TestTube2, UserRound, X } from 'lucide-react';
import type { WorkItemDto } from './dto';
import type { EmployeeOption } from './employeeDirectory';
import { workItemLane, workItemStatusLabel } from './workflowUi';

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is Record<string, unknown> => Boolean(item)) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [];
}

function valueText(value: unknown, fallback = '未提供'): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function firstValue(source: Record<string, unknown>, keys: string[]): unknown {
  return keys.map((key) => source[key]).find((value) => value !== null && value !== undefined && value !== '');
}

function detailText(value: unknown, fallback = '未提供'): string {
  if (Array.isArray(value)) {
    const items = value.filter((item) => item !== null && item !== undefined && item !== '').map((item) => (
      typeof item === 'string' ? item : JSON.stringify(item, null, 2)
    ));
    return items.length ? items.join('\n') : fallback;
  }
  if (value !== null && typeof value === 'object') return JSON.stringify(value, null, 2);
  if (typeof value === 'boolean') return value ? '是' : '否';
  const text = String(value ?? '').trim();
  return text || fallback;
}

function requirementText(value: unknown): string {
  const ids = strings(value);
  return ids.length ? ids.join('、') : '未关联';
}

function taskTemplate(item: WorkItemDto, content: Record<string, unknown>): { label: string; kind: string } {
  const haystack = `${item.title ?? ''} ${item.objective ?? ''}`.toLowerCase();
  if (/test|测试|验证|验收/.test(haystack) || strings(content.test_obligations).length) return { label: '测试验证型', kind: 'test' };
  const outputs = records(content.outputs ?? item.outputs);
  if (outputs.length && outputs.every((output) => /markdown|文档|报告|说明|doc/i.test(valueText(output.format, '') + valueText(output.name, '')))) {
    return { label: '文档交付型', kind: 'document' };
  }
  if (strings(content.allowed_paths).length || strings(content.allowed_tools).length) return { label: '工程实现型', kind: 'engineering' };
  return { label: '通用交付型', kind: 'general' };
}

function ReviewCheck({ id, checked, onToggle, label }: { id: string; checked: boolean; onToggle: (id: string) => void; label: string }) {
  return <input type="checkbox" checked={checked} onChange={() => onToggle(id)} aria-label={label} />;
}

type AgentAcceptanceKind = 'pending' | 'passed' | 'failed';
type AgentAcceptance = { kind: AgentAcceptanceKind; label: '尚未交付' | '验收通过' | '未通过' };
type ContentPreview = {
  id: string;
  title: string;
  status: AgentAcceptance;
  rows: Array<{ label: string; value: unknown }>;
  body?: unknown;
  externalUrl?: string;
};

const acceptanceLabels: Record<AgentAcceptanceKind, AgentAcceptance['label']> = {
  pending: '尚未交付',
  passed: '验收通过',
  failed: '未通过',
};

function acceptanceStatus(entry: Record<string, unknown>, itemStatus: WorkItemDto['status']): AgentAcceptance {
  const nested = record(entry.agent_acceptance);
  const source = firstValue(nested ?? entry, [
    'status', 'verdict', 'result', 'agent_acceptance_status', 'acceptance_status', 'review_status',
  ]);
  const raw = typeof source === 'string' ? source.trim().toLowerCase().replaceAll('-', '_').replaceAll(' ', '_') : '';
  let kind: AgentAcceptanceKind;
  if (/(未通过|驳回|失败|reject|fail|not_pass|blocked)/.test(raw)) kind = 'failed';
  else if (/(验收通过|已通过|通过|approve|accept|pass|success|succeed|done|completed)/.test(raw)) kind = 'passed';
  else if (/(尚未交付|未交付|待交付|待验收|pending|todo|not_delivered|in_progress|started)/.test(raw)) kind = 'pending';
  else {
    const taskStatus = String(itemStatus ?? '').trim().toLowerCase();
    if (/^(done|completed|succeeded|success)$/.test(taskStatus)) kind = 'passed';
    else if (/^(failed|blocked|rejected)$/.test(taskStatus)) kind = 'failed';
    else kind = 'pending';
  }
  return { kind, label: acceptanceLabels[kind] };
}

function AgentAcceptanceStatus({ value }: { value: AgentAcceptance }) {
  return <span className={`ff-agent-acceptance is-${value.kind}`} aria-label={`Agent 验收：${value.label}`}>{value.label}</span>;
}

function ContentLink({ preview, disabled, onOpen }: { preview: ContentPreview; disabled?: boolean; onOpen: (preview: ContentPreview) => void }) {
  if (disabled) return <span className="ff-review-content-link is-disabled" aria-disabled="true" title="交付物尚未交付，暂无可查看内容">{preview.title}</span>;
  return <a href={`#${preview.id}`} className="ff-review-content-link" onClick={(event) => {
    event.preventDefault();
    onOpen(preview);
  }}>{preview.title}</a>;
}

function ContentPreviewPanel({ preview, onClose }: { preview: ContentPreview; onClose: () => void }) {
  return <section id={preview.id} className="ff-review-content-preview" aria-label={`${preview.title} 详情`}>
    <header>
      <div><small>可复核内容</small><h3>{preview.title}</h3></div>
      <AgentAcceptanceStatus value={preview.status} />
      <button type="button" onClick={onClose} aria-label={`关闭 ${preview.title} 详情`}><X aria-hidden="true" /></button>
    </header>
    <dl>
      {preview.rows.map((row) => <div key={row.label}><dt>{row.label}</dt><dd>{detailText(row.value)}</dd></div>)}
    </dl>
    {preview.body !== undefined && <div className="ff-review-content-body"><strong>交付内容</strong><pre>{detailText(preview.body, '当前历史记录只保留了交付物定义，未记录正文或文件路径。')}</pre></div>}
    {preview.externalUrl && <a className="ff-review-external-link" href={preview.externalUrl} target="_blank" rel="noreferrer">打开原始交付物 <ArrowUpRight aria-hidden="true" />
    </a>}
  </section>;
}

export function WorkItemReviewDashboard({
  item,
  content,
  employees,
  assigneeId,
  onAssigneeChange,
  dependencies,
  workItems,
  onOpenWorkItem,
}: {
  item: WorkItemDto;
  content: Record<string, unknown>;
  employees: EmployeeOption[];
  assigneeId: string;
  onAssigneeChange?: (value: string) => void;
  dependencies: string[];
  workItems: WorkItemDto[];
  onOpenWorkItem?: (workItemId: string) => void;
}) {
  const [checked, setChecked] = useState<Set<string>>(() => new Set());
  const [contentPreview, setContentPreview] = useState<ContentPreview | null>(null);
  const outputs = records(content.outputs ?? item.outputs);
  const criteria = records(content.acceptance_criteria ?? item.acceptance_criteria);
  const obligations = strings(content.test_obligations);
  const risks = Array.isArray(content.risks) ? content.risks : [];
  const questions = records(content.open_questions);
  const blockers = questions.filter((question) => question.blocking === true).length;
  const template = taskTemplate(item, content);
  const checkCount = outputs.length + criteria.length + obligations.length;
  const employeeOptions = assigneeId && !employees.some((employee) => employee.id === assigneeId)
    ? [{ id: assigneeId, name: assigneeId }, ...employees]
    : employees;
  const toggle = (id: string) => setChecked((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const completedChecks = useMemo(() => [...checked].filter((id) => /^(delivery|case|obligation):/.test(id)).length, [checked]);
  const taskInputs = firstValue(content, ['test_inputs', 'inputs', 'context_refs']);
  const outputPreview = (output: Record<string, unknown>, index: number): ContentPreview => {
    const status = acceptanceStatus(output, item.status);
    const reference = firstValue(output, ['path', 'file_path', 'artifact_path', 'external_ref', 'url', 'href']);
    const externalUrl = typeof reference === 'string' && /^https?:\/\//i.test(reference) ? reference : undefined;
    const body = firstValue(output, ['content', 'markdown', 'text', 'body', 'evidence']) ?? (status.kind === 'pending' ? undefined : '');
    return {
      id: `review-delivery-${item.id}-${index}`,
      title: valueText(output.name, `交付物 ${index + 1}`),
      status,
      rows: [
        { label: '格式', value: output.format },
        { label: '交付要求', value: output.required === true ? '必须交付' : output.required === false ? '可选' : '未标记' },
        { label: '内容引用', value: reference },
      ],
      body,
      externalUrl,
    };
  };
  const casePreview = (criterion: Record<string, unknown>, index: number): ContentPreview => {
    const title = `TC-${String(index + 1).padStart(2, '0')}`;
    const input = firstValue(criterion, ['test_input', 'test_inputs', 'input', 'inputs', 'precondition', 'preconditions']) ?? taskInputs ?? criterion.criterion;
    return {
      id: `review-case-${item.id}-${index}`,
      title,
      status: acceptanceStatus(criterion, item.status),
      rows: [
        { label: '输入 / 前置条件', value: input },
        { label: '关联需求', value: requirementText(criterion.requirement_ids) },
        { label: '验证内容', value: criterion.criterion },
        { label: '验证方法', value: criterion.verification_method },
        { label: '期望输出', value: criterion.expected_result },
      ],
    };
  };
  const obligationPreview = (obligation: string, index: number): ContentPreview => ({
    id: `review-obligation-${item.id}-${index}`,
    title: `TO-${String(index + 1).padStart(2, '0')}`,
    status: acceptanceStatus({}, item.status),
    rows: [
      { label: '输入 / 前置条件', value: taskInputs },
      { label: '关联需求', value: '测试义务' },
      { label: '验证内容', value: obligation },
      { label: '验证方法', value: '按 Spec 执行' },
      { label: '期望输出', value: '留存可复核证据' },
    ],
  });

  return <div className={`ff-review-dashboard is-${template.kind}`}>
    <section className="ff-review-command-bar">
      <div><ClipboardCheck aria-hidden="true" /><span><small>REVIEW TEMPLATE</small><strong>{template.label}</strong></span></div>
      <p>勾选仅用于本次人工核验视图，不会改写后端任务状态。</p>
      <span>{completedChecks}/{checkCount} 已核验</span>
    </section>

    <section className="ff-review-kpis" aria-label="任务审核概览">
      <div><PackageCheck aria-hidden="true" /><span>交付物</span><strong>{outputs.length}</strong></div>
      <div><CheckCircle2 aria-hidden="true" /><span>验收项</span><strong>{criteria.length}</strong></div>
      <div><TestTube2 aria-hidden="true" /><span>测试项</span><strong>{criteria.length + obligations.length}</strong></div>
      <div className={blockers ? 'is-warning' : ''}><ShieldCheck aria-hidden="true" /><span>风险/阻塞</span><strong>{risks.length}/{blockers}</strong></div>
    </section>

    <section className="ff-review-assignment">
      <header><UserRound aria-hidden="true" /><div><h3>任务分配</h3><p>确认执行责任与当前状态</p></div></header>
      <div className="ff-review-assignment-grid">
        <label><span>指派员工</span><select value={assigneeId} disabled={!onAssigneeChange} onChange={(event) => onAssigneeChange?.(event.target.value)}>
          <option value="">未指派</option>
          {employeeOptions.map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}
        </select></label>
        <div><span>责任角色</span><strong>{valueText(content.responsible_role, item.responsible_role || '未指定')}</strong></div>
        <div><span>任务状态</span><strong className={`is-${workItemLane(item.status)}`}>{workItemStatusLabel(item.status)}</strong></div>
        <div><span>前置依赖</span><strong>{dependencies.length}</strong></div>
      </div>
    </section>

    <section className="ff-review-table-section">
      <header><PackageCheck aria-hidden="true" /><div><h3>交付物核验表</h3><p>逐项确认名称、格式和交付要求</p></div></header>
      {outputs.length ? <div className="ff-review-table-scroll"><table className="ff-review-delivery-table"><thead><tr><th>核验</th><th>交付物</th><th>格式</th><th>要求</th><th>Agent 验收</th></tr></thead><tbody>
        {outputs.map((output, index) => {
          const preview = outputPreview(output, index);
          return <tr key={index}><td><ReviewCheck id={`delivery:${index}`} checked={checked.has(`delivery:${index}`)} onToggle={toggle} label={`核验交付物 ${valueText(output.name)}`} /></td><th><ContentLink preview={preview} disabled={preview.status.kind === 'pending'} onOpen={setContentPreview} /></th><td>{valueText(output.format)}</td><td>{output.required === true ? '必须交付' : output.required === false ? '可选' : '未标记'}</td><td><AgentAcceptanceStatus value={preview.status} /></td></tr>;
        })}
      </tbody></table></div> : <p className="ff-review-empty">Agent Spec 尚未列出交付物。</p>}
    </section>

    <section className="ff-review-table-section">
      <header><TestTube2 aria-hidden="true" /><div><h3>验收与 Test Case</h3><p>将验收标准转换为可逐项复核的测试表</p></div></header>
      {criteria.length || obligations.length ? <div className="ff-review-table-scroll"><table className="ff-review-test-table"><thead><tr><th>核验</th><th>Case</th><th>关联需求</th><th>验证内容</th><th>验证方法</th><th>预期结果</th><th>Agent 验收</th></tr></thead><tbody>
        {criteria.map((criterion, index) => {
          const preview = casePreview(criterion, index);
          return <tr key={`case:${index}`}><td><ReviewCheck id={`case:${index}`} checked={checked.has(`case:${index}`)} onToggle={toggle} label={`核验测试用例 ${preview.title}`} /></td><th><ContentLink preview={preview} onOpen={setContentPreview} /></th><td>{requirementText(criterion.requirement_ids)}</td><td>{valueText(criterion.criterion)}</td><td>{valueText(criterion.verification_method)}</td><td>{valueText(criterion.expected_result)}</td><td><AgentAcceptanceStatus value={preview.status} /></td></tr>;
        })}
        {obligations.map((obligation, index) => {
          const preview = obligationPreview(obligation, index);
          return <tr key={`obligation:${index}`}><td><ReviewCheck id={`obligation:${index}`} checked={checked.has(`obligation:${index}`)} onToggle={toggle} label={`核验测试义务 ${index + 1}`} /></td><th><ContentLink preview={preview} onOpen={setContentPreview} /></th><td>测试义务</td><td>{obligation}</td><td>按 Spec 执行</td><td>留存可复核证据</td><td><AgentAcceptanceStatus value={preview.status} /></td></tr>;
        })}
      </tbody></table></div> : <p className="ff-review-empty">Agent Spec 尚未列出验收标准或测试义务。</p>}
    </section>

    {contentPreview && <ContentPreviewPanel preview={contentPreview} onClose={() => setContentPreview(null)} />}

    <div className="ff-review-bottom-grid">
      <section className="ff-review-boundary">
        <header><ShieldCheck aria-hidden="true" /><h3>范围与边界核验</h3></header>
        <dl>
          <div><dt>任务范围</dt><dd>{strings(content.scope ?? item.scope).join('；') || '未提供'}</dd></div>
          <div><dt>明确排除</dt><dd>{strings(content.exclusions ?? item.exclusions).join('；') || '未提供'}</dd></div>
          <div><dt>允许路径</dt><dd>{strings(content.allowed_paths).join('；') || '未限制'}</dd></div>
          <div><dt>允许工具</dt><dd>{strings(content.allowed_tools).join('；') || '未限制'}</dd></div>
        </dl>
      </section>
      <section className="ff-review-dependencies">
        <header><Network aria-hidden="true" /><h3>前置依赖</h3></header>
        {dependencies.length ? dependencies.map((id) => {
          const dependency = workItems.find((candidate) => candidate.id === id);
          return <button key={id} type="button" disabled={!dependency || !onOpenWorkItem} onClick={() => onOpenWorkItem?.(id)}>
            <span><strong>{dependency?.title || id}</strong><small>#{id}</small></span><ArrowUpRight aria-hidden="true" />
          </button>;
        }) : <p className="ff-review-empty">无前置依赖，可独立开始。</p>}
      </section>
    </div>
  </div>;
}
