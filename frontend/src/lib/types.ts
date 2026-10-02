// Response shapes of the backend API used by the operating environment (M1–M6).

export interface Access {
  organization_id: string;
  organization_name: string;
  role_name: string;
  is_platform_admin: boolean;
  permissions: string[];
}

export interface Membership {
  organization_id: string;
  organization_name: string | null;
  role_name: string;
  status: string;
}

export interface Me {
  user: { id: string; email: string; full_name: string | null };
  memberships: Membership[];
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Overview {
  window_days: number;
  agents_by_status: Record<string, number>;
  runs_total: number;
  runs_by_status: Record<string, number>;
  success_rate: number | null;
  escalation_rate: number | null;
  failure_rate: number | null;
  avg_run_seconds: number | null;
  pending_approvals: number;
  approvals_by_status: Record<string, number>;
  tool_calls: number;
  tool_failures: number;
  tool_denials: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  usage_by_model: Record<string, unknown>[];
  recent_escalations: Record<string, unknown>[];
}

export interface Agent {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  agent_type: string;
  memory_mode: string;
  default_provider: string | null;
  default_model: string | null;
  active_version_id: string | null;
  created_at: string;
}

export interface AgentTemplate {
  key: string;
  name: string;
  agent_type: string;
  summary: string;
  capabilities: string[];
  planned: string[];
  tools: Record<string, string | null>;
  memory_mode: string;
  version: string;
}

/** Why the server withheld a run's content (M8): never which source. */
export type WithheldReason = "restricted_sources" | "unknown_provenance";

export interface Run {
  id: string;
  agent_id: string;
  conversation_id: string | null;
  initiated_by: string | null;
  status: string;
  escalation_reason: string | null;
  error_code: string | null;
  step_count: number;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: string | number;
  last_model: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  /** True when the server withheld this run's content from you (ADR-0035, M8). */
  content_withheld: boolean;
  /** Why it was withheld; null when the content is shown. */
  content_withheld_reason: WithheldReason | null;
}

export interface RunStep {
  id: string;
  sequence: number;
  step_type: string;
  status: string;
  name: string | null;
  detail: Record<string, unknown> | null;
  error: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  created_at: string;
}

export interface RunDetail extends Run {
  steps: RunStep[];
}

export interface Approval {
  id: string;
  agent_id: string | null;
  tool_name: string;
  request_payload: Record<string, unknown>;
  reason: string | null;
  status: string;
  requested_by: string | null;
  expires_at: string | null;
  run_id: string | null;
  risk_level: string | null;
  modified_payload: Record<string, unknown> | null;
  decision_note: string | null;
  created_at: string;
  /** The request gates a publication derived from restricted sources (M9). */
  restricted_publication: boolean;
  /** True when the server withheld the arguments and note from you (M9.6):
   *  `request_payload` is then `{}`, and the others are null. */
  payload_withheld: boolean;
}

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  status: string;
  active_version_id: string | null;
  trigger_type: string | null;
  event_name: string | null;
  next_run_at: string | null;
  created_at: string;
  webhook_token?: string | null;
  webhook_path?: string | null;
}

export interface WorkflowStepRun {
  id: string;
  sequence: number;
  step_id: string;
  step_type: string;
  attempt: number;
  status: string;
  output: Record<string, unknown> | null;
  error: string | null;
  agent_run_id: string | null;
  decision: string | null;
  decision_note: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** The step gates a publication derived from restricted sources (M9). */
  restricted_publication: boolean;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  status: string;
  trigger_type: string;
  trigger_detail: Record<string, unknown>;
  /** Null when withheld: only the person the run acts for sees its content (ADR-0035). */
  input: Record<string, unknown> | null;
  current_step: string | null;
  current_attempt: number;
  steps_executed: number;
  initiated_by: string | null;
  error_code: string | null;
  error: string | null;
  next_attempt_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  content_withheld: boolean;
  content_withheld_reason: WithheldReason | null;
}

export interface WorkflowRunDetail extends WorkflowRun {
  context: Record<string, unknown> | null;
  steps: WorkflowStepRun[];
}

export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  due_at: string | null;
  assignee_id: string | null;
  created_by_agent_id: string | null;
  created_at: string;
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: { type?: string; id?: string } | null;
  read_at: string | null;
  created_at: string;
}

export interface Connection {
  id: string;
  provider: string;
  name: string;
  status: string;
  config: Record<string, unknown>;
  secret_fields: Record<string, string>;
  last_used_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface ExecutionResult {
  status: string;
  conversation_id: string;
  run_id: string | null;
  approval_id: string | null;
  tool_name: string | null;
  escalation_reason: string | null;
  message: { id: string; role: string; content: string } | null;
}
