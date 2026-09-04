import type { AgentSpecDto, WorkItemDto } from './dto';

export interface EmployeeOption {
  id: string;
  name: string;
  role?: string;
}

// Frontend examples only. Replace this source with an employee API when available.
const exampleEmployees: EmployeeOption[] = [
  { id: 'preview-product', name: '产品同事（示例）', role: '产品 / 需求' },
  { id: 'preview-frontend', name: '前端同事（示例）', role: '前端开发' },
  { id: 'preview-backend', name: '后端同事（示例）', role: '后端开发' },
  { id: 'preview-qa', name: '测试同事（示例）', role: '测试 / 验收' },
];

export function getEmployeeOptions(workItems: WorkItemDto[], agentSpecs: AgentSpecDto[]): EmployeeOption[] {
  const employees = new Map<string, EmployeeOption>();
  const addCurrent = (id: unknown, role: unknown) => {
    if (typeof id !== 'string' || !id.trim() || employees.has(id)) return;
    employees.set(id, { id, name: id, role: typeof role === 'string' ? role : undefined });
  };
  workItems.forEach((item) => addCurrent(item.suggested_assignee, item.responsible_role));
  agentSpecs.forEach((spec) => addCurrent(spec.content.suggested_assignee, spec.content.responsible_role));
  exampleEmployees.forEach((employee) => {
    if (!employees.has(employee.id)) employees.set(employee.id, employee);
  });
  return [...employees.values()];
}
