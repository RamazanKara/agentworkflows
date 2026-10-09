import { Context } from '@temporalio/activity';
import { ApplicationFailure } from '@temporalio/common';
import { GatewayError, GatewayTransportError } from './errors';
import { requestJson } from './http';
import type { Call, GovernedActivities, TriggerRequest, TriggerResult } from './types';

export class GatewayActivities {
  constructor(readonly baseUrl: string, private readonly apiKey: string) {}

  private async request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    const context = Context.current();
    try {
      return await requestJson<T>(this.baseUrl, this.apiKey, method, path, body, headers,
        AbortSignal.any([context.cancellationSignal, AbortSignal.timeout(120_000)]));
    } catch (error) {
      if (context.cancellationSignal.aborted) return await context.cancelled;
      if (error instanceof GatewayError) {
        if (path.endsWith('/approval-waiting') && error.statusCode === 404) return {
          queued: false, policy_version: 1, approval_required: true, required_approvals: 1, approval_timeout_seconds: 604800,
        } as T;
        if (error.reason === 'trigger_paused') return { paused: true } as T;
        const retryable = [429, 500, 502, 503, 504].includes(error.statusCode) &&
          !['workflow_store_required', 'provider_not_configured', 'tool_not_configured'].includes(error.reason ?? '');
        throw ApplicationFailure.create({
          message: `Gateway activity failed (${error.statusCode}). Gateway request ID: ${error.requestId ?? 'unavailable'}`,
          type: error.reason ?? 'GatewayError', nonRetryable: !retryable,
          ...(retryable && error.retryAfter !== undefined ? { nextRetryDelay: error.retryAfter * 1000 } : {}),
        });
      }
      if (error instanceof GatewayTransportError) {
        throw ApplicationFailure.retryable(error.message, 'GatewayUnavailable');
      }
      throw error;
    }
  }

  async call(call: Call): Promise<Record<string, unknown>> {
    const info = Context.current().info;
    if (!info.workflowExecution || !info.workflowType) {
      throw ApplicationFailure.nonRetryable('Governed calls require a Temporal workflow execution.', 'WorkflowContextRequired');
    }
    const runId = info.workflowExecution.runId;
    if (call.kind === 'approval_waiting') {
      return this.request('POST', `/v1/workflow-runs/${runId}/approval-waiting`, Object.keys(call.payload).length ? call.payload : undefined);
    }
    await this.request('PUT', `/v1/workflow-runs/${runId}`, { ...call.budget, workflow: info.workflowType },
      { 'X-Workflow-ID': info.workflowExecution.workflowId });
    const headers = {
      'X-Workflow-Run-ID': runId, 'X-Workflow-Step-ID': info.activityId,
      'X-Data-Classification': call.data_classification,
    };
    const path = call.kind === 'model' ? '/v1/chat/completions'
      : call.kind === 'tool' ? `/v1/tools/${encodeURIComponent(call.tool)}/call` : undefined;
    if (!path) throw ApplicationFailure.nonRetryable('Use WorkflowGateway.model or .tool.', 'InvalidCall');
    return this.request('POST', path, call.payload, headers);
  }

  async trigger(request: TriggerRequest): Promise<TriggerResult> {
    if (request.run_id) return this.request('GET', `/v1/workflow-runs/${encodeURIComponent(request.run_id)}`);
    return this.request('POST', `/v1/workflow-triggers/${encodeURIComponent(request.workflow)}/${encodeURIComponent(request.trigger)}/fire`,
      { firing_id: request.firing_id });
  }

  activities(): GovernedActivities {
    return { 'agentworkflows.call': this.call.bind(this), 'agentworkflows.trigger': this.trigger.bind(this) };
  }
}
