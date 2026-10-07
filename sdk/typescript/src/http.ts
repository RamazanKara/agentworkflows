import { GatewayError, GatewayTransportError } from './errors';

function errorDetail(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== 'object') return {};
  for (const key of ['detail', 'error']) {
    const value = (body as Record<string, unknown>)[key];
    if (typeof value === 'string') return { message: value };
    if (Array.isArray(value)) {
      const fields = value.map((item: { loc?: unknown[] }) => item.loc?.join('.')).filter(Boolean);
      return { reason: 'invalid_request', message: `Invalid request fields: ${fields.join(', ')}. Check the API reference.` };
    }
    if (value && typeof value === 'object') return value as Record<string, unknown>;
  }
  return {};
}

export async function requestJson<T>(
  baseUrl: string, apiKey: string | undefined, method: string, path: string,
  body?: unknown, headers: Record<string, string> = {}, signal = AbortSignal.timeout(120_000),
): Promise<T> {
  let response: Response;
  let result: unknown;
  try {
    response = await fetch(`${baseUrl.replace(/\/$/, '')}${path}`, {
      method,
      headers: {
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'error',
      signal,
    });
    const text = await response.text();
    try {
      result = text ? JSON.parse(text) : {};
    } catch {
      if (response.ok) throw new GatewayTransportError();
      result = {};
    }
  } catch {
    // Transport errors can contain request URLs or credentials; keep them out of history.
    throw new GatewayTransportError();
  }
  if (!response.ok) throw new GatewayError(response, errorDetail(result));
  return result as T;
}
