import { setTimeout } from 'node:timers/promises';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GatewayClient, GatewayError, GatewayTimeoutError, GatewayTransportError } from '../src';

vi.mock('node:timers/promises', () => ({ setTimeout: vi.fn(async () => undefined) }));
const fetchMock = vi.fn<typeof fetch>();
const client = new GatewayClient('http://gateway.test', { apiKey: 'fixture-key' });
const ok = (body = {}) => new Response(JSON.stringify(body), { status: 200 });
const refused = () => new TypeError('fetch failed', {
  cause: Object.assign(new Error('connect ECONNREFUSED 10.0.0.7:8080 fixture-key'), { code: 'ECONNREFUSED' }),
});
const reset = () => new TypeError('fetch failed', { cause: Object.assign(new Error('socket hang up'), { code: 'ECONNRESET' }) });
/** A fetch that never answers until its signal aborts, like an unresponsive gateway. */
const hang: typeof fetch = async (_url, init) => new Promise((_resolve, reject) => {
  if (init?.signal?.aborted) reject(init.signal.reason);
  init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
});
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); vi.mocked(setTimeout).mockClear(); });
afterEach(() => vi.unstubAllGlobals());

it('retries a state-changing call only when the gateway provably did not act', async () => {
  fetchMock.mockRejectedValueOnce(refused()).mockResolvedValueOnce(new Response('{}', { status: 503 }))
    .mockResolvedValueOnce(new Response('{}', { status: 429, headers: { 'Retry-After': '1' } }));
  await expect(client.createKey('CI')).rejects.toMatchObject({ statusCode: 429 });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(vi.mocked(setTimeout).mock.calls).toEqual([[250], [500]]);
  const bodies = fetchMock.mock.calls.map(([, init]) => init?.body);
  expect(new Set(bodies).size).toBe(1);
});

it.each([
  ['approveRun', () => client.approveRun('run')],
  ['setProviderKey', () => client.setProviderKey('openai', 'value', 1)],
  ['installTemplate', () => client.installTemplate('research', '0.9.0')],
  ['pauseTrigger', () => client.pauseTrigger('Daily', 'daily')],
] as const)('retries %s after a refused connection and a load-shed 503', async (_name, write) => {
  fetchMock.mockRejectedValueOnce(refused()).mockResolvedValueOnce(new Response('{}', { status: 503 })).mockResolvedValueOnce(ok({ done: true }));
  await expect(write()).resolves.toEqual({ done: true });
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it.each([
  ['a reset connection', reset],
  ['a timeout', () => new DOMException('timed out', 'TimeoutError')],
])('does not repeat a key mutation after %s, but repeats a read', async (_name, failure) => {
  fetchMock.mockRejectedValueOnce(failure());
  await expect(client.revokeKey('key')).rejects.toBeInstanceOf(GatewayTransportError);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockRejectedValueOnce(failure()).mockResolvedValueOnce(ok({ keys: [] }));
  await expect(client.listKeys()).resolves.toEqual({ keys: [] });
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it('names the connection failure without leaking the address or credentials', async () => {
  fetchMock.mockRejectedValue(refused());
  const error = await new GatewayClient('http://gateway.test', { maxRetries: 0 }).run('run').catch((error: unknown) => error);
  expect(error).toBeInstanceOf(GatewayTransportError);
  expect((error as GatewayTransportError).code).toBe('ECONNREFUSED');
  expect((error as GatewayTransportError).cause).toBeUndefined();
  expect(String(error)).toContain('ECONNREFUSED');
  expect(String(error)).not.toContain('10.0.0.7');
  expect(String(error)).not.toContain('fixture-key');
});

it('times out each attempt and retries reads with a timeout error', async () => {
  fetchMock.mockImplementationOnce(hang).mockResolvedValueOnce(ok({ run_id: 'run' }));
  const fast = new GatewayClient('http://gateway.test', { timeoutMs: 20 });
  await expect(fast.run('run')).resolves.toEqual({ run_id: 'run' });
  fetchMock.mockImplementation(hang);
  const error = await fast.withOptions({ maxRetries: 0 }).run('run').catch((error: unknown) => error);
  expect(error).toBeInstanceOf(GatewayTimeoutError);
  expect(error).toBeInstanceOf(GatewayTransportError);
  expect(error).toMatchObject({ timeoutMs: 20, code: 'ETIMEDOUT' });
  await expect(fast.createKey('CI')).rejects.toBeInstanceOf(GatewayTimeoutError);
});

it('stops at once with the abort reason, without retrying or waiting', async () => {
  const controller = new AbortController();
  fetchMock.mockImplementation(async (url, init) => {
    controller.abort(new Error('caller stopped'));
    return hang(url, init);
  });
  await expect(client.withOptions({ signal: controller.signal }).runs()).rejects.toThrow('caller stopped');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(setTimeout).not.toHaveBeenCalled();
  await expect(client.withOptions({ signal: controller.signal }).runs()).rejects.toThrow('caller stopped');
});

it('passes the signal to retry waits', async () => {
  const controller = new AbortController();
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 })).mockResolvedValueOnce(ok());
  await client.withOptions({ signal: controller.signal }).runs();
  expect(setTimeout).toHaveBeenCalledWith(250, undefined, { signal: controller.signal });
});

it('applies backoff, retry count and Retry-After options', async () => {
  fetchMock.mockResolvedValue(new Response('{}', { status: 502 }));
  await expect(new GatewayClient('http://gateway.test', { retryBackoffMs: 100, maxRetries: 3 }).run('run'))
    .rejects.toBeInstanceOf(GatewayError);
  expect(vi.mocked(setTimeout).mock.calls).toEqual([[100], [200], [400]]);
  fetchMock.mockClear();
  vi.mocked(setTimeout).mockClear();
  await expect(new GatewayClient('http://gateway.test', { maxRetries: -1 }).run('run')).rejects.toBeInstanceOf(GatewayError);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 429, headers: { 'Retry-After': '45' } })).mockResolvedValueOnce(ok());
  await new GatewayClient('http://gateway.test', { retryAfterCap: 60 }).run('run');
  expect(setTimeout).toHaveBeenCalledWith(45_000);
});

it('sends the sandbox, default headers and API key, with the key always winning', async () => {
  fetchMock.mockImplementation(async () => ok());
  const scoped = new GatewayClient('http://gateway.test/', {
    apiKey: 'fixture-key', sandboxId: 'team-a', headers: { traceparent: '00-trace', Authorization: 'Bearer other' },
  });
  await scoped.usage();
  await scoped.withOptions({ sandboxId: undefined }).usage();
  expect(fetchMock.mock.calls[0][1]?.headers).toEqual({
    traceparent: '00-trace', 'X-Sandbox-ID': 'team-a', Authorization: 'Bearer fixture-key',
  });
  expect(fetchMock.mock.calls[1][1]?.headers).not.toHaveProperty('X-Sandbox-ID');
  expect(fetchMock.mock.calls[0][0]).toBe('http://gateway.test/v1/usage');
});

it('uses a supplied fetch instead of the global one', async () => {
  const custom = vi.fn<typeof fetch>(async () => ok({ data: [] }));
  await expect(new GatewayClient('http://gateway.test', { fetch: custom }).models()).resolves.toEqual({ data: [] });
  expect(custom).toHaveBeenCalledTimes(1);
  expect(fetchMock).not.toHaveBeenCalled();
});

it.each(['gateway.test', '/relative', 'ftp://gateway.test'])('rejects the unusable gateway URL %s up front', (url) => {
  expect(() => new GatewayClient(url)).toThrow(TypeError);
});

it('explains a successful response that is not JSON', async () => {
  fetchMock.mockResolvedValue(new Response('<html>login</html>', { status: 200 }));
  await expect(new GatewayClient('http://gateway.test', { maxRetries: 0 }).teamSettings())
    .rejects.toThrow('not JSON; check that AGENTWORKFLOWS_URL points at the gateway');
});

it('keeps a lost error body from hiding the status', async () => {
  fetchMock.mockResolvedValue(new Response(new ReadableStream({ pull(controller) { controller.error(new Error('lost')); } }), { status: 403 }));
  await expect(client.teamSettings()).rejects.toMatchObject({ statusCode: 403, detail: {} });
});
