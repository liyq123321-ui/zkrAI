import { Search } from 'lucide-react';
import { RootTaskFilter } from './RootTaskFilter';
import type { WorkItemFilterState, WorkItemKindFilter } from './workItemFilters';

export function WorkItemFilterControls({ value, roots, agents, searchLabel, onChange }: {
  value: WorkItemFilterState;
  roots: Array<{ id: string; title: string }>;
  agents: string[];
  searchLabel: string;
  onChange: (value: WorkItemFilterState) => void;
}) {
  const patch = (next: Partial<WorkItemFilterState>) => onChange({ ...value, ...next });

  return <>
    <label className="ff-search-box">
      <Search aria-hidden="true" />
      <input aria-label={searchLabel} value={value.search} onChange={(event) => patch({ search: event.target.value })} placeholder="搜索工单 ID、标题或 Agent…" />
    </label>
    <RootTaskFilter roots={roots} selectedIds={value.selectedRootIds} onChange={(selectedRootIds) => patch({ selectedRootIds })} />
    <label className="ff-filter-label">类型：
      <select aria-label={`${searchLabel}类型`} value={value.kind} onChange={(event) => patch({ kind: event.target.value as WorkItemKindFilter })}>
        <option value="ALL">全部类型</option><option value="ROOT">项目需求</option>
        <option value="MILESTONE">里程碑</option><option value="TASK">子任务</option>
      </select>
    </label>
    <label className="ff-filter-label">执行者：
      <select aria-label={`${searchLabel}执行者`} value={value.agent} onChange={(event) => patch({ agent: event.target.value })}>
        <option value="ALL">所有 Agent</option>
        {agents.map((agent) => <option key={agent}>{agent}</option>)}
      </select>
    </label>
  </>;
}
