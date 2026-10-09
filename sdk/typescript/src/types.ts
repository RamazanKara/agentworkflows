export interface ChatCompletion {
  choices: { message: { content?: string | null; [key: string]: unknown } }[];
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  [key: string]: unknown;
}

export type Role = 'admin' | 'builder' | 'approver' | 'viewer';

export interface KeyOptions {
  role?: Role;
  project?: string | null;
  expires_at?: string | null;
}

export interface KeyUpdate extends KeyOptions {
  name?: string;
}

export interface ManagedKey {
  key_id: string;
  team: string;
  name: string;
  role: Role;
  project: string | null;
  created_by: string;
  created_at: number;
  expires_at: number | null;
  last_used_at: number | null;
  revoked_at: number | null;
}

export interface CreatedKey extends ManagedKey {
  key: string;
}

export interface KeyList {
  keys: ManagedKey[];
}

export type TeamSettingValue = string | number | boolean | string[] | null;

export interface TeamSetting {
  value: TeamSettingValue;
  source: 'policy' | 'override';
  policy_default: TeamSettingValue;
}

export interface TeamSettings {
  revision: number;
  updated_by: string | null;
  updated_at: number | null;
  fields: Record<string, TeamSetting>;
  routes: string[];
  providers: string[];
  approver_roles: ('admin' | 'approver')[];
}

export type CaptureMode = 'none' | 'redacted' | 'full';
export interface SpendAlert {
  id: string;
  level: 'soft' | 'hard';
  limit_usd: number;
  reserved_and_spent_usd: number;
  requested_usd: number;
  created_at: number;
  webhook_status: 'disabled' | 'pending' | 'delivered' | 'failed';
  attempts: number;
}
export interface TeamSpend {
  team_id: string;
  window_start: number;
  window_end: number;
  soft_limit_usd: number | null;
  hard_limit_usd: number | null;
  reserved_and_spent_usd: number;
  status: 'ok' | 'soft_limit' | 'hard_limit';
  alerts: SpendAlert[];
}

export interface AuditRange {
  from?: number;
  to?: number;
}

export interface AuditFilters extends AuditRange {
  eventType?: string;
  actor?: string;
  project?: string;
  runId?: string;
  cursor?: string;
  limit?: number;
}

export interface AuditEntry {
  id: string;
  chain_id: string;
  sequence: number;
  team_sequence: number;
  record_hash: string;
  view_prev_hash: string;
  view_hash: string;
  event: Record<string, unknown>;
}

export interface AuditPage {
  enabled: boolean;
  message?: string | null;
  events: AuditEntry[];
  next_cursor: string | null;
}

export interface AuditPosition {
  chain_id: string;
  sequence: number;
  reason: string;
}

export interface AuditVerification {
  enabled: boolean;
  message?: string | null;
  ok: boolean | null;
  checked: number;
  first_break: AuditPosition | null;
  boundaries: AuditPosition[];
}

export interface RunBudget {
  token_limit: number;
  cost_limit_usd: number;
}

export interface Call {
  kind: 'model' | 'tool' | 'approval_waiting';
  payload: Record<string, unknown>;
  budget: RunBudget;
  tool: string;
  data_classification: 'public' | 'internal' | 'confidential' | 'restricted';
}

export interface StartedRun {
  run_id: string;
  workflow_id: string;
  project: string;
  request_id: string;
}

export interface Receipt {
  step_id: string;
  action: string;
  receipt_id: string;
  chain_id: string;
  tokens: number;
  cost_usd: number;
  status_code: number | null;
  receipt: Record<string, unknown>;
}

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  workflow: string;
  project: string;
  trigger?: { name: string; kind: 'cron' | 'webhook' };
  status: 'running' | 'completed' | 'failed' | 'canceled' | 'terminated' | 'continued_as_new' | 'timed_out';
  progress?: { stage: string; draft?: string; reviewer?: string; run_id?: string };
  result?: unknown;
  budget: RunBudget & { tokens: number; cost_usd: number };
  timeline?: Receipt[];
}

export interface RunFilters {
  project?: string;
  workflow?: string;
  trigger?: string;
  status?: WorkflowRun['status'] | 'awaiting_approval';
  cursor?: string;
  offset?: number;
  limit?: number;
}

export interface RunPage {
  runs: WorkflowRun[];
  next_offset: number | null;
  next_cursor: string | null;
}

export interface TriggerRequest {
  workflow: string;
  trigger: string;
  firing_id?: string;
  run_id?: string;
}

export interface TriggerResult {
  paused?: boolean;
  run_id?: string;
  status?: WorkflowRun['status'];
}

export interface GovernedActivities {
  'agentworkflows.call'(call: Call): Promise<Record<string, unknown>>;
  'agentworkflows.trigger'(request: TriggerRequest): Promise<TriggerResult>;
}
