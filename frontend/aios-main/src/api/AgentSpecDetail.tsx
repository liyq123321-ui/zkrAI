import type { ReactNode } from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  FileInput,
  FolderLock,
  ListChecks,
  Network,
  PackageCheck,
  ShieldCheck,
  SlidersHorizontal,
  TestTube2,
  UserRound,
  Wrench,
} from 'lucide-react';
import type { AgentSpecDto, SpecVersionDto, WorkItemDto } from './dto';
import { AgentSpecDetails } from './AgentSpecDetails';
import type { EmployeeOption } from './employeeDirectory';
import { WorkItemAgentPanel } from './WorkItemAgentPanel';
import { workItemLane, workItemStatusLabel } from './workflowUi';

const fieldLabels: Record<string, string> = {
  name: '名称',
  format: '格式',
  required: '必须交付',
  requirement_ids: '关联需求',
  criterion: '验收条件',
  verification_method: '验证方法',
  expected_result: '预期结果',
  question: '问题',
  owner: '负责人',
  blocking: '是否阻塞',
  reason: '原因',
};

const knownFields = new Set([
  'objective', 'scope', 'exclusions', 'context_refs', 'inputs', 'outputs',
  'fixed_constraints', 'configurable_parts', 'extension_points', 'acceptance_criteria',
  'required_skills', 'allowed_tools', 'allowed_paths', 'responsible_role',
  'suggested_assignee', 'dependency_keys', 'dependency_work_item_ids',
  'test_obligations', 'risks', 'open_questions', 'work_item_id', 'source_spec_version_id',
  'requirements', 'implementation_plan',
]);

function present(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as object).length > 0;
  return true;
}

function text(value: unknown, fallback = '未提供'): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
    : [];
}

function StructuredValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <ul className="ff-spec-value-list">
        {value.map((item, index) => <li key={index}><StructuredValue value={item} /></li>)}
      </ul>
    );
  }
  if (value && typeof value === 'object') {
    return (
      <dl className="ff-spec-record">
        {Object.entries(value as Record<string, unknown>).filter(([, item]) => present(item)).map(([key, item]) => (
          <div key={key}>
            <dt>{fieldLabels[key] ?? key.replaceAll('_', ' ')}</dt>
            <dd><StructuredValue value={item} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>;
  return <span>{String(value ?? '')}</span>;
}

function ContentGroup({ label, value }: { label: string; value: unknown }) {
  if (!present(value)) return null;
  return (
    <div className="ff-spec-content-group">
      <h4>{label}</h4>
      <StructuredValue value={value} />
    </div>
  );
}

function SpecSection({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="ff-spec-section">
      <header><span>{icon}</span><h3>{title}</h3></header>
      <div className="ff-spec-section-body">{children}</div>
    </section>
  );
}

function MetaCard({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="ff-spec-meta-card">
      <header>{icon}<h3>{title}</h3></header>
      <div>{children}</div>
    </section>
  );
}

export interface WorkItemPreview {
  draft: string;
  // Undefined preserves the server suggestion; empty explicitly means unassigned.
  assigneeId?: string;
}

export function AgentSpecDetail({ item, agentSpecs, sourceSpecs = [], employees = [], preview = { draft: '' }, onPreviewChange, workItems = [], onOpenWorkItem }: {
  item: WorkItemDto;
  agentSpecs: AgentSpecDto[];
  sourceSpecs?: SpecVersionDto[];
  employees?: EmployeeOption[];
  preview?: WorkItemPreview;
  onPreviewChange?: (patch: Partial<WorkItemPreview>) => void;
  workItems?: WorkItemDto[];
  onOpenWorkItem?: (workItemId: string) => void;
}) {
  const lane = workItemLane(item.status);
  const primary = agentSpecs[0];
  const content = primary?.content ?? {};
  const suggestedAssignee = text(content.suggested_assignee, item.suggested_assignee || '');
  const assigneeId = preview.assigneeId ?? suggestedAssignee;
  const employeeOptions = assigneeId && !employees.some((employee) => employee.id === assigneeId)
    ? [{ id: assigneeId, name: assigneeId }, ...employees]
    : employees;
  const objective = text(content.objective, item.objective || item.description || '未提供任务目标');
  const skills = stringList(content.required_skills).length > 0
    ? stringList(content.required_skills)
    : item.required_skills ?? [];
  const dependencies = primary?.dependency_work_item_ids?.length
    ? primary.dependency_work_item_ids
    : item.dependency_work_item_ids;
  const otherEntries = Object.entries(content).filter(([key, value]) => !knownFields.has(key) && present(value));

  return (
    <article className="ff-spec-detail">
      <div className="ff-spec-titlebar">
        <div className="ff-spec-title-tags">
          <span className="ff-item-id">#{item.id}</span>
          <span className="ff-priority">P2</span>
          <span className={`ff-detail-status is-${lane}`}>{workItemStatusLabel(item.status)}</span>
        </div>
        <div>
          <h2>{item.title || item.id}</h2>
          <p>由后端 WorkItem 状态和 Agent Spec 实时呈现</p>
        </div>
      </div>

      <div className="ff-spec-objective">
        <span><ListChecks aria-hidden="true" /></span>
        <div><h3>任务目标</h3><p>{objective}</p></div>
      </div>

      <div className="ff-spec-detail-layout">
        <div className="ff-spec-main">
          <WorkItemAgentPanel draft={preview.draft} onDraftChange={onPreviewChange ? (draft) => onPreviewChange({ draft }) : undefined} />
          {primary ? (
            <>
              {agentSpecs.map((agentSpec) => (
                <AgentSpecDetails
                  key={agentSpec.id}
                  agentSpec={agentSpec}
                  sourceSpec={sourceSpecs.find((spec) => spec.id === agentSpec.source_spec_version_id)}
                  implementationOnly
                />
              ))}
              <SpecSection title="工作范围" icon={<ShieldCheck aria-hidden="true" />}>
                <ContentGroup label="需要完成" value={content.scope ?? item.scope} />
                <ContentGroup label="明确不包含" value={content.exclusions ?? item.exclusions} />
              </SpecSection>

              <SpecSection title="输入与交付物" icon={<PackageCheck aria-hidden="true" />}>
                <ContentGroup label="上下文与参考" value={content.context_refs} />
                <ContentGroup label="输入" value={content.inputs} />
                <ContentGroup label="输出" value={content.outputs ?? item.outputs} />
              </SpecSection>

              <SpecSection title="验收标准与测试" icon={<CheckCircle2 aria-hidden="true" />}>
                <ContentGroup label="验收标准" value={content.acceptance_criteria ?? item.acceptance_criteria} />
                <ContentGroup label="测试义务" value={content.test_obligations} />
              </SpecSection>

              <SpecSection title="实施边界与扩展点" icon={<SlidersHorizontal aria-hidden="true" />}>
                <ContentGroup label="固定约束" value={content.fixed_constraints} />
                <ContentGroup label="可配置部分" value={content.configurable_parts} />
                <ContentGroup label="扩展点" value={content.extension_points} />
              </SpecSection>

              {(present(content.risks) || present(content.open_questions)) && (
                <SpecSection title="风险与待确认事项" icon={<AlertTriangle aria-hidden="true" />}>
                  <ContentGroup label="风险" value={content.risks} />
                  <ContentGroup label="待确认事项" value={content.open_questions} />
                </SpecSection>
              )}

              {otherEntries.length > 0 && (
                <SpecSection title="其他说明" icon={<FileInput aria-hidden="true" />}>
                  {otherEntries.map(([key, value]) => <ContentGroup key={key} label={fieldLabels[key] ?? key.replaceAll('_', ' ')} value={value} />)}
                </SpecSection>
              )}

              {agentSpecs.length > 1 && <p className="ff-spec-version-note">该任务关联 {agentSpecs.length} 份 Spec，当前展示最新返回的第一份。</p>}
            </>
          ) : (
            <div className="ff-empty-detail">该卡片当前还没有 Agent Spec。任务信息仍以 WorkItem 数据展示。</div>
          )}
        </div>

        <aside className="ff-spec-sidebar">
          <MetaCard title="当前状态" icon={<CheckCircle2 aria-hidden="true" />}>
            <strong className={`ff-meta-status is-${lane}`}>{workItemStatusLabel(item.status)}</strong>
            <p>状态由后端 WorkItem 字段决定，前端不修改任务状态。</p>
          </MetaCard>
          <MetaCard title="责任 Agent" icon={<UserRound aria-hidden="true" />}>
            <label className="ff-assignee-field">
              <span>指派员工</span>
              <select value={assigneeId} disabled={!onPreviewChange} onChange={(event) => onPreviewChange?.({ assigneeId: event.target.value })}>
                <option value="">未指派</option>
                {employeeOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
              </select>
            </label>
            <p>{text(content.responsible_role, item.responsible_role || '未指定角色')}</p>
            <p className="ff-assignee-preview-note">仅本页预览，刷新后恢复。示例员工未关联真实账号。</p>
          </MetaCard>
          <MetaCard title="依赖关系" icon={<Network aria-hidden="true" />}>
            {dependencies.length > 0 ? (
              <div className="ff-dependency-list">
                {dependencies.map((id) => {
                  const dependency = workItems.find((candidate) => candidate.id === id);
                  const label = dependency?.title || `#${id}`;
                  return (
                    <button
                      key={id}
                      type="button"
                      aria-label={`打开依赖任务：${label}`}
                      disabled={!dependency || !onOpenWorkItem}
                      onClick={() => onOpenWorkItem?.(id)}
                    >
                      <span>{label}<ArrowUpRight aria-hidden="true" /></span>
                      {dependency && <small>#{id}</small>}
                      {!dependency && <small>任务暂不可用</small>}
                    </button>
                  );
                })}
              </div>
            ) : <p>无前置依赖</p>}
          </MetaCard>
          {skills.length > 0 && (
            <MetaCard title="所需技能" icon={<Wrench aria-hidden="true" />}>
              <div className="ff-spec-chips">{skills.map((skill) => <span key={skill}>{skill}</span>)}</div>
            </MetaCard>
          )}
          {present(content.allowed_tools) && (
            <MetaCard title="允许工具" icon={<TestTube2 aria-hidden="true" />}>
              <StructuredValue value={content.allowed_tools} />
            </MetaCard>
          )}
          {present(content.allowed_paths) && (
            <MetaCard title="允许路径" icon={<FolderLock aria-hidden="true" />}>
              <StructuredValue value={content.allowed_paths} />
            </MetaCard>
          )}
          {primary && (
            <MetaCard title="Spec 来源" icon={<FileInput aria-hidden="true" />}>
              <p>Agent Spec：{primary.id}</p>
              <p>PRD 版本：{primary.source_spec_version_id}</p>
            </MetaCard>
          )}
        </aside>
      </div>
    </article>
  );
}
