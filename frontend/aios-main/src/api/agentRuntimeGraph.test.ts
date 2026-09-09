import { describe, expect, it } from 'vitest';
import type { AgentRuntimeDto, AgentSpecDto, WorkItemDto } from './dto';
import { buildRuntimeAgentGraph } from './agentRuntimeGraph';

function runtime(
  agentSessionId: string,
  role: string,
  status: AgentRuntimeDto['status'],
): AgentRuntimeDto {
  return {
    agent_session_id:agentSessionId,
    project_id:'project-1',
    role,
    provider:'codex',
    model:null,
    purpose:`${role} purpose`,
    status,
    current_operation:'generate_spec',
    current_summary:'Working',
    current_call_id:`call-${agentSessionId}`,
    started_at:'2026-09-08T01:00:00Z',
    completed_at:status === 'running' ? null : '2026-09-08T01:01:00Z',
    call_count:1,
  };
}

function task(id: string, status: string, dependencies: string[]): WorkItemDto {
  return {
    id,
    parent_id:'milestone-1',
    kind:'TASK',
    title:`Task ${id}`,
    description:null,
    summary:null,
    objective:`Deliver ${id}`,
    status,
    scope:null,
    exclusions:null,
    outputs:null,
    acceptance_criteria:null,
    required_skills:null,
    responsible_role:'Engineer',
    suggested_assignee:`Agent ${id}`,
    dependency_work_item_ids:dependencies,
  };
}

function spec(workItemId: string): AgentSpecDto {
  return {
    id:`spec-${workItemId}`,
    work_item_id:workItemId,
    source_spec_version_id:'spec-v1',
    dependency_work_item_ids:[],
    content:{},
    created_at:'2026-09-08T01:00:00Z',
  };
}

describe('buildRuntimeAgentGraph', () => {
  it('always creates a solid PM and dashed planned reviewer/prototype skeleton', () => {
    const graph = buildRuntimeAgentGraph(
      { sessionId:'session-1', title:'Project' },
      [],
    );

    expect(graph.coreLevels.flat().map((node) => [node.role, node.status])).toEqual([
      ['PM', 'ready'],
      ['REVIEWER', 'planned'],
      ['PROTOTYPE_DESIGNER', 'planned'],
    ]);
    expect(graph.edges.map((edge) => [edge.key, edge.status])).toEqual([
      ['core:pm->core:reviewer', 'planned'],
      ['core:pm->core:prototype', 'planned'],
      ['core:reviewer->core:prototype', 'planned'],
    ]);
  });

  it('turns core nodes and incoming edges into their actual runtime states', () => {
    const graph = buildRuntimeAgentGraph(
      { sessionId:'session-1', title:'Project' },
      [
        runtime('pm', 'PM Agent', 'completed'),
        runtime('review', 'REVIEWER', 'running'),
        runtime('prototype', 'PROTOTYPE_DESIGNER', 'completed'),
      ],
    );

    expect(graph.coreLevels.flat().map((node) => node.status)).toEqual([
      'completed',
      'running',
      'completed',
    ]);
    expect(graph.edges.map((edge) => edge.status)).toEqual(['running', 'completed', 'completed']);
  });

  it('layers planned child Agents by task dependency and colors edges by target state', () => {
    const tasks = [
      task('a', 'todo', []),
      task('b', 'in_progress', ['a']),
      task('c', 'completed', ['b']),
    ];
    const graph = buildRuntimeAgentGraph(
      {
        sessionId:'session-1',
        title:'Project',
        workItems:tasks,
        agentSpecs:tasks.map((item) => spec(item.id)),
      },
      [],
    );

    expect(graph.taskLevels.map((level) => level.map((node) => node.workItem?.id))).toEqual([
      ['a'],
      ['b'],
      ['c'],
    ]);
    expect(graph.nodeById.get('task:a')?.status).toBe('planned');
    expect(graph.nodeById.get('task:b')?.status).toBe('running');
    expect(graph.nodeById.get('task:c')?.status).toBe('completed');
    expect(graph.edges.map((edge) => [edge.key, edge.status])).toEqual([
      ['core:pm->core:reviewer', 'planned'],
      ['core:pm->core:prototype', 'planned'],
      ['core:reviewer->core:prototype', 'planned'],
      ['core:pm->task:a', 'planned'],
      ['core:prototype->task:a', 'planned'],
      ['task:a->task:b', 'running'],
      ['task:b->task:c', 'completed'],
    ]);
  });
});
