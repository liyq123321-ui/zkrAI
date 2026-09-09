import { useMemo, useState } from 'react';
import { ArrowUpRight, CheckCircle2, ClipboardCheck, Network, PackageCheck, ShieldCheck, TestTube2, UserRound } from 'lucide-react';
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
      {outputs.length ? <div className="ff-review-table-scroll"><table><thead><tr><th>核验</th><th>交付物</th><th>格式</th><th>要求</th></tr></thead><tbody>
        {outputs.map((output, index) => <tr key={index}><td><ReviewCheck id={`delivery:${index}`} checked={checked.has(`delivery:${index}`)} onToggle={toggle} label={`核验交付物 ${valueText(output.name)}`} /></td><th>{valueText(output.name, `交付物 ${index + 1}`)}</th><td>{valueText(output.format)}</td><td>{output.required === true ? '必须交付' : output.required === false ? '可选' : '未标记'}</td></tr>)}
      </tbody></table></div> : <p className="ff-review-empty">Agent Spec 尚未列出交付物。</p>}
    </section>

    <section className="ff-review-table-section">
      <header><TestTube2 aria-hidden="true" /><div><h3>验收与 Test Case</h3><p>将验收标准转换为可逐项复核的测试表</p></div></header>
      {criteria.length || obligations.length ? <div className="ff-review-table-scroll"><table className="ff-review-test-table"><thead><tr><th>核验</th><th>Case</th><th>关联需求</th><th>验证内容</th><th>验证方法</th><th>预期结果</th></tr></thead><tbody>
        {criteria.map((criterion, index) => <tr key={`case:${index}`}><td><ReviewCheck id={`case:${index}`} checked={checked.has(`case:${index}`)} onToggle={toggle} label={`核验测试用例 TC-${String(index + 1).padStart(2, '0')}`} /></td><th>TC-{String(index + 1).padStart(2, '0')}</th><td>{requirementText(criterion.requirement_ids)}</td><td>{valueText(criterion.criterion)}</td><td>{valueText(criterion.verification_method)}</td><td>{valueText(criterion.expected_result)}</td></tr>)}
        {obligations.map((obligation, index) => <tr key={`obligation:${index}`}><td><ReviewCheck id={`obligation:${index}`} checked={checked.has(`obligation:${index}`)} onToggle={toggle} label={`核验测试义务 ${index + 1}`} /></td><th>TO-{String(index + 1).padStart(2, '0')}</th><td>测试义务</td><td>{obligation}</td><td>按 Spec 执行</td><td>留存可复核证据</td></tr>)}
      </tbody></table></div> : <p className="ff-review-empty">Agent Spec 尚未列出验收标准或测试义务。</p>}
    </section>

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
