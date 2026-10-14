import { randomUUID } from 'node:crypto';
import { setTimeout } from 'node:timers/promises';
import { GatewayError, GatewayRetryAfterError, GatewayStreamError, GatewayTimeoutError, GatewayTransportError } from './errors';
import { DEFAULT_TIMEOUT_MS, send, transportCode } from './http';
import type { ResponseKind } from './http';
import type {
  AuditFilters, AuditPage, AuditRange, AuditVerification, CaptureMode, CreatedKey, KeyList, KeyOptions, KeyUpdate,
  ManagedKey, RunFilters, RunPage, StartedRun, TeamSettings, TeamSettingValue, TeamSpend, TeamSSO, WorkflowRun,
  ModelList, DeploymentReadiness, AlertRules, TeamAlertRules, TeamWorkflows, WorkflowRegistration, WorkflowInsights, InsightFilters, WorkflowRegistrationOptions, Invitation, Onboarding, ProviderSetup, InstalledTemplate, WorkflowTemplate, WorkflowSecret, RetentionPolicy, TeamRetention, TeamDataStatus, TeamDataExport, TeamTelemetry,
  ApiList, ApiObject, BatchOptions, ChatCompletion, ChatMessage, InferenceParams, MessagesParams, ReceiptAction,
  ResponseParams, SandboxUsage, TeamInfo, TriggerList, TriggerState,
  ApprovalEscalationOptions, ApprovalEscalations, ApprovalEscalationSaved, ReviewQueueFilters,
} from './types';

export interface ClientOptions {
  apiKey?: string;
  /** Sent as `X-Sandbox-ID`. Leave unset for a credential bound to a team or sandbox; the gateway uses that one. */
  sandboxId?: string;
  /** Extra headers on every request, such as `traceparent`. The API key always sets `Authorization`. */
  headers?: Record<string, string>;
  /** Per-attempt timeout in milliseconds (default 120000). A stream's limit applies between chunks. */
  timeoutMs?: number;
  /** Retries after the first attempt for transient failures (default 2). */
  maxRetries?: number;
  /** First retry delay in milliseconds, doubled per retry (default 250). */
  retryBackoffMs?: number;
  /** Longest `Retry-After` in seconds worth waiting for; longer delays throw GatewayRetryAfterError (default 30). */
  retryAfterCap?: number;
  /** Aborts this client's requests and retry waits; use `withOptions({ signal })` for one call. */
  signal?: AbortSignal;
  /** A fetch implementation for proxies or tests (default: the global fetch). */
  fetch?: typeof fetch;
}

/**
 * How a failed request may be repeated. `idempotent` requests retry transient failures. `unsafe` requests create or
 * change state, so they retry only when the gateway provably did not act: a connection that never opened, or a
 * 429/503 rejection. `never` requests return the only copy of a secret and run once.
 */
type RetryMode = 'idempotent' | 'unsafe' | 'never';

interface RequestInit {
  body?: unknown;
  headers?: Record<string, string>;
  retry?: RetryMode;
  read?: ResponseKind;
}

const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);
// The gateway returns these before doing any work (rate limit, load shed, backend unavailable).
const REJECTED_BEFORE_PROCESSING = new Set([429, 503]);
// Connection failures that happen before a request byte is sent.
const NOT_SENT = new Set(['ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN', 'EHOSTUNREACH', 'ENETUNREACH', 'UND_ERR_CONNECT_TIMEOUT']);

/** A workflow function with the name Temporal registers it under, optionally declared with `withInputSchema`. */
export type WorkflowReference = string | (((...args: never[]) => Promise<unknown>) & { inputSchema?: Record<string, unknown> });

function workflowName(workflow: WorkflowReference): string {
  const name = typeof workflow === 'string' ? workflow : workflow.name;
  if (!name) throw new TypeError('Pass a workflow type name or a named workflow function.');
  return name;
}

function retryable(error: unknown, mode: RetryMode): boolean {
  if (mode === 'never') return false;
  if (error instanceof GatewayError) {
    return (mode === 'idempotent' ? RETRYABLE_STATUS : REJECTED_BEFORE_PROCESSING).has(error.statusCode);
  }
  if (!(error instanceof GatewayTransportError)) return false;
  return mode === 'idempotent' || NOT_SENT.has(error.code ?? '');
}

/** Yield `choices[0].delta.content` from an OpenAI-compatible SSE stream until `[DONE]`. */
async function* streamedContent(body: ReadableStream<Uint8Array>, idleMs: number, signal?: AbortSignal): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const next = async () => {
    let timer: ReturnType<typeof globalThis.setTimeout> | undefined;
    const idle = new Promise<never>((_resolve, reject) => {
      timer = globalThis.setTimeout(() => reject(new GatewayTimeoutError(idleMs)), idleMs);
    });
    try {
      return await Promise.race([reader.read(), idle]);
    } catch (error) {
      if (signal?.aborted) throw signal.reason;
      if (error instanceof GatewayTimeoutError) throw error;
      throw new GatewayTransportError(transportCode(error));
    } finally {
      globalThis.clearTimeout(timer);
    }
  };
  const content = (line: string): string | null | undefined => {
    if (!line.startsWith('data:')) return undefined;
    const data = line.slice(5).trim();
    if (data === '[DONE]') return null;
    let event: unknown;
    try {
      event = JSON.parse(data);
    } catch {
      return undefined;
    }
    if (!event || typeof event !== 'object' || Array.isArray(event)) return undefined;
    const { error, choices } = event as { error?: unknown; choices?: { delta?: { content?: unknown } }[] };
    if (error && typeof error === 'object') throw new GatewayStreamError(error as Record<string, unknown>);
    const text = Array.isArray(choices) ? choices[0]?.delta?.content : undefined;
    return typeof text === 'string' && text ? text : undefined;
  };
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await next();
      buffer += done ? `${decoder.decode()}\n` : decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        const text = content(line);
        if (text === null) return;
        if (text !== undefined) yield text;
      }
      if (done) return;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

/**
 * Client for the gateway's workflow, team administration and OpenAI/Anthropic-compatible APIs.
 *
 * Retries are bounded, back off exponentially and honor `Retry-After` up to `retryAfterCap` seconds. Calls that
 * create state (keys, settings, uploads, approvals, ...) retry only when the gateway provably did not act, so a
 * retry never applies a change twice. Response fields keep the API's snake_case names.
 */
export class GatewayClient {
  constructor(readonly baseUrl: string, private readonly options: ClientOptions = {}) {
    let url: URL;
    try {
      url = new URL(baseUrl);
    } catch {
      throw new TypeError('The gateway URL must be absolute, such as http://127.0.0.1:8080.');
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      throw new TypeError('The gateway URL must use http or https.');
    }
  }

  /** A client with the same settings and these overrides, such as `{ signal }` or `{ timeoutMs }` for one call. */
  withOptions(options: Partial<ClientOptions>): GatewayClient {
    return new GatewayClient(this.baseUrl, { ...this.options, ...options });
  }

  private async request<T>(method: string, path: string, init: RequestInit = {}): Promise<T> {
    const mode = init.retry ?? (method === 'GET' ? 'idempotent' : 'unsafe');
    const { signal } = this.options;
    const headers = {
      ...this.options.headers,
      ...(this.options.sandboxId ? { 'X-Sandbox-ID': this.options.sandboxId } : {}),
      ...init.headers,
    };
    for (let attempt = 0; ; attempt++) {
      try {
        return await send<T>(this.baseUrl, this.options.apiKey, {
          method, path, body: init.body, headers, read: init.read, signal,
          timeoutMs: this.options.timeoutMs, fetch: this.options.fetch,
        });
      } catch (error) {
        if (signal?.aborted || !retryable(error, mode)) throw error;
        const delay = error instanceof GatewayError ? error.retryAfter : undefined;
        if (delay !== undefined && delay > (this.options.retryAfterCap ?? 30)) {
          throw new GatewayRetryAfterError(error as GatewayError);
        }
        if (attempt >= Math.max(0, this.options.maxRetries ?? 2)) throw error;
        const backoff = Math.max((this.options.retryBackoffMs ?? 250) * 2 ** attempt, (delay ?? 0) * 1000);
        await (signal ? setTimeout(backoff, undefined, { signal }) : setTimeout(backoff));
      }
    }
  }

  // --- OpenAI- and Anthropic-compatible inference ---

  /** Create a chat completion. Extra OpenAI fields (`max_tokens`, `tools`, `temperature`, ...) pass through. */
  chat(messages: ChatMessage[], params: InferenceParams = {}): Promise<ChatCompletion> {
    return this.request('POST', '/v1/chat/completions', { body: { messages, ...params }, retry: 'idempotent' });
  }

  /**
   * Stream a chat completion, yielding the text of each content delta. A terminal gateway `error` event throws
   * GatewayStreamError so a truncated answer is never mistaken for a complete one. Streams are not retried: once
   * text flows, a retry would produce a second, different answer.
   */
  async *chatStream(messages: ChatMessage[], params: InferenceParams = {}): AsyncGenerator<string> {
    const response = await this.request<Response>('POST', '/v1/chat/completions', {
      body: { messages, ...params, stream: true }, retry: 'never', read: 'response',
    });
    if (!response.body) return;
    yield* streamedContent(response.body, this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS, this.options.signal);
  }

  /** Create a legacy text completion (`/v1/completions`). */
  completions(prompt: string | string[], params: InferenceParams = {}): Promise<ApiObject> {
    return this.request('POST', '/v1/completions', { body: { prompt, ...params }, retry: 'idempotent' });
  }

  embeddings(input: string | string[], params: { model?: string } = {}): Promise<ApiList> {
    return this.request('POST', '/v1/embeddings', { body: { input, ...params }, retry: 'idempotent' });
  }

  /** Classify input against the gateway content policy. */
  moderations(input: string | string[]): Promise<ApiObject> {
    return this.request('POST', '/v1/moderations', { body: { input }, retry: 'idempotent' });
  }

  /** Run several chat completions in one synchronous call (`/v1/batch-inference`); see createBatch for the Batch API. */
  batch(requests: Record<string, unknown>[]): Promise<ApiObject> {
    return this.request('POST', '/v1/batch-inference', { body: { requests }, retry: 'idempotent' });
  }

  /** Create an Anthropic-style message (`/v1/messages`) on the same governed path as chat. */
  messages(messages: ChatMessage[], maxTokens: number, params: MessagesParams = {}): Promise<ApiObject> {
    return this.request('POST', '/v1/messages', { body: { messages, max_tokens: maxTokens, ...params }, retry: 'idempotent' });
  }

  // --- Asynchronous Files and Batch API (BATCH_API_ENABLED on the gateway) ---

  /** Upload a JSONL batch input file (purpose `batch`) and return the file object. */
  uploadBatchFile(content: string | Uint8Array | Blob, filename = 'batch.jsonl'): Promise<ApiObject> {
    const form = new FormData();
    form.append('purpose', 'batch');
    form.append('file', content instanceof Blob ? content : new Blob([content as BlobPart], { type: 'application/jsonl' }), filename);
    return this.request('POST', '/v1/files', { body: form });
  }

  getFile(fileId: string): Promise<ApiObject> {
    return this.request('GET', `/v1/files/${encodeURIComponent(fileId)}`);
  }

  /** Download raw file content, such as a batch output or error file. */
  getFileContent(fileId: string): Promise<Uint8Array> {
    return this.request('GET', `/v1/files/${encodeURIComponent(fileId)}/content`, { read: 'bytes' });
  }

  listFiles(): Promise<ApiList> {
    return this.request('GET', '/v1/files');
  }

  deleteFile(fileId: string): Promise<ApiObject> {
    return this.request('DELETE', `/v1/files/${encodeURIComponent(fileId)}`);
  }

  /** Create an asynchronous batch from an uploaded input file. */
  createBatch(inputFileId: string, options: BatchOptions = {}): Promise<ApiObject> {
    return this.request('POST', '/v1/batches', {
      body: {
        input_file_id: inputFileId, endpoint: options.endpoint ?? '/v1/chat/completions',
        completion_window: options.completionWindow ?? '24h', metadata: options.metadata,
      },
    });
  }

  getBatch(batchId: string): Promise<ApiObject> {
    return this.request('GET', `/v1/batches/${encodeURIComponent(batchId)}`);
  }

  cancelBatch(batchId: string): Promise<ApiObject> {
    return this.request('POST', `/v1/batches/${encodeURIComponent(batchId)}/cancel`, { body: {} });
  }

  /** List batches for this sandbox, most recent first. */
  listBatches(options: { limit?: number; after?: string } = {}): Promise<ApiList> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 20) });
    if (options.after !== undefined) query.set('after', options.after);
    return this.request('GET', `/v1/batches?${query}`);
  }

  // --- OpenAI Responses API ---

  /**
   * Create a Responses API response. `store: true` (with `previous_response_id` to chain) keeps conversation state
   * on the gateway; such a call retries only when the gateway provably did not store it.
   */
  createResponse(input: string | Record<string, unknown>[], params: ResponseParams = {}): Promise<ApiObject> {
    return this.request('POST', '/v1/responses', { body: { input, ...params }, retry: params.store ? 'unsafe' : 'idempotent' });
  }

  getResponse(responseId: string): Promise<ApiObject> {
    return this.request('GET', `/v1/responses/${encodeURIComponent(responseId)}`);
  }

  deleteResponse(responseId: string): Promise<ApiObject> {
    return this.request('DELETE', `/v1/responses/${encodeURIComponent(responseId)}`);
  }

  responseInputItems(responseId: string): Promise<ApiList> {
    return this.request('GET', `/v1/responses/${encodeURIComponent(responseId)}/input_items`);
  }

  // --- Agent-action receipts (AGENT_RECEIPTS_ENABLED on the gateway) ---

  /**
   * Record an agent action on the audit hash chain. The receipt is evidence that another control acted; it permits
   * nothing. `fields` holds optional attributes such as `target`, `tool` and `detail`.
   */
  recordReceipt(actionType: ReceiptAction, decision: 'allowed' | 'denied', fields: Record<string, unknown> = {}): Promise<ApiObject> {
    return this.request('POST', '/v1/receipts', { body: { action_type: actionType, decision, ...fields } });
  }

  // --- Discovery, accounting and health ---

  /** Discover the current credential's team, role and projects. */
  team(): Promise<TeamInfo> {
    return this.request('GET', '/v1/team');
  }

  /** This sandbox's usage and estimated cost. */
  usage(): Promise<SandboxUsage> {
    return this.request('GET', '/v1/usage');
  }

  /** This sandbox's budget usage, limits and window TTL. */
  sandboxBudget(): Promise<Record<string, unknown>> {
    return this.request('GET', '/v1/sandbox/budget');
  }

  /** Whether the gateway reports itself and its runtimes ready (`/readyz`). Never throws for an unready gateway. */
  async ready(): Promise<boolean> {
    try {
      await this.request('GET', '/readyz', { retry: 'never', read: 'text' });
      return true;
    } catch (error) {
      if (this.options.signal?.aborted) throw error;
      return false;
    }
  }

  // --- Team administration ---

  invitations(): Promise<Invitation[]> {
    return this.request('GET', '/v1/team/invitations');
  }

  /** Return a one-day, single-use token. Runs once: the response holds the only copy of the token. */
  createInvitation(name: string, options: KeyOptions = {}): Promise<Invitation & { token: string }> {
    return this.request('POST', '/v1/team/invitations', { body: { name, ...options }, retry: 'never' });
  }

  revokeInvitation(invitationId: string): Promise<Invitation> {
    return this.request('DELETE', `/v1/team/invitations/${encodeURIComponent(invitationId)}`);
  }

  /** Exchange an invitation once; save the returned credential securely. */
  acceptInvitation(token: string): Promise<CreatedKey> {
    return this.request('POST', '/v1/auth/invitations/accept', { body: { token }, retry: 'never' });
  }

  listKeys(): Promise<KeyList> {
    return this.request('GET', '/v1/team/keys');
  }

  /** Export current UTC month usage as CSV, scoped to this credential's team/project. */
  exportUsage(): Promise<string> {
    return this.request('GET', '/v1/usage/export', { headers: { Accept: 'text/csv' }, read: 'text' });
  }

  /** Issue a key whose plaintext is returned only here; role defaults to viewer. Expiry is ISO-8601 with a timezone. */
  createKey(name: string, options: KeyOptions = {}): Promise<CreatedKey> {
    return this.request('POST', '/v1/team/keys', { body: { name, ...options } });
  }

  /** Change supplied fields only; null clears project or expires_at (admin only). */
  updateKey(keyId: string, changes: KeyUpdate): Promise<ManagedKey> {
    return this.request('PATCH', `/v1/team/keys/${encodeURIComponent(keyId)}`, { body: changes });
  }

  /** Revoke a key immediately; the current key cannot revoke itself. */
  revokeKey(keyId: string): Promise<ManagedKey> {
    return this.request('DELETE', `/v1/team/keys/${encodeURIComponent(keyId)}`);
  }

  /** Read effective values, policy defaults and the current revision (unrestricted admin only). */
  teamSettings(): Promise<TeamSettings> {
    return this.request('GET', '/v1/team/settings');
  }

  /** Inspect this team's company sign-in policy (unrestricted admin only). */
  teamSSO(): Promise<TeamSSO> {
    return this.request('GET', '/v1/team/sso');
  }

  deployment(): Promise<DeploymentReadiness> {
    return this.request('GET', '/v1/team/deployment');
  }

  alertRules(): Promise<TeamAlertRules> {
    return this.request('GET', '/v1/team/alert-rules');
  }

  setAlertRules(rules: AlertRules, revision: number | string): Promise<TeamAlertRules> {
    return this.request('PUT', '/v1/team/alert-rules', { body: rules, headers: { 'If-Match': String(revision) } });
  }

  teamSpend(): Promise<TeamSpend> {
    return this.request('GET', '/v1/team/spend');
  }

  /** List the models this caller may use; simulated marks the Compose demo local fakes. */
  models(): Promise<ModelList> {
    return this.request('GET', '/v1/models');
  }

  onboarding(): Promise<Onboarding> {
    return this.request('GET', '/v1/team/onboarding');
  }

  /** Encrypt a credential for an already approved team provider; version zero creates it. */
  setProviderKey(provider: string, value: string, expectedVersion = 0): Promise<ProviderSetup> {
    return this.request('PUT', `/v1/team/providers/${encodeURIComponent(provider)}/key`,
      { body: { value, expected_version: expectedVersion } });
  }

  workflowTemplates(): Promise<WorkflowTemplate[]> {
    return this.request('GET', '/v1/workflow-templates');
  }

  installTemplate(templateId: string, version: string): Promise<InstalledTemplate> {
    return this.request('POST', `/v1/workflow-templates/${encodeURIComponent(templateId)}/install`, { body: { version } });
  }

  /** List registered workflows, reserved names, and the models, tools and limits a registration may use. */
  teamWorkflows(): Promise<TeamWorkflows> {
    return this.request('GET', '/v1/team/workflows');
  }

  /**
   * Register or replace a workflow type (unrestricted admin only). Pass the type name or the exported workflow
   * function; a function declared with `withInputSchema` also supplies its input schema. The gateway accepts only
   * models, providers and tools already approved for the team, derives network egress from them and keeps limits
   * within the team's own. Omit `revision` to use the current one; pass a reviewed value to refuse a concurrent change.
   */
  async registerWorkflow(
    workflow: WorkflowReference, models: string[], options: WorkflowRegistrationOptions = {}, revision?: number | string,
  ): Promise<WorkflowRegistration> {
    const name = workflowName(workflow);
    const body: Record<string, unknown> = { allowed_models: models, ...options };
    if (typeof workflow !== 'string' && !('input_schema' in options) && workflow.inputSchema) {
      body.input_schema = workflow.inputSchema;
    }
    const reviewed = revision ?? (await this.teamWorkflows()).revision;
    return this.request('PUT', `/v1/team/workflows/${encodeURIComponent(name)}`,
      { body, headers: { 'If-Match': String(reviewed) } });
  }

  /** Remove a registered workflow type; new calls for its runs are denied. */
  async removeWorkflow(workflow: WorkflowReference, revision?: number | string): Promise<{ revision: number; removed: string }> {
    const name = workflowName(workflow);
    const reviewed = revision ?? (await this.teamWorkflows()).revision;
    return this.request('DELETE', `/v1/team/workflows/${encodeURIComponent(name)}`,
      { headers: { 'If-Match': String(reviewed) } });
  }

  /** List per-workflow approval escalation and the channels it may notify (unrestricted admin only). */
  approvalEscalations(): Promise<ApprovalEscalations> {
    return this.request('GET', '/v1/team/approval-escalations');
  }

  /**
   * Escalate reviews of `workflow` that still wait `afterSeconds` after the draft is ready. Escalation notifies the
   * team's alert channels (or `channels`; `[]` sends nothing) and lets more reviewers vote: every approver with
   * `approver_role: 'approver'`, and the identities in `reviewers` (OIDC subjects or managed key IDs holding an
   * approver role). Runs keep the configuration they started with. Omit `revision` to use the current one; pass the
   * value you reviewed to refuse a concurrent change. Unrestricted team admin only.
   */
  async setApprovalEscalation(
    workflow: WorkflowReference, afterSeconds: number, options: ApprovalEscalationOptions = {}, revision?: number | string,
  ): Promise<ApprovalEscalationSaved> {
    const name = workflowName(workflow);
    const reviewed = revision ?? (await this.approvalEscalations()).revision;
    return this.request('PUT', `/v1/team/workflows/${encodeURIComponent(name)}/approval-escalation`,
      { body: { after_seconds: afterSeconds, ...options }, headers: { 'If-Match': String(reviewed) } });
  }

  /** Stop escalating reviews of `workflow` for runs started afterwards. */
  async removeApprovalEscalation(workflow: WorkflowReference, revision?: number | string): Promise<{ revision: number; removed: string }> {
    const name = workflowName(workflow);
    const reviewed = revision ?? (await this.approvalEscalations()).revision;
    return this.request('DELETE', `/v1/team/workflows/${encodeURIComponent(name)}/approval-escalation`,
      { headers: { 'If-Match': String(reviewed) } });
  }

  /**
   * Summarize recent retained runs per workflow: outcomes, duration, review wait and cost. `days` is 1-30.
   * At most 200 recent runs are scanned; `truncated` says when the window held more.
   */
  workflowInsights(filters: InsightFilters = {}): Promise<WorkflowInsights> {
    const query = new URLSearchParams({ days: String(filters.days ?? 7) });
    if (filters.project !== undefined) query.set('project', filters.project);
    if (filters.workflow !== undefined) query.set('workflow', filters.workflow);
    return this.request('GET', `/v1/workflow-insights?${query}`);
  }

  workflowSecrets(workflow: string): Promise<WorkflowSecret[]> {
    return this.request('GET', `/v1/workflows/${encodeURIComponent(workflow)}/secrets`);
  }

  /** Zero creates a secret; the current version rotates it. Values are never listed. */
  setWorkflowSecret(workflow: string, name: string, value: string, expectedVersion: number): Promise<WorkflowSecret> {
    return this.request('PUT', `/v1/workflows/${encodeURIComponent(workflow)}/secrets/${encodeURIComponent(name)}`,
      { body: { value, expected_version: expectedVersion } });
  }

  /** Call inside an activity and do not return the value into Temporal workflow history. */
  resolveWorkflowSecret(runId: string, stepId: string, name: string): Promise<WorkflowSecret & { value: string }> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/secrets/${encodeURIComponent(name)}/resolve`,
      { body: {}, headers: { 'X-Workflow-Run-ID': runId, 'X-Workflow-Step-ID': stepId } });
  }

  teamRetention(): Promise<TeamRetention> {
    return this.request('GET', '/v1/team/retention');
  }

  setTeamRetention(policy: RetentionPolicy, revision: number | string): Promise<TeamRetention> {
    return this.request('PUT', '/v1/team/retention', { body: policy, headers: { 'If-Match': String(revision) } });
  }

  teamData(): Promise<TeamDataStatus> {
    return this.request('GET', '/v1/team/data');
  }

  exportTeamData(): Promise<TeamDataExport> {
    return this.request('GET', '/v1/team/data/export');
  }

  deleteTeamData(confirmTeam: string): Promise<TeamDataStatus> {
    return this.request('DELETE', '/v1/team/data', { body: { confirm_team: confirmTeam } });
  }

  teamTelemetry(): Promise<TeamTelemetry> {
    return this.request('GET', '/v1/team/telemetry');
  }

  /** Set both monthly limits atomically. null disables a limit; zero is a zero-dollar limit. */
  setSpendLimits(limits: { softLimitUsd: number | null; hardLimitUsd: number | null }, options: { revision: number | string }): Promise<TeamSettings> {
    return this.updateTeamSettings({ soft_cost_limit_usd: limits.softLimitUsd, cost_limit_usd: limits.hardLimitUsd }, options);
  }

  /** Change capture for future steps, for the team default or a named workflow. */
  setContentCapture(mode: CaptureMode, options: { revision: number | string; workflow?: string }): Promise<TeamSettings> {
    const field = options.workflow === undefined ? 'capture_content' : `workflows.${options.workflow}.capture_content`;
    return this.updateTeamSettings({ [field]: mode }, options);
  }

  /** Atomically override fields with If-Match; stale revisions raise 409 and invalid fields raise 422. */
  updateTeamSettings(fields: Record<string, TeamSettingValue>, options: { revision: number | string }): Promise<TeamSettings> {
    return this.request('PATCH', '/v1/team/settings', { body: { fields }, headers: { 'If-Match': String(options.revision) } });
  }

  /** Reset a field to policy using a revision or quoted ETag; stale revisions raise 409. */
  resetTeamSetting(field: string, options: { revision: number | string }): Promise<TeamSettings> {
    return this.request('DELETE', `/v1/team/settings/${encodeURIComponent(field)}`,
      { headers: { 'If-Match': String(options.revision) } });
  }

  /** Read one newest-first audit page; times are inclusive Unix seconds and limit defaults to 50 (maximum 200). */
  audit(options: AuditFilters = {}): Promise<AuditPage> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries({
      from: options.from, to: options.to, event_type: options.eventType, actor: options.actor,
      project: options.project, run_id: options.runId, cursor: options.cursor, limit: options.limit,
    })) {
      if (value !== undefined) query.set(key, String(value));
    }
    return this.request('GET', `/v1/team/audit${query.size ? `?${query}` : ''}`);
  }

  /** Verify every retained event in the time range, regardless of other filters; ok is null when disabled. */
  verifyAudit(options: AuditRange = {}): Promise<AuditVerification> {
    const query = new URLSearchParams();
    if (options.from !== undefined) query.set('from', String(options.from));
    if (options.to !== undefined) query.set('to', String(options.to));
    return this.request('GET', `/v1/team/audit/verify${query.size ? `?${query}` : ''}`);
  }

  /** Yield original events as newline-terminated JSON, following cursors with a fixed to (default: now).
   * Disabled views throw Error; read failures propagate, possibly after yielding partial output.
   * Retention still applies. The export is not a complete process log for the operator verifier.
   */
  async *exportAudit(options: AuditFilters = {}): AsyncGenerator<string> {
    const filters = { ...options, to: options.to ?? Date.now() / 1000 };
    for (;;) {
      const page = await this.audit(filters);
      if (!page.enabled) throw new Error(page.message || 'Audit view is off.');
      for (const entry of page.events) yield JSON.stringify(entry.event) + '\n';
      if (page.next_cursor === null) return;
      filters.cursor = page.next_cursor;
    }
  }

  /** Start once; reuse requestId with identical input after an ambiguous network failure. */
  startRun(workflow: string, input: unknown, options: { project?: string; requestId?: string } = {}): Promise<StartedRun> {
    return this.request('POST', '/v1/workflow-runs', {
      body: { workflow, input, project: options.project ?? null, request_id: options.requestId ?? randomUUID() },
      retry: 'idempotent',
    });
  }

  /** Limit bounds scanned records (1-100, default 20); empty filtered pages can still have next_cursor. */
  runs(options: RunFilters = {}): Promise<RunPage> {
    const query = new URLSearchParams({ offset: String(options.offset ?? 0) });
    for (const [key, value] of Object.entries(options)) {
      if (value !== undefined) query.set(key, String(value));
    }
    return this.request('GET', `/v1/workflow-runs?${query}`);
  }

  /** Yield full run details and retained step content as JSON Lines, following every cursor page.
   * Retention and status changes continue during export. Read failures may leave partial output.
   */
  async *exportRuns(options: RunFilters = {}): AsyncGenerator<string> {
    const filters = { ...options };
    for (;;) {
      const page = await this.runs(filters);
      if (page.next_cursor === undefined) throw new Error('Run export requires gateway v0.6.0 or newer; upgrade the gateway and retry.');
      for (const run of page.runs) yield JSON.stringify(await this.run(run.run_id)) + '\n';
      if (page.next_cursor === null) return;
      delete filters.offset;
      filters.cursor = page.next_cursor;
    }
  }

  run(runId: string): Promise<WorkflowRun> {
    return this.request('GET', `/v1/workflow-runs/${encodeURIComponent(runId)}`);
  }

  /**
   * Yield runs awaiting a review this credential may give now, newest first, following every cursor page. Each
   * run's `approval_access` says whether you are eligible through your role or an approval escalation; a gateway
   * that predates escalation sends none, and its waiting runs are yielded unfiltered.
   */
  async *reviewQueue(filters: ReviewQueueFilters = {}): AsyncGenerator<WorkflowRun> {
    const query: RunFilters = { status: 'awaiting_approval', limit: filters.limit ?? 100, project: filters.project, workflow: filters.workflow };
    for (;;) {
      const page = await this.runs(query);
      for (const run of page.runs) {
        if (!run.approval_access || run.approval_access.eligible) yield run;
      }
      if (!page.next_cursor) return;
      query.cursor = page.next_cursor;
    }
  }

  /** Record your vote; inspect the run for quorum completion or a pending review. */
  approveRun(runId: string, options: { approved?: boolean } = {}): Promise<{ run_id: string; approved: boolean; reviewer: string }> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/approve`, { body: { approved: options.approved ?? true } });
  }

  cancelRun(runId: string): Promise<{ run_id: string; status: string }> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/cancel`, { body: {} });
  }

  retryRun(runId: string): Promise<StartedRun> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/retry`, { body: {} });
  }

  /** List configured workflow triggers and their effective pause state. */
  triggers(): Promise<TriggerList> {
    return this.request('GET', '/v1/workflow-triggers');
  }

  pauseTrigger(workflow: string, name: string, options: { paused?: boolean } = {}): Promise<TriggerState> {
    return this.request('PATCH', `/v1/workflow-triggers/${encodeURIComponent(workflow)}/${encodeURIComponent(name)}`,
      { body: { paused: options.paused ?? true } });
  }
}
