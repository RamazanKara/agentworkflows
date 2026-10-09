export interface ChatCompletion {
  choices: { message: { content?: string | null; [key: string]: unknown } }[];
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  [key: string]: unknown;
}

export type Role = 'admin' | 'builder' | 'approver' | 'viewer';

export interface TeamSSO {
  team_id: string;
  enabled: boolean;
  provider_name: string | null;
  role_source: 'groups' | 'claim';
  team_claim: string;
  project_claim: string;
  role_claim: string | null;
  default_role: Role | null;
  groups_claim: string | null;
  group_role_mappings: Record<string, Role>;
}

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

export interface ApprovalProgress {
  stage: string;
  draft?: string;
  reviewer?: string;
  run_id?: string;
  approval_policy_version?: 1;
  required_approvals?: number;
  approved_by?: string[];
  expires_at?: string;
  approver_role?: 'admin' | 'approver';
}

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  workflow: string;
  project: string;
  trigger?: { name: string; kind: 'cron' | 'webhook' };
  status: 'running' | 'completed' | 'failed' | 'canceled' | 'terminated' | 'continued_as_new' | 'timed_out';
  progress?: ApprovalProgress;
  result?: unknown;
  error?: { code: string; message: string };
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
export interface InstalledTemplate { id: string; version: string; workflow: string }
export interface WorkflowTemplate extends InstalledTemplate {
  name: string; description: string; installable: boolean; installed_version: string | null;
  input_schema: Record<string, unknown> | null;
}
export interface WorkflowSecret { name: string; version: number; updated_at: number }
export interface RetentionPolicy { run_seconds: number; content_seconds: number; audit_seconds: number }
export interface TeamRetention extends RetentionPolicy { revision: number }
export interface TeamDataStatus { team_id: string; status: 'active' | 'erasing' | 'erased'; external_follow_up: string[] }
export interface TeamDataExport {
  version: number; team_id: string; exported_at: number; settings: Record<string, unknown> | null;
  runs: Record<string, unknown>[]; start_intents: Record<string, unknown>[];
  keys: Record<string, unknown>[]; audit: Record<string, unknown>[];
  step_content: Record<string, unknown>; temporal_histories: Record<string, unknown>[];
  responses: Record<string, unknown>[]; files: Record<string, unknown>[]; batches: Record<string, unknown>[];
  external_follow_up: string[];
}
export interface TeamTelemetry { traces_enabled: boolean; metrics_enabled: boolean; protocol: string; service_name: string }

export interface ProviderSetup { provider: string; configured: boolean; version: number; can_save: boolean }
export interface Onboarding {
  providers: ProviderSetup[];
  sample: { template_id: string; version: string; workflow: string; installed: boolean; input: Record<string, unknown>; ready: boolean } | null;
  blockers: string[];
}

export interface Invitation {
  invitation_id: string; key_id: string; name: string; role: ManagedKey['role']; project: string | null;
  created_at: number; expires_at: number; accepted_at: number | null; revoked_at: number | null;
}

export interface AlertRules {
  events: ('awaiting_approval' | 'failed' | 'budget_threshold' | 'slow_step')[];
  channels: ('slack' | 'email' | 'webhook')[];
  budget_threshold: number;
  slow_step_ms: number;
}
export interface TeamAlertRules extends AlertRules {
  revision: number;
  available_channels: AlertRules['channels'];
}

export interface DeploymentReadiness {
  checks: { id: string; name: string; configured: boolean; action: string }[];
  verification_required: string[];
}
