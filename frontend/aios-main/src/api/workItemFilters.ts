import type { WorkItemDto } from './dto';
import { validWorkItemSummary } from './workItemSummary';

export type WorkItemKindFilter = 'ALL' | NonNullable<WorkItemDto['kind']>;

export type WorkItemFilterState = {
  search: string;
  selectedRootIds: string[] | null;
  kind: WorkItemKindFilter;
  agent: string;
};

export function defaultWorkItemFilters(): WorkItemFilterState {
  return {
    search: '',
    selectedRootIds: null,
    kind: 'ALL',
    agent: 'ALL',
  };
}

export function filterWorkItems(
  items: WorkItemDto[],
  filters: WorkItemFilterState,
  rootIdByItem: ReadonlyMap<string, string>,
  assigneeForItem: (item: WorkItemDto) => string,
): WorkItemDto[] {
  const query = filters.search.trim().toLowerCase();

  return items.filter((item) => {
    const assignee = assigneeForItem(item);

    if (filters.selectedRootIds !== null) {
      const rootId = rootIdByItem.get(item.id);
      if (!rootId || !filters.selectedRootIds.includes(rootId)) return false;
    }

    if (filters.kind !== 'ALL' && item.kind !== filters.kind) return false;
    if (filters.agent !== 'ALL' && assignee !== filters.agent) return false;

    if (!query) return true;

    return [item.id, item.title, validWorkItemSummary(item.summary), item.objective, item.description, assignee]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query));
  });
}
