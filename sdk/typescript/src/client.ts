import { randomUUID } from 'node:crypto';
import { setTimeout } from 'node:timers/promises';
import { GatewayError, GatewayRetryAfterError, GatewayTransportError } from './errors';
import { requestJson } from './http';
import type {
  AuditFilters, AuditPage, AuditRange, AuditVerification, CaptureMode, CreatedKey, KeyList, KeyOptions, KeyUpdate,
  ManagedKey, RunFilters, RunPage, StartedRun, TeamSettings, TeamSettingValue, TeamSpend, TeamSSO, WorkflowRun,
  ModelList, DeploymentReadiness, AlertRules, TeamAlertRules, TeamWorkflows, WorkflowRegistration, WorkflowInsights, InsightFilters, WorkflowRegistrationOptions, Invitation, Onboarding, ProviderSetup, InstalledTemplate, WorkflowTemplate, WorkflowSecret, RetentionPolicy, TeamRetention, TeamDataStatus, TeamDataExport, TeamTelemetry,
} from './types';

export interface ClientOptions {
  apiKey?: string;
  timeoutMs?: number;
  maxRetries?: number;
  retryAfterCap?: number;
}

export class GatewayClient {
  constructor(readonly baseUrl: string, private readonly options: ClientOptions = {}) {}

  private async request<T>(
    method: string, path: string, body?: unknown, retry = method === 'GET', headers: Record<string, string> = {},
  ): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await requestJson<T>(this.baseUrl, this.options.apiKey, method, path, body, headers,
          AbortSignal.timeout(this.options.timeoutMs ?? 120_000));
      } catch (error) {
        const transient = error instanceof GatewayTransportError ||
          (error instanceof GatewayError && [429, 500, 502, 503, 504].includes(error.statusCode));
        if (!retry || !transient) throw error;
        const delay = error instanceof GatewayError ? error.retryAfter : undefined;
        if (delay !== undefined && delay > (this.options.retryAfterCap ?? 30)) {
          throw new GatewayRetryAfterError(error as GatewayError);
        }
        if (attempt >= (this.options.maxRetries ?? 2)) throw error;
        await setTimeout(Math.max(250 * 2 ** attempt, (delay ?? 0) * 1000));
      }
    }
  }

  invitations(): Promise<Invitation[]> {
    return this.request('GET', '/v1/team/invitations');
  }

  createInvitation(name: string, options: KeyOptions = {}): Promise<Invitation & { token: string }> {
    return this.request('POST', '/v1/team/invitations', { name, ...options });
  }

  revokeInvitation(invitationId: string): Promise<Invitation> {
    return this.request('DELETE', `/v1/team/invitations/${encodeURIComponent(invitationId)}`);
  }

  acceptInvitation(token: string): Promise<CreatedKey> {
    return this.request('POST', '/v1/auth/invitations/accept', { token });
  }

  listKeys(): Promise<KeyList> {
    return this.request('GET', '/v1/team/keys');
  }

  /** Export current UTC month usage as CSV, scoped to this credential's team/project. */
  exportUsage(): Promise<string> {
    return this.request('GET', '/v1/usage/export', undefined, true, { Accept: 'text/csv' });
  }

  /** Issue a key whose plaintext is returned only here; role defaults to viewer. Expiry is ISO-8601 with a timezone. */
  createKey(name: string, options: KeyOptions = {}): Promise<CreatedKey> {
    return this.request('POST', '/v1/team/keys', { name, ...options });
  }

  /** Change supplied fields only; null clears project or expires_at (admin only). */
  updateKey(keyId: string, changes: KeyUpdate): Promise<ManagedKey> {
    return this.request('PATCH', `/v1/team/keys/${encodeURIComponent(keyId)}`, changes);
  }

  /** Revoke a key immediately; the current key cannot revoke itself. */
  revokeKey(keyId: string): Promise<ManagedKey> {
    return this.request('DELETE', `/v1/team/keys/${encodeURIComponent(keyId)}`, undefined, false);
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
    return this.request('PUT', '/v1/team/alert-rules', rules, false, { 'If-Match': String(revision) });
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

  setProviderKey(provider: string, value: string, expectedVersion = 0): Promise<ProviderSetup> {
    return this.request('PUT', `/v1/team/providers/${encodeURIComponent(provider)}/key`,
      { value, expected_version: expectedVersion });
  }

  workflowTemplates(): Promise<WorkflowTemplate[]> {
    return this.request('GET', '/v1/workflow-templates');
  }

  installTemplate(templateId: string, version: string): Promise<InstalledTemplate> {
    return this.request('POST', `/v1/workflow-templates/${encodeURIComponent(templateId)}/install`, { version });
  }

  /** List registered workflows, reserved names, and the models, tools and limits a registration may use. */
  teamWorkflows(): Promise<TeamWorkflows> {
    return this.request('GET', '/v1/team/workflows');
  }

  /**
   * Register or replace a workflow type (unrestricted admin only). The gateway accepts only models, providers
   * and tools already approved for the team, derives network egress from them and keeps limits within the
   * team's own. Omit `revision` to use the current one; pass a reviewed value to refuse a concurrent change.
   */
  async registerWorkflow(
    name: string, models: string[], options: WorkflowRegistrationOptions = {}, revision?: number | string,
  ): Promise<WorkflowRegistration> {
    const reviewed = revision ?? (await this.teamWorkflows()).revision;
    return this.request('PUT', `/v1/team/workflows/${encodeURIComponent(name)}`, { allowed_models: models, ...options },
      false, { 'If-Match': String(reviewed) });
  }

  /** Remove a registered workflow type; new calls for its runs are denied. */
  async removeWorkflow(name: string, revision?: number | string): Promise<{ revision: number; removed: string }> {
    const reviewed = revision ?? (await this.teamWorkflows()).revision;
    return this.request('DELETE', `/v1/team/workflows/${encodeURIComponent(name)}`, undefined, false,
      { 'If-Match': String(reviewed) });
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
      { value, expected_version: expectedVersion });
  }

  /** Call inside an activity and do not return the value into Temporal workflow history. */
  resolveWorkflowSecret(runId: string, stepId: string, name: string): Promise<WorkflowSecret & { value: string }> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/secrets/${encodeURIComponent(name)}/resolve`, {}, false,
      { 'X-Workflow-Run-ID': runId, 'X-Workflow-Step-ID': stepId });
  }

  teamRetention(): Promise<TeamRetention> {
    return this.request('GET', '/v1/team/retention');
  }

  setTeamRetention(policy: RetentionPolicy, revision: number | string): Promise<TeamRetention> {
    return this.request('PUT', '/v1/team/retention', policy, false, { 'If-Match': String(revision) });
  }

  teamData(): Promise<TeamDataStatus> {
    return this.request('GET', '/v1/team/data');
  }

  exportTeamData(): Promise<TeamDataExport> {
    return this.request('GET', '/v1/team/data/export');
  }

  deleteTeamData(confirmTeam: string): Promise<TeamDataStatus> {
    return this.request('DELETE', '/v1/team/data', { confirm_team: confirmTeam });
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
    return this.request('PATCH', '/v1/team/settings', { fields }, false, { 'If-Match': String(options.revision) });
  }

  /** Reset a field to policy using a revision or quoted ETag; stale revisions raise 409. */
  resetTeamSetting(field: string, options: { revision: number | string }): Promise<TeamSettings> {
    return this.request('DELETE', `/v1/team/settings/${encodeURIComponent(field)}`, undefined, false,
      { 'If-Match': String(options.revision) });
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

  startRun(workflow: string, input: unknown, options: { project?: string; requestId?: string } = {}): Promise<StartedRun> {
    return this.request('POST', '/v1/workflow-runs', {
      workflow, input, project: options.project ?? null, request_id: options.requestId ?? randomUUID(),
    }, true);
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

  approveRun(runId: string, options: { approved?: boolean } = {}): Promise<{ run_id: string; approved: boolean; reviewer: string }> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/approve`, { approved: options.approved ?? true });
  }

  cancelRun(runId: string): Promise<{ run_id: string; status: string }> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/cancel`, {});
  }

  retryRun(runId: string): Promise<StartedRun> {
    return this.request('POST', `/v1/workflow-runs/${encodeURIComponent(runId)}/retry`, {});
  }

  triggers(): Promise<Record<string, unknown>> {
    return this.request('GET', '/v1/workflow-triggers');
  }

  pauseTrigger(workflow: string, name: string, options: { paused?: boolean } = {}): Promise<Record<string, unknown>> {
    return this.request('PATCH', `/v1/workflow-triggers/${encodeURIComponent(workflow)}/${encodeURIComponent(name)}`,
      { paused: options.paused ?? true });
  }
}
