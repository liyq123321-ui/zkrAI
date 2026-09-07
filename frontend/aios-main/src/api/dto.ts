export type CommandAction =
  | 'message'
  | 'skip_clarification'
  | 'create_spec'
  | 'revise'
  | 'approve'
  | 'reject'
  | 'rework'
  | 'publish_review'
  | 'convert_to_work_item'
  | 'restore_spec_version'
  | 'start_task'
  | 'complete_task'
  | 'fail_task';

export interface ClarificationQuestionDto {
  question_id: string;
  question: string;
  reason: string;
  affected_areas: string[];
  blocking: boolean;
  [key: string]: unknown;
}

export interface SessionSummaryDto {
  session_id: string;
  project_id: string;
  root_work_item_id: string | null;
  title: string;
}

export interface SessionStateDto {
  session_id: string;
  project_id: string;
  phase: string;
  state_version: number;
  current_spec_version_id: string | null;
  current_spec_status: string | null;
  legal_actions: CommandAction[];
  next_action: string;
  outstanding_questions: ClarificationQuestionDto[];
  review_findings: Array<Record<string, unknown>>;
}

export interface ProjectBriefDto {
  motivation: string;
  final_objective: string;
  known_scope: string[];
  exclusions: string[];
  reference_materials: string[];
  expected_deliverables: string[];
  time_constraints: string;
  staffing_constraints: string;
  final_approver: string;
  project_manager_ids: string[];
  root_owner_ids: string[];
}

export interface SpecReviewDto {
  command_id?: string | null;
  id: string;
  kind: string;
  verdict: string;
  findings: Array<Record<string, unknown>>;
  comments: string | null;
  created_at: string;
}

export interface SpecVersionDto {
  generator_call_id?: string;
  id: string;
  project_id: string;
  revision: number;
  content: Record<string, unknown>;
  markdown: string;
  generation_source: string;
  parent_version_id: string | null;
  change_summary: string;
  status: string;
  reviews: SpecReviewDto[];
  created_at: string;
}

export interface WorkItemDto {
  id: string;
  parent_id: string | null;
  kind: 'ROOT' | 'MILESTONE' | 'TASK' | null;
  title: string | null;
  description: string | null;
  objective: string | null;
  status?: string | null;
  scope: string[] | null;
  exclusions: string[] | null;
  outputs: unknown[] | null;
  acceptance_criteria: unknown[] | null;
  required_skills: string[] | null;
  responsible_role: string | null;
  suggested_assignee: string | null;
  dependency_work_item_ids: string[];
  /** Backend-computed per-item actions. The UI renders these and never derives them. */
  available_actions?: string[];
  /** Longest-path depth in the dependency DAG, for lane layout. */
  graph_depth?: number;
}

export interface AgentSpecDto {
  id: string;
  work_item_id: string;
  source_spec_version_id: string;
  dependency_work_item_ids: string[];
  content: Record<string, unknown>;
  created_at: string;
}

export interface AuditEventDto {
  id: string;
  event_type: string;
  actor_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export type AgentRuntimeStatus = 'running' | 'completed' | 'error';

export interface AgentRuntimeDto {
  agent_session_id: string;
  project_id: string;
  role: string;
  provider: string | null;
  model: string | null;
  purpose: string | null;
  status: AgentRuntimeStatus;
  current_operation: string;
  current_summary: string;
  current_call_id: string;
  started_at: string;
  completed_at: string | null;
  call_count: number;
}

export interface CommandResultDto {
  command_id: string;
  state: SessionStateDto;
  created_resource_ids: string[];
}

export type CommandJobStatus = 'pending' | 'processing' | 'succeeded' | 'failed';

export interface CommandJobAcceptedDto {
  command_id: string;
  status: CommandJobStatus;
  status_url: string;
  events_url: string;
}

export interface CommandJobReadDto {
  command_id: string;
  status: CommandJobStatus;
  status_version: number;
  result: CommandResultDto | null;
  error: { code: string; message: string } | null;
  progress_stage?: string | null;
  progress_message?: string | null;
  last_activity_at?: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export type CommandSubmissionDto = CommandResultDto | CommandJobAcceptedDto;

export interface HealthDto {
  status: 'ok';
  database: 'ok';
}

export interface PrdDocumentDto {
  wi: string;
  version: number;
  filename: string;
  pr_number: number;
  commit_sha: string | null;
  content: string;
  change_summary: string | null;
  er_diagrams: PrdErDiagramDto[];
}

export interface PrdErDiagramDto {
  diagram_id: string;
  title: string;
  after_section: 'functional_requirements' | 'system_boundaries' | 'core_objects' | 'main_flows';
  anchor: string;
  drawio_xml: string;
}

export interface PrdCommentableLinesDto {
  wi: string;
  version: number;
  filename: string;
  commit_sha: string;
  lines: Array<{ line: number; kind: 'context' | 'addition'; text: string }>;
}

export interface PrdCommentDto {
  id: number;
  path: string;
  line: number;
  author_type: 'human' | 'agent';
  body: string;
  resolved: boolean;
  replies: Array<{ id: number; author_type: 'human' | 'agent'; body: string }>;
}

export interface ReviewTaskDto {
  task_id: string;
  wi: string;
  status: 'pending' | 'processing' | 'done' | 'error';
  base_version: number;
  new_version: number | null;
  new_commit_sha: string | null;
  error: string | null;
}

export interface PublishReviewAcceptedDto {
  task_id: string;
  base_version: number;
  comment_count: number;
}

export interface ReviewTaskAcceptedDto {
  task_id: string;
  base_version: number;
  no_change: boolean;
}

export interface PrdDiffDto {
  wi: string;
  version: number;
  filename: string;
  commit_sha: string;
  patch: string;
}
