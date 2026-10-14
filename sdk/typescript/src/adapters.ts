import { ApplicationFailure } from '@temporalio/common';
import { GatewayTransportError } from './errors';
import { gatewayError, transportCode } from './http';

/** Options accepted by the OpenAI SDK (`new OpenAI(...)`), the Vercel AI SDK's `createOpenAI` and similar clients. */
export interface OpenAIClientOptions {
  baseURL: string;
  apiKey: string;
  fetch: typeof fetch;
  maxRetries: 0;
}

/** Options accepted by the Anthropic SDK (`new Anthropic(...)`). */
export type AnthropicClientOptions = OpenAIClientOptions;

/**
 * The governed gateway context of one agent step. Framework clients built from `openaiOptions()` or
 * `anthropicOptions()`, and requests through `fetch`, carry the step's run, step and classification headers: each
 * request gets its own receipt (`<step>/<n>`), the worker key stays in the worker, and only the gateway is reachable.
 * A gateway error response throws GatewayError, so policy and budget denials fail the step instead of being retried
 * inside the framework.
 */
export class AgentContext {
  /** Requests sent so far; the next request's step ID ends in `/<sequence + 1>`. */
  sequence = 0;
  private readonly gateway: URL;

  constructor(
    readonly baseUrl: string,
    private readonly apiKey: string,
    readonly headers: Readonly<Record<string, string>>,
    private readonly signal?: AbortSignal,
  ) {
    this.gateway = new URL(baseUrl.replace(/\/*$/, '/'));
  }

  /** A fetch that sends governed requests to the gateway and refuses any other destination. */
  readonly fetch: typeof fetch = async (input, init) => {
    // Relative paths resolve against the gateway URL, so `context.fetch('v1/models')` works.
    const url = new URL(input instanceof Request ? input.url : String(input), this.gateway);
    if (url.origin !== this.gateway.origin || !url.pathname.startsWith(this.gateway.pathname)) {
      throw ApplicationFailure.nonRetryable(
        'Agent steps may only call the gateway; use its approved tools for other services.', 'AgentEgressDenied');
    }
    this.sequence += 1;
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
    for (const [key, value] of Object.entries(this.headers)) headers.set(key, value);
    headers.set('Authorization', `Bearer ${this.apiKey}`);
    headers.set('X-Workflow-Step-ID', `${this.headers['X-Workflow-Step-ID']}/${this.sequence}`);
    const signals = [this.signal, init?.signal ?? (input instanceof Request ? input.signal : undefined)]
      .filter((signal): signal is AbortSignal => Boolean(signal));
    const signal = signals.length ? AbortSignal.any(signals) : undefined;
    let response: Response;
    try {
      response = await fetch(input instanceof Request ? input : url, { ...init, headers, redirect: 'error', signal });
    } catch (error) {
      // Let the framework see its own timeouts and cancellation; redact other transport errors.
      if (signal?.aborted) throw error;
      throw new GatewayTransportError(transportCode(error));
    }
    if (!response.ok) throw await gatewayError(response);
    return response;
  };

  /** `new OpenAI(context.openaiOptions())`: chat completions and the OpenAI Agents SDK through the gateway. */
  openaiOptions(): OpenAIClientOptions {
    return { baseURL: new URL('v1', this.gateway).href, apiKey: this.apiKey, fetch: this.fetch, maxRetries: 0 };
  }

  /** `new Anthropic(context.anthropicOptions())`: the Messages API through the gateway. */
  anthropicOptions(): AnthropicClientOptions {
    return { baseURL: this.gateway.href.replace(/\/$/, ''), apiKey: this.apiKey, fetch: this.fetch, maxRetries: 0 };
  }

  /** Call an approved HTTP/MCP tool and return its result. */
  async tool<T = unknown>(name: string, args: Record<string, unknown>): Promise<T> {
    const response = await this.fetch(new URL(`v1/tools/${encodeURIComponent(name)}/call`, this.gateway), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ arguments: args }),
    });
    return (await response.json() as { result: T }).result;
  }
}
