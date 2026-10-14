import { useEffect, useRef, useState } from 'react';

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
export type Channel = 'slack' | 'email' | 'webhook';
// A workflow's escalation: after a review waits this long, more reviewers may vote and a notice goes out.
export type Escalation = { after_seconds: number; approver_role: 'approver' | null; reviewers: string[]; channels: Channel[] | null };
export type Policy = {
  allowedModels: string[]; allowedProviders: string[]; tokenLimit: number; costLimitUsd: number;
  approvalRequired?: boolean; approvalThresholdUsd?: number; approverRole?: string;
  requiredApprovals?: number; approvalTimeoutSeconds?: number;
  inputSchema?: InputSchema | null; captureContent?: 'none' | 'redacted' | 'full';
  approvalEscalation?: Escalation | null;
};
// The escalation a run started with, and where its review stands.
export type RunEscalation = Escalation & { waiting_since: number | null; escalates_at: number | null; escalated: boolean; recorded_at: number | null };
// Whether the signed-in reviewer may decide this waiting run now, and when escalation would let them.
export type ApprovalAccess = { eligible: boolean; via: 'role' | 'escalation' | null; eligible_at: number | null };
export type Step = {
  step_id: string; action: string; provider: string; model: string; tool: string;
  tokens: number; cost_usd: number; duration_ms: number; timestamp: number;
  status_code: number | null; receipt_id: string; chain_id: string;
  attempts: unknown[]; receipt: Record<string, unknown>;
  content?: { input: string | null; output: string | null; truncated: { input: boolean; output: boolean }; redaction: 'redacted' | 'full' } | null;
  content_reason?: string;
};
export type StepSummary = { step_id: string; action: string; name?: string; calls: number; duration_ms: number; tokens: number; cost_usd: number };
export type RunSummary = {
  elapsed_seconds: number | null; model_calls: number; tool_calls: number; model_ms: number; tool_ms: number;
  review_seconds: number | null; review_open: boolean; tokens: number; cost_usd: number; by_step: StepSummary[];
};
export type WorkflowInsight = {
  workflow: string; runs: number; outcomes: Record<'completed' | 'rejected' | 'failed' | 'canceled' | 'awaiting_approval' | 'running', number>;
  completion_rate: number | null; median_seconds: number | null; p95_seconds: number | null; median_review_seconds: number | null;
  average_cost_usd: number | null; total_cost_usd: number;
  slowest_step: { step_id: string; name?: string; action?: string; median_ms: number } | null; costliest_step: { step_id: string; name?: string; action?: string; average_cost_usd: number } | null;
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
  approval_escalation?: RunEscalation;
  approval_access?: ApprovalAccess;
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

// Short-lived answers for reads that rarely change (workflow policies, models), keyed by session.
// Any change made through api() clears it, so this tab never shows a policy older than its own edits.
const cache = new Map<string, { at: number; value: Promise<unknown> }>();

export async function api<T>(csrfToken: string, path: string, init: RequestInit = {}): Promise<T> {
  const csrf = csrfToken || document.cookie.split('; ').find(value => value.startsWith('aw_csrf='))?.slice(8) || '';
  let response: Response;
  const headers = new Headers(init.headers);
  const change = Boolean(init.method && init.method !== 'GET');
  if (change) {
    cache.clear();
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }
  if (init.body) headers.set('Content-Type', 'application/json');
  try {
    response = await fetch(path, {
      ...init, cache: 'no-store', credentials: 'include',
      headers,
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new Error('Cannot reach the gateway. Check your connection, then try again.');
  } finally {
    // A read cached while this change was in flight may predate it.
    if (change) cache.clear();
  }
  if (response.ok && headers.get('Accept') === 'text/csv') return await response.text() as T;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/v1/auth/')) window.dispatchEvent(new Event('aw:expired'));
    const detail = body.error?.message || body.detail?.message || (typeof body.detail === 'string' ? body.detail : '');
    const fallback = advice[response.status] || (response.status >= 502 ? advice[503] : response.status >= 500 ? advice[500] : 'The request failed. Try again.');
    throw Object.assign(new Error(`${detail || fallback} (${response.status})`), { status: response.status, fields: body.detail?.fields });
  }
  // Team settings, alert rules, retention, registered workflows and escalations share one document revision.
  // A write here moves it on, so other editors on the page follow instead of failing their next save.
  const match = headers.get('If-Match');
  if (change && match !== null && typeof body?.revision === 'number') {
    window.dispatchEvent(new CustomEvent<Moved>('aw:revision', { detail: { from: Number(match), to: body.revision } }));
  }
  return body as T;
}

type Moved = { from: number; to: number };
// The revision to send as If-Match: the one last read, moved on by this tab's own writes to the same document.
// A change from anywhere else still conflicts, as it should.
export function useRevision(current: number | undefined) {
  const [revision, setRevision] = useState(current);
  useEffect(() => setRevision(current), [current]);
  useEffect(() => {
    const follow = (event: Event) => {
      const { from, to } = (event as CustomEvent<Moved>).detail;
      setRevision(value => value === from ? to : value);
    };
    window.addEventListener('aw:revision', follow);
    return () => window.removeEventListener('aw:revision', follow);
  }, []);
  return revision;
}

// Used when the gateway sends no message of its own.
const advice: Record<number, string> = {
  401: 'Your sign-in expired or the credential is invalid. Sign in again with a valid team API key.',
  403: 'Your role cannot do this. Ask your team admin for the access you need.',
  404: 'Not found in this team or project. It may have been removed or be past its retention period.',
  409: 'This changed since you loaded it. Refresh, review the latest version, then try again.',
  422: 'Some values were not accepted. Check them, then try again.',
  429: 'A spend or request limit was reached. Review Costs, or ask your team admin to raise the limit.',
  500: 'The gateway hit an unexpected error. Try again; if it keeps happening, ask your operator to check the gateway logs.',
  503: 'The gateway is unavailable right now. Try again in a moment; if it persists, ask your operator to check the gateway, Redis and Temporal.',
};

// Policies and models change rarely; pages that only read them share one answer for a minute.
export const policyCache = 60000;

// A read that later pages render; refreshes keep the last answer on screen until the new one arrives.
export type DataState<T> = { data?: T; error?: string; loading: boolean; settled: number };

export function useData<T>(csrfToken: string, path: string, revision = 0, cacheMs = 0): DataState<T> {
  const [state, setState] = useState<DataState<T> & { path: string }>({ path, loading: true, settled: 0 });
  useEffect(() => {
    const controller = new AbortController();
    // A different path never shows the previous path's answer; a refresh of the same path keeps it.
    setState(previous => ({ path, data: previous.path === path ? previous.data : undefined, loading: true, settled: previous.settled }));
    const key = `${csrfToken}\n${path}`;
    const hit = cache.get(key);
    let request: Promise<T>;
    if (cacheMs && revision === 0 && hit && Date.now() - hit.at < cacheMs) request = hit.value as Promise<T>;
    else if (cacheMs) {
      // Shared answers are not tied to one page's lifetime, so leaving a page does not cancel them for another.
      request = api<T>(csrfToken, path);
      cache.set(key, { at: Date.now(), value: request });
      request.catch(() => { if (cache.get(key)?.value === request) cache.delete(key); });
    } else request = api<T>(csrfToken, path, { signal: controller.signal });
    request.then(
      data => { if (!controller.signal.aborted) setState(previous => ({ path, data, loading: false, settled: previous.settled + 1 })); },
      error => {
        if (!controller.signal.aborted) setState(previous => ({ path, data: previous.path === path ? previous.data : undefined, error: error.message, loading: false, settled: previous.settled + 1 }));
      },
    );
    return () => controller.abort();
  }, [csrfToken, path, revision, cacheMs]);
  return state.path === path ? state : { loading: true, settled: state.settled };
}

// Refreshes while a condition holds: the next refresh waits for the previous answer, never runs in a
// hidden tab, and catches up as soon as the tab is visible again.
export function usePolling(active: boolean, refresh: () => void, settled: number, delay = 5000) {
  const latest = useRef(refresh);
  useEffect(() => { latest.current = refresh; });
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => { if (!document.hidden) latest.current(); }, delay);
    const visible = () => { if (!document.hidden) { window.clearTimeout(timer); latest.current(); } };
    document.addEventListener('visibilitychange', visible);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [active, settled, delay]);
}

// Links from workflow results and receipts open only as web pages.
export const webUrl = (value: unknown) => {
  if (typeof value !== 'string') return undefined;
  try { return ['http:', 'https:'].includes(new URL(value).protocol) ? value : undefined; } catch { return undefined; }
};

export const money = (value: number | null | undefined) => value == null ? '—' : value > 0 && value < 0.005 ? '<$0.01' : `$${value.toFixed(2)}`;
// ResearchWorkflow → Research, DocumentQAWorkflow → Document Q&A, GitHubIssueTriageWorkflow → GitHub issue triage.
export const workflowName = (value: string) => value.replace(/Workflow$/, '').split(/(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/)
  .map((word, i) => i === 0 || /[A-Z].*[A-Z]/.test(word) ? word : word.toLowerCase()).join(' ').replace(/\bGit hub\b/i, 'GitHub').replace(/\bQA\b/, 'Q&A') || value;
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
// Run IDs are time-ordered UUIDs, so the start is shared by runs made close together; show the random end.
export const shortId = (value: string) => value.slice(-8);
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
// A gateway older than approval escalation sends no access; its waiting runs count as reviewable, like the SDK's review queue.
export const reviewable = (run: Run) => !run.approval_access || run.approval_access.eligible;
// Who an escalation lets review: "every approver and 2 named reviewers", or nobody new.
export const escalationWho = (escalation: Pick<Escalation, 'approver_role' | 'reviewers'>) => {
  const named = escalation.reviewers.length;
  return [escalation.approver_role === 'approver' && 'every approver', named > 0 && (named === 1 ? 'one named reviewer' : `${named} named reviewers`)].filter(Boolean).join(' and ');
};
// "every approver may now review." An escalation that widens nobody only notifies.
export function escalationReach(escalation: Pick<Escalation, 'approver_role' | 'reviewers'>, done: boolean) {
  const who = escalationWho(escalation);
  if (who) return `${who} ${done ? 'may now review' : 'may review then'}.`;
  return done ? 'a notice went out; who may review is unchanged.' : 'a notice goes out; who may review stays the same.';
}
export const channelNames: Record<string, string> = { slack: 'Slack', email: 'Email', webhook: 'Webhook' };
export const status = (run: Run) => run.progress?.stage === 'awaiting_approval' ? 'awaiting_approval'
  : run.status === 'completed' && (run.outcome ?? (run.result as { status?: string } | undefined)?.status) === 'rejected' ? 'rejected' : run.status;
export const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
