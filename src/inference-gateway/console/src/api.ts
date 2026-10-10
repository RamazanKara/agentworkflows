import { useEffect, useState } from 'react';

export type Team = {
  team_id: string;
  role: 'admin' | 'builder' | 'approver' | 'viewer';
  projects: string[];
  providers: string[];
  cost_limit_usd: number | null;
  soft_cost_limit_usd?: number | null;
  capture_content?: 'none' | 'redacted' | 'full';
  project_budgets?: Record<string, number | null>;
  model_routes?: Record<string, string>;
  notifications?: { channels: string[]; budget_threshold: number };
  provider_configuration?: Record<string, { environment_variable: string; configured: boolean }>;
};
export type Session = { csrfToken: string; team: Team; id: number; name?: string; keyId?: string; reviewerId?: string };
export type BrowserSession = { csrf_token: string; principal: { key_id?: string; sub?: string; name?: string }; sandbox_id: string };
export type AuthConfig = { api_key: boolean; jwt: boolean; oidc: { enabled: boolean; provider_name: string; login_url: string } };
export type TeamSSO = {
  team_id: string; enabled: boolean; provider_name: string | null; role_source: 'groups' | 'claim';
  team_claim: string; project_claim: string; role_claim: string | null; default_role: Team['role'] | null;
  groups_claim: string | null; group_role_mappings: Record<string, Team['role']>;
};
export type InputProperty = {
  title?: string;
  type: 'string' | 'number' | 'integer' | 'boolean' | 'array';
  description?: string; default?: string | number | boolean | string[];
  examples?: (string | number | boolean | string[])[];
  enum?: (string | number | boolean | string[])[]; items?: { type: 'string' };
  minLength?: number; pattern?: string;
  minimum?: number; maximum?: number; exclusiveMinimum?: number;
};
export type InputSchema = {
  type: 'object'; properties: Record<string, InputProperty>; required?: string[]; description?: string;
};
export type Policy = {
  allowedModels: string[]; allowedProviders: string[]; tokenLimit: number; costLimitUsd: number;
  approvalRequired?: boolean; approvalThresholdUsd?: number; approverRole?: string;
  requiredApprovals?: number; approvalTimeoutSeconds?: number;
  inputSchema?: InputSchema | null; captureContent?: 'none' | 'redacted' | 'full';
};
export type Step = {
  step_id: string; action: string; provider: string; model: string; tool: string;
  tokens: number; cost_usd: number; duration_ms: number; timestamp: number;
  status_code: number | null; receipt_id: string; chain_id: string;
  attempts: unknown[]; receipt: Record<string, unknown>;
  content?: { input: string | null; output: string | null; truncated: { input: boolean; output: boolean }; redaction: 'redacted' | 'full' } | null;
  content_reason?: string;
};
export type StepSummary = { step_id: string; action: string; calls: number; duration_ms: number; tokens: number; cost_usd: number };
export type RunSummary = {
  elapsed_seconds: number | null; model_calls: number; tool_calls: number; model_ms: number; tool_ms: number;
  review_seconds: number | null; review_open: boolean; tokens: number; cost_usd: number; by_step: StepSummary[];
};
export type WorkflowInsight = {
  workflow: string; runs: number; outcomes: Record<'completed' | 'rejected' | 'failed' | 'canceled' | 'awaiting_approval' | 'running', number>;
  completion_rate: number | null; median_seconds: number | null; p95_seconds: number | null; median_review_seconds: number | null;
  average_cost_usd: number | null; total_cost_usd: number;
  slowest_step: { step_id: string; median_ms: number } | null; costliest_step: { step_id: string; average_cost_usd: number } | null;
};
export type WorkflowInsights = { window: { days: number; start: number; end: number }; projects: string[]; scanned: number; skipped: number; truncated: boolean; workflows: WorkflowInsight[] };
export type Run = {
  run_id: string; workflow: string; project: string; created_at: number; status: string;
  trigger?: { name: string; kind: 'cron' | 'webhook' };
  template?: { id: string; version: string };
  progress?: { stage: string; draft?: string; message?: string; required_approvals?: number; approved_by?: string[]; expires_at?: string; approver_role?: string };
  budget: { tokens: number; cost_usd: number; token_limit: number; cost_limit_usd: number };
  timeline?: Step[];
  summary?: RunSummary;
  result?: unknown;
  outcome?: string;
  error?: { code: string; message: string };
};
export type RunPage = { runs: Run[]; next_cursor: string | null };
export type CostRow = { calls?: number; tokens?: number; cost_usd?: number };
export type Usage = {
  estimated_cost: number;
  spend: {
    project: string | null; window_start: number; window_seconds: number; period?: 'month';
    reserved_and_spent_usd: number | null; cost_limit_usd: number | null;
    providers: Record<string, CostRow>; workflows: Record<string, CostRow>; accounting: string;
  };
};
export type Budget = { usage: { estimated_tokens: number }; limits: { estimated_tokens: number }; window_seconds?: number };
export type TeamSpend = {
  team_id: string; window_start: number; window_end: number;
  soft_limit_usd: number | null; hard_limit_usd: number | null; reserved_and_spent_usd: number;
  status: 'ok' | 'soft_limit' | 'hard_limit';
  alerts: { id: string; level: 'soft' | 'hard'; limit_usd: number; reserved_and_spent_usd: number;
    requested_usd: number; created_at: number; webhook_status: 'disabled' | 'pending' | 'delivered' | 'failed'; attempts: number }[];
};
export type Models = { data: { id: string; owned_by: string; simulated?: boolean }[] };
export type SettingValue = number | string | boolean | string[] | null;
export type SettingField = { value: SettingValue; source: 'policy' | 'override'; policy_default: SettingValue };
export type TeamSettings = {
  revision: number; updated_by: string | null; updated_at: number | null;
  fields: Record<string, SettingField>; routes: string[]; providers: string[]; approver_roles: string[];
};
export type ApiError = Error & { status: number; fields?: { field: string; message: string }[] };

export async function api<T>(csrfToken: string, path: string, init: RequestInit = {}): Promise<T> {
  const csrf = csrfToken || document.cookie.split('; ').find(value => value.startsWith('aw_csrf='))?.slice(8) || '';
  let response: Response;
  const headers = new Headers(init.headers);
  if (csrf && init.method && init.method !== 'GET') headers.set('X-CSRF-Token', csrf);
  if (init.body) headers.set('Content-Type', 'application/json');
  try {
    response = await fetch(path, {
      ...init, cache: 'no-store', credentials: 'include',
      headers,
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new Error('Cannot reach the gateway. Check your connection, then try again.');
  }
  if (response.ok && headers.get('Accept') === 'text/csv') return await response.text() as T;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/v1/auth/')) window.dispatchEvent(new Event('aw:expired'));
    const detail = body.error?.message || body.detail?.message || (typeof body.detail === 'string' ? body.detail : '');
    const advice: Record<number, string> = {
      401: 'Credential expired or invalid. Sign in with a valid team API key or JWT.',
      403: 'Your role cannot perform this action. Ask your team admin for the required access.',
      404: 'Not found in this team or project. The run may be outside Temporal retention.',
      409: 'This run changed. Refresh and review its current state before trying again.',
      422: 'Check the workflow input and selected project, then try again.',
      429: 'The spend or request limit was reached. Review Costs or ask your team admin to update the limit.',
      503: 'The service is unavailable. Check the gateway, Redis, and Temporal, then retry.',
    };
    throw Object.assign(new Error(`${detail || advice[response.status] || 'The request failed.'} (${response.status})`), { status: response.status, fields: body.detail?.fields });
  }
  return body as T;
}

export function useData<T>(csrfToken: string, path: string, revision = 0) {
  const [state, setState] = useState<{ data?: T; error?: string }>({});
  useEffect(() => {
    const controller = new AbortController();
    setState(previous => ({ data: previous.data }));
    api<T>(csrfToken, path, { signal: controller.signal }).then(
      data => { if (!controller.signal.aborted) setState({ data }); },
      error => { if (!controller.signal.aborted) setState({ error: error.message }); },
    );
    return () => controller.abort();
  }, [csrfToken, path, revision]);
  return state;
}

export const money = (value: number | null | undefined) => value == null ? '—' : value > 0 && value < 0.005 ? '<$0.01' : `$${value.toFixed(2)}`;
// ResearchWorkflow → Research, DocumentQAWorkflow → Document QA, GitHubIssueTriageWorkflow → GitHub issue triage.
export const workflowName = (value: string) => value.replace(/Workflow$/, '').split(/(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/)
  .map((word, i) => i === 0 || /[A-Z].*[A-Z]/.test(word) ? word : word.toLowerCase()).join(' ').replace(/\bGit hub\b/i, 'GitHub') || value;
// 45 s, 12 min, 3.5 h, 4 days. A no-break space keeps the number and unit together.
const NB = String.fromCharCode(160);
export const duration = (seconds: number | null | undefined) => {
  if (seconds == null) return '—';
  if (seconds < 1) return `<1${NB}s`;
  if (seconds < 90) return `${Math.round(seconds)}${NB}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}${NB}min`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1).replace(/\.0$/, '')}${NB}h`;
  return `${Math.round(seconds / 86400)}${NB}days`;
};
export const milliseconds = (value: number) => value < 1000 ? `${Math.round(value)}${NB}ms` : `${(value / 1000).toFixed(1).replace(/\.0$/, '')}${NB}s`;
export const shortId = (value: string) => value.slice(0, 8);
export const number = (value: number | undefined) => (value ?? 0).toLocaleString();
// Oct 8, 12:26 PM; the year appears only when it differs from this year.
export const date = (value: number) => {
  const when = new Date(value * 1000);
  return when.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...(when.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) });
};
const providerNames: Record<string, string> = {
  openai: 'OpenAI', anthropic: 'Anthropic', 'azure-openai': 'Azure OpenAI', bedrock: 'AWS Bedrock',
  vertex: 'Vertex Gemini', ollama: 'Ollama', vllm: 'vLLM', tool: 'Tools',
};
export const providerName = (value: string) => providerNames[value] || value;
export const providerList = (values: string[]) => values.map(providerName).join(' and ');
// Admins see which provider keys are configured. With none present, every model call fails.
export const missingKeys = (team: Team) => Object.entries(team.provider_configuration || {}).filter(([, value]) => !value.configured).map(([provider]) => provider);
export const noProviderKeys = (team: Team) => Object.keys(team.provider_configuration || {}).length > 0 && missingKeys(team).length === Object.keys(team.provider_configuration || {}).length;
// The gateway flags routes served by local fakes (the Compose demo); demo guidance appears only then.
export const isDemo = (models?: Models) => Boolean(models?.data.some(model => model.simulated));
export const status = (run: Run) => run.progress?.stage === 'awaiting_approval' ? 'awaiting_approval'
  : run.status === 'completed' && (run.outcome ?? (run.result as { status?: string } | undefined)?.status) === 'rejected' ? 'rejected' : run.status;
export const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
