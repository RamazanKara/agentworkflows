import { GatewayError, GatewayTimeoutError, GatewayTransportError } from './errors';

export const DEFAULT_TIMEOUT_MS = 120_000;

export function errorDetail(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== 'object') return {};
  for (const key of ['detail', 'error']) {
    const value = (body as Record<string, unknown>)[key];
    if (typeof value === 'string') return { message: value };
    if (Array.isArray(value)) {
      const fields = value.map((item: { loc?: unknown[] }) => item?.loc?.join('.')).filter(Boolean);
      return { reason: 'invalid_request', message: `Invalid request fields: ${fields.join(', ')}. Check the API reference.` };
    }
    if (value && typeof value === 'object') return value as Record<string, unknown>;
  }
  return {};
}

/** Read an error response and build the GatewayError, keeping its body out of the message unless it is a detail. */
export async function gatewayError(response: Response): Promise<GatewayError> {
  let body: unknown;
  try {
    body = JSON.parse(await response.text());
  } catch {
    body = undefined;
  }
  return new GatewayError(response, errorDetail(body));
}

/** The system error code behind a failed fetch, without its message (which can contain the URL). */
export function transportCode(error: unknown): string | undefined {
  for (let cause = error, depth = 0; cause && typeof cause === 'object' && depth < 4; depth++) {
    const code = (cause as { code?: unknown }).code;
    if (typeof code === 'string' && /^[A-Z][A-Z0-9_]{1,40}$/.test(code)) return code;
    const errors = (cause as { errors?: unknown }).errors;
    if (Array.isArray(errors) && errors.length) cause = errors[0];
    else cause = (cause as { cause?: unknown }).cause;
  }
  return undefined;
}

export type ResponseKind = 'json' | 'text' | 'bytes' | 'response';

export interface HttpRequest {
  method: string;
  path: string;
  /** JSON-encoded unless it is FormData. */
  body?: unknown;
  headers?: Record<string, string>;
  /** How to read a successful response; `response` returns it unread for streaming. */
  read?: ResponseKind;
  signal?: AbortSignal;
  timeoutMs?: number;
  fetch?: typeof fetch;
}

/**
 * Send one request. Gateway errors throw GatewayError; timeouts throw GatewayTimeoutError; other failures
 * throw GatewayTransportError without the original message, which can contain URLs or credentials. When
 * `signal` aborts, its reason is thrown unchanged. The timeout covers the whole exchange, except that a
 * `response` read stops the clock once headers arrive so a stream can outlive it.
 */
export async function send<T>(baseUrl: string, apiKey: string | undefined, request: HttpRequest): Promise<T> {
  const timeoutMs = request.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(), timeoutMs);
  timer.unref?.();
  const signal = request.signal ? AbortSignal.any([request.signal, deadline.signal]) : deadline.signal;
  const form = typeof FormData !== 'undefined' && request.body instanceof FormData;
  try {
    const response = await (request.fetch ?? fetch)(`${baseUrl.replace(/\/+$/, '')}${request.path}`, {
      method: request.method,
      headers: {
        ...(request.body === undefined || form ? {} : { 'Content-Type': 'application/json' }),
        ...request.headers,
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      body: request.body === undefined ? undefined : form ? request.body as FormData : JSON.stringify(request.body),
      redirect: 'error',
      signal,
    });
    if (!response.ok) throw await gatewayError(response);
    switch (request.read ?? 'json') {
      case 'response': return response as T;
      case 'text': return await response.text() as T;
      case 'bytes': return new Uint8Array(await response.arrayBuffer()) as T;
    }
    const text = await response.text();
    try {
      return (text ? JSON.parse(text) : {}) as T;
    } catch {
      throw new GatewayTransportError(undefined,
        'The gateway returned a response that is not JSON; check that AGENTWORKFLOWS_URL points at the gateway.');
    }
  } catch (error) {
    if (request.signal?.aborted) throw request.signal.reason;
    if (error instanceof GatewayError || error instanceof GatewayTransportError) throw error;
    if (deadline.signal.aborted) throw new GatewayTimeoutError(timeoutMs);
    // Transport errors can contain request URLs or credentials; keep them out of history.
    throw new GatewayTransportError(transportCode(error));
  } finally {
    clearTimeout(timer);
  }
}

/** One JSON request with no retries; Temporal activities leave retry scheduling to Temporal. */
export function requestJson<T>(
  baseUrl: string, apiKey: string | undefined, method: string, path: string,
  body?: unknown, headers: Record<string, string> = {}, signal?: AbortSignal,
): Promise<T> {
  return send<T>(baseUrl, apiKey, { method, path, body, headers, signal });
}
