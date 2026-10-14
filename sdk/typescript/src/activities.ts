import { Context } from '@temporalio/activity';
import { ApplicationFailure } from '@temporalio/common';
import { AgentContext } from './adapters';
import { runContainer } from './containers';
import type { Kubectl } from './containers';
import { GatewayError, GatewayTransportError } from './errors';
import { requestJson } from './http';
import type { Call, GovernedActivities, TriggerRequest, TriggerResult } from './types';

export { AgentContext } from './adapters';
export type { AnthropicClientOptions, OpenAIClientOptions } from './adapters';

/** A framework agent run by `WorkflowGateway.agent(name, args)`; its result must be JSON-serializable. */
export type AgentHandler = (context: AgentContext, args: Record<string, unknown>) => Promise<unknown>;

export interface GatewayActivitiesOptions {
  /** Agents by name, for `WorkflowGateway.agent`. */
  agents?: Record<string, AgentHandler>;
  /** Runs kubectl for container steps (default: the `kubectl` binary on PATH). */
  kubectl?: Kubectl;
}

const NON_RETRYABLE_REASONS = ['workflow_store_required', 'provider_not_configured', 'tool_not_configured'];

type GovernedFailure = GatewayError | GatewayTransportError | ApplicationFailure;

/** The gateway or Temporal failure behind an error, following `cause` as framework clients wrap them. */
function governedCause(error: unknown): GovernedFailure | undefined {
  for (let cause = error, depth = 0; cause instanceof Error && depth < 8; depth++) {
    if (cause instanceof GatewayError || cause instanceof GatewayTransportError || cause instanceof ApplicationFailure) {
      return cause;
    }
    cause = cause.cause;
  }
  return undefined;
}

/** Map gateway failures to Temporal failures without response bodies, URLs or credentials. */
function failure(error: GatewayError): ApplicationFailure {
  const retryable = [429, 500, 502, 503, 504].includes(error.statusCode) && !NON_RETRYABLE_REASONS.includes(error.reason ?? '');
  return ApplicationFailure.create({
    message: `Gateway activity failed (${error.statusCode}). Gateway request ID: ${error.requestId ?? 'unavailable'}`,
    type: error.reason ?? 'GatewayError', nonRetryable: !retryable,
    ...(retryable && error.retryAfter !== undefined ? { nextRetryDelay: error.retryAfter * 1000 } : {}),
  });
}

export class GatewayActivities {
  private readonly agents: Record<string, AgentHandler>;
  private readonly kubectl?: Kubectl;

  constructor(readonly baseUrl: string, private readonly apiKey: string, options: GatewayActivitiesOptions = {}) {
    this.agents = options.agents ?? {};
    this.kubectl = options.kubectl;
  }

  /** Run one gateway interaction of this activity, translating its failures for Temporal. */
  private async governed<T>(interaction: (signal: AbortSignal) => Promise<T>, onError?: (error: GatewayError) => T | undefined): Promise<T> {
    const context = Context.current();
    try {
      return await interaction(context.cancellationSignal);
    } catch (error) {
      if (context.cancellationSignal.aborted) return context.cancelled;
      if (error instanceof GatewayError) {
        const handled = onError?.(error);
        if (handled !== undefined) return handled;
      }
      const cause = governedCause(error);
      if (cause instanceof GatewayError) throw failure(cause);
      if (cause instanceof ApplicationFailure) throw cause;
      if (cause instanceof GatewayTransportError) throw ApplicationFailure.retryable(cause.message, 'GatewayUnavailable');
      throw error;
    }
  }

  private request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    return this.governed(signal => requestJson<T>(this.baseUrl, this.apiKey, method, path, body, headers, signal), (error) => {
      if (path.endsWith('/approval-waiting') && error.statusCode === 404) return {
        queued: false, policy_version: 1, approval_required: true, required_approvals: 1, approval_timeout_seconds: 604800,
      } as T;
      if (error.reason === 'trigger_paused') return { paused: true } as T;
      return undefined;
    });
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
    if (!['model', 'tool', 'agent', 'container'].includes(call.kind)) {
      throw ApplicationFailure.nonRetryable('Use WorkflowGateway.model, .tool, .agent or .container.', 'InvalidCall');
    }
    const agent = Object.hasOwn(this.agents, call.tool) ? this.agents[call.tool] : undefined;
    if (call.kind === 'agent' && !agent) {
      throw ApplicationFailure.nonRetryable(
        `Register agent "${call.tool}" in new GatewayActivities(url, key, { agents: { ... } }) or runWorker's agents option.`,
        'AgentNotRegistered');
    }
    await this.request('PUT', `/v1/workflow-runs/${runId}`, { ...call.budget, workflow: info.workflowType },
      { 'X-Workflow-ID': info.workflowExecution.workflowId });
    const headers = {
      'X-Workflow-Run-ID': runId, 'X-Workflow-Step-ID': info.activityId,
      'X-Data-Classification': call.data_classification,
    };
    if (call.kind === 'agent' && agent) {
      return this.governed(async (signal) => ({ result: await agent(new AgentContext(this.baseUrl, this.apiKey, headers, signal), call.payload) }));
    }
    if (call.kind === 'container') {
      return this.governed(signal => runContainer({
        baseUrl: this.baseUrl, apiKey: this.apiKey, agent: call.tool, payload: call.payload, headers, signal, kubectl: this.kubectl,
      }));
    }
    const path = call.kind === 'model' ? '/v1/chat/completions' : `/v1/tools/${encodeURIComponent(call.tool)}/call`;
    return this.request('POST', path, call.payload, headers);
  }

  async trigger(request: TriggerRequest): Promise<TriggerResult> {
    const body = request.run_id
      ? await this.request<Record<string, unknown>>('GET', `/v1/workflow-runs/${encodeURIComponent(request.run_id)}?timeline=false`)
      : await this.request<Record<string, unknown>>('POST',
        `/v1/workflow-triggers/${encodeURIComponent(request.workflow)}/${encodeURIComponent(request.trigger)}/fire`,
        { firing_id: request.firing_id });
    // Activity results live in the schedule's Temporal history: keep the run's content out of it.
    return Object.fromEntries(['paused', 'run_id', 'status', 'outcome', 'error']
      .filter(key => key in body).map(key => [key, body[key]])) as TriggerResult;
  }

  activities(): GovernedActivities {
    return { 'agentworkflows.call': this.call.bind(this), 'agentworkflows.trigger': this.trigger.bind(this) };
  }
}
