import { useId, type ReactNode } from 'react';
import type { AgentSpecDto, SpecVersionDto } from './dto';

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record).filter((item) => item !== undefined) : [];
}

function text(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : '';
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

function requiredLabel(value: unknown): string {
  return value === true ? '必填' : value === false ? '可选' : '未提供';
}

const bodyClass = 'whitespace-pre-wrap break-words text-sm leading-6 text-slate-300';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section aria-label={title} className="space-y-3">
    <h4 className="text-sm font-medium text-cyan-200">{title}</h4>
    {children}
  </section>;
}

function TextList({ value, empty = '未提供' }: { value: unknown; empty?: string }) {
  const items = strings(value);
  return items.length ? <ul className="list-disc space-y-1 pl-5">
    {items.map((item, index) => <li key={index} className={bodyClass}>{item}</li>)}
  </ul> : <p className="text-sm text-slate-500">{empty}</p>;
}

function Definition({ label, value }: { label: string; value: unknown }) {
  return <div><dt className="text-xs font-medium text-slate-400">{label}</dt>
    <dd className={`mt-1 ${bodyClass}`}>{text(value) || '未提供'}</dd></div>;
}

function InterfaceFields({ name, title, value }: { name: string; title: string; value: unknown }) {
  const fields = records(value);
  return <div className="min-w-0 space-y-2">
    <h6 className="text-xs font-medium text-slate-400">{title}</h6>
    {fields.length ? <div className="overflow-x-auto rounded-lg border border-slate-800">
      <table aria-label={`${name} · ${title}`} className="w-full text-left text-xs leading-5 text-slate-300">
        <thead className="bg-slate-800/60 text-slate-400"><tr>
          {['字段', '类型', '必填性', '说明'].map((label) => <th key={label} scope="col" className="px-3 py-2 font-medium">{label}</th>)}
        </tr></thead>
        <tbody>{fields.map((field, index) => <tr key={index} className="border-t border-slate-800 align-top">
          <th scope="row" className="break-words px-3 py-2 font-mono font-normal">{text(field.name) || '未提供'}</th>
          <td className="whitespace-pre-wrap break-words px-3 py-2 font-mono">{text(field.data_type) || '未提供'}</td>
          <td className="whitespace-nowrap px-3 py-2">{requiredLabel(field.required)}</td>
          <td className="min-w-40 whitespace-pre-wrap break-words px-3 py-2">{text(field.description) || '未提供'}</td>
        </tr>)}</tbody>
      </table>
    </div> : <p className="text-sm text-slate-500">未列出字段</p>}
  </div>;
}

export function AgentSpecDetails({ agentSpec, sourceSpec, implementationOnly = false }: { agentSpec: AgentSpecDto; sourceSpec?: SpecVersionDto | null; implementationOnly?: boolean }) {
  const prefix = useId();
  const content = record(agentSpec.content) ?? {};
  const plan = record(content.implementation_plan);
  const steps = records(plan?.steps);
  const criteria = records(content.acceptance_criteria);
  const requirementIds = new Set(criteria.flatMap((criterion) => strings(criterion.requirement_ids)));
  // Historical tasks must never inherit requirements from a newer/current PRD.
  const boundSource = sourceSpec?.id === agentSpec.source_spec_version_id ? sourceSpec : undefined;
  const sourceContent = record(boundSource?.content);
  const savedRequirements = records(content.requirements).filter((item) => text(item.requirement_id) && text(item.statement));
  const requirements = savedRequirements.length ? savedRequirements : [
    ...records(sourceContent?.functional_requirements), ...records(sourceContent?.non_functional_requirements),
  ].filter((item) => requirementIds.has(text(item.requirement_id)) && text(item.statement));
  const requirementTarget = (id: string) => `${prefix}-requirement-${encodeURIComponent(id)}`;
  const renderRequirementLinks = (value: unknown) => <div className="flex flex-wrap gap-2 text-xs">
    {strings(value).map((id, index) => requirements.some((item) => text(item.requirement_id) === id)
      ? <a key={index} href={`#${requirementTarget(id)}`} className="rounded bg-cyan-500/10 px-2 py-1 text-cyan-300 underline underline-offset-2">{id}</a>
      : <span key={index} className="text-amber-200">{id}（原文缺失）</span>)}
  </div>;
  const outputs = records(content.outputs);
  const interfaces = records(plan?.interfaces);
  const dataStructures = records(plan?.data_structures);
  const decisions = records(plan?.design_decisions);

  return <article className="min-w-0 space-y-6 rounded-xl border border-cyan-500/20 bg-slate-950 p-4">
    <header>
      <h3 className="text-sm font-medium text-cyan-200">任务 Agent Spec</h3>
      <p className="mt-1 break-all text-xs text-slate-500">来源 PRD：{agentSpec.source_spec_version_id}{boundSource ? ` · v${boundSource.revision}` : ''}</p>
    </header>
    {!implementationOnly && <>
    <Section title="任务目标"><p className={bodyClass}>{text(content.objective) || '未提供目标'}</p></Section>
    <Section title="任务范围"><TextList value={content.scope} /></Section>
    <div className="grid gap-5 md:grid-cols-2">
      <Section title="输入"><TextList value={content.inputs} /></Section>
      <Section title="输出">
        {outputs.length ? <ul className="space-y-2">{outputs.map((output, index) => <li key={index} className="rounded-lg border border-slate-800 p-3">
          <p className={bodyClass}>{text(output.name) || '未命名产出'}</p>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-400"><span>{text(output.format) || '未提供格式'}</span>
            <span>{output.required === true ? '必须交付' : output.required === false ? '可选交付' : '未提供交付要求'}</span></div>
        </li>)}</ul> : <TextList value={content.outputs} />}
      </Section>
    </div>
    </>}
    <Section title="对应需求原文">
      {!savedRequirements.length && boundSource && <p className="text-xs text-slate-500">根据本任务验收关联，从绑定的来源 PRD 读取原文。</p>}
      {requirements.length ? <ul className="space-y-3">{requirements.map((requirement, index) => <li key={index} id={requirementTarget(text(requirement.requirement_id))} className="scroll-mt-24 rounded-lg border border-slate-800 p-3">
        <div className="mb-1 flex gap-2 text-xs"><span className="font-mono text-cyan-300">{text(requirement.requirement_id)}</span><span className="text-slate-400">{text(requirement.priority) || '未提供优先级'}</span></div>
        <p className={bodyClass}>{text(requirement.statement)}</p>
      </li>)}</ul> : <p className="text-sm text-amber-200">{boundSource
        ? '绑定的来源 PRD 中未找到本任务验收关联的需求原文。'
        : '未找到绑定的来源 PRD，无法还原需求原文。'}</p>}
    </Section>
    <Section title="实施方案">
      {plan ? <>
        <p className={bodyClass}>{text(plan.overview) || '未提供实施概述'}</p>
        {steps.length ? <ol className="space-y-4">{steps.map((step, index) => <li key={index} className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <h5 className="text-sm font-medium text-slate-100">{text(step.step_id) || `步骤 ${index + 1}`} · {text(step.title) || '未提供步骤标题'}</h5>
          {renderRequirementLinks(step.requirement_ids)}
          <dl className="space-y-3"><Definition label="实现方法" value={step.implementation_method} /><Definition label="步骤产出" value={step.expected_output} /><Definition label="验证方式" value={step.verification_method} /></dl>
        </li>)}</ol> : <p className="text-sm text-amber-200">尚未记录具体实施步骤。</p>}
      </> : <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-200">此任务 Spec 尚未生成实现方案，需补齐具体步骤、实现方法、产出和验证方式。</p>}
    </Section>
    {plan && <>
      <Section title="接口定义">
        <p className={bodyClass}>{text(plan.interface_notes) || (interfaces.length ? '未记录接口适用性说明。' : '此方案未记录接口说明与定义。')}</p>
        {interfaces.map((item, index) => {
          const name = text(item.name) || `接口 ${index + 1}`;
          return <div key={index} className="min-w-0 space-y-4 rounded-xl border border-slate-800 p-4">
            <h5 className="text-sm font-medium text-slate-100">{name}<span className="ml-2 text-xs text-slate-400">{text(item.kind)}</span></h5>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-3 text-xs leading-5 text-cyan-100">{text(item.definition) || '未提供接口定义'}</pre>
            <InterfaceFields name={name} title="输入字段" value={item.inputs} />
            <InterfaceFields name={name} title="输出字段" value={item.outputs} />
            <div className="space-y-2"><h6 className="text-xs font-medium text-slate-400">错误处理</h6><TextList value={item.error_handling} empty="未记录错误处理" /></div>
          </div>;
        })}
      </Section>
      {dataStructures.length > 0 && <Section title="数据结构">
        {dataStructures.map((item, index) => <div key={index} className="space-y-3 rounded-xl border border-slate-800 p-4">
          <h5 className="text-sm font-medium text-slate-100">{text(item.name) || '未命名数据结构'}</h5>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-3 text-xs leading-5 text-cyan-100">{text(item.definition) || '未提供数据定义'}</pre>
          <h6 className="text-xs font-medium text-slate-400">校验规则</h6><TextList value={item.validation_rules} empty="未记录校验规则" />
        </div>)}
      </Section>}
      {decisions.length > 0 && <Section title="设计决策"><ul className="space-y-3">
        {decisions.map((item, index) => <li key={index} className="space-y-2 rounded-lg border border-slate-800 p-3">
          <span className={`text-xs ${item.status === 'FIXED' ? 'text-cyan-300' : 'text-amber-200'}`}>{item.status === 'FIXED' ? '已确定' : item.status === 'PROPOSED' ? '提案 · 待确认' : '状态未提供'}</span>
          <p className={bodyClass}>{text(item.decision) || '未提供决策'}</p><dl><Definition label="理由" value={item.reason} /></dl>
        </li>)}
      </ul></Section>}
    </>}
    {!implementationOnly && <>
    <details className="rounded-lg border border-slate-800 p-3">
      <summary className="cursor-pointer text-sm text-slate-300">验收、约束与执行信息</summary>
      <div className="mt-4 space-y-5">
        <Section title="验收标准">{criteria.length ? <ol className="space-y-3">{criteria.map((criterion, index) => <li key={index} className="space-y-2 rounded-lg bg-slate-900 p-3">
          {renderRequirementLinks(criterion.requirement_ids)}<p className={bodyClass}>{text(criterion.criterion) || '未提供验收标准'}</p>
          <dl className="space-y-2"><Definition label="验证方式" value={criterion.verification_method} /><Definition label="预期结果" value={criterion.expected_result} /></dl>
        </li>)}</ol> : <p className="text-sm text-slate-500">未提供验收标准</p>}</Section>
        <div className="grid gap-5 md:grid-cols-2">
          {([
            ['排除范围', 'exclusions'], ['固定约束', 'fixed_constraints'], ['可配置部分', 'configurable_parts'],
            ['扩展点', 'extension_points'], ['所需技能', 'required_skills'], ['允许工具', 'allowed_tools'],
            ['允许路径', 'allowed_paths'], ['测试要求', 'test_obligations'], ['上下文引用', 'context_refs'], ['风险', 'risks'],
          ] as const).map(([title, field]) => <Section key={field} title={title}><TextList value={content[field]} empty="未列出" /></Section>)}
        </div>
        <dl className="grid gap-4 md:grid-cols-2"><Definition label="负责角色" value={content.responsible_role} /><Definition label="建议负责人" value={content.suggested_assignee} /></dl>
        <Section title="依赖任务"><TextList value={content.dependency_work_item_ids ?? agentSpec.dependency_work_item_ids} empty="未列出依赖" /></Section>
        <Section title="待确认问题">
          {records(content.open_questions).map((question, index) => <div key={index} className="space-y-2 rounded-lg bg-slate-900 p-3">
            <p className={bodyClass}>{text(question.question) || '未提供问题内容'}</p>
            <p className="text-xs text-amber-200">{question.blocking === true ? '阻塞执行' : question.blocking === false ? '不阻塞执行' : '未提供阻塞状态'}</p>
            <dl className="space-y-2"><Definition label="风险负责人" value={question.risk_owner} /><Definition label="接受的后果" value={question.accepted_consequence} /></dl>
          </div>)}
          {!records(content.open_questions).length && <TextList value={content.open_questions} empty="未列出待确认问题" />}
        </Section>
      </div>
    </details>
    <details className="rounded-lg border border-slate-800 p-3">
      <summary className="cursor-pointer text-sm text-slate-400">原始 JSON</summary>
      <pre className="mt-3 max-h-[520px] overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-slate-400">{JSON.stringify(agentSpec.content, null, 2)}</pre>
    </details>
    </>}
  </article>;
}
