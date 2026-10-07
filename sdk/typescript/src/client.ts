import { randomUUID } from 'node:crypto';
import { setTimeout } from 'node:timers/promises';
import { GatewayError, GatewayRetryAfterError, GatewayTransportError } from './errors';
import { requestJson } from './http';
import type { StartedRun, WorkflowRun } from './types';

export interface ClientOptions {
  apiKey?: string;
  timeoutMs?: number;
  maxRetries?: number;
  retryAfterCap?: number;
}

export class GatewayClient {
  constructor(readonly baseUrl: string, private readonly options: ClientOptions = {}) {}

  private async request<T>(method: string, path: string, body?: unknown, retry = method === 'GET'): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await requestJson<T>(this.baseUrl, this.options.apiKey, method, path, body, {},
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

  startRun(workflow: string, input: unknown, options: { project?: string; requestId?: string } = {}): Promise<StartedRun> {
    return this.request('POST', '/v1/workflow-runs', {
      workflow, input, project: options.project ?? null, request_id: options.requestId ?? randomUUID(),
    }, true);
  }

  runs(options: { project?: string; offset?: number } = {}): Promise<{ runs: WorkflowRun[]; next_offset: number | null }> {
    const query = new URLSearchParams({ offset: String(options.offset ?? 0) });
    if (options.project) query.set('project', options.project);
    return this.request('GET', `/v1/workflow-runs?${query}`);
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
