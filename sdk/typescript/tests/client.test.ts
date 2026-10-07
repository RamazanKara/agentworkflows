import { setTimeout } from 'node:timers/promises';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GatewayClient, GatewayError, GatewayRetryAfterError, GatewayTransportError } from '../src';

vi.mock('node:timers/promises', () => ({ setTimeout: vi.fn(async () => undefined) }));
const fetchMock = vi.fn<typeof fetch>();
const client = new GatewayClient('http://gateway.test/', { apiKey: 'fixture-key' });
const ok = (body = {}) => new Response(JSON.stringify(body), { status: 200 });
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); vi.mocked(setTimeout).mockClear(); });
afterEach(() => vi.unstubAllGlobals());

it('uses the workflow API with encoded paths, pagination, and authenticated approval', async () => {
  fetchMock.mockImplementation(async () => ok());
  await client.startRun('CodeReviewWorkflow', { diff: 'diff' }, { requestId: 'request-1', project: 'engineering' });
  await client.runs({ offset: 20, project: 'project & more' });
  await client.run('run/id');
  await client.approveRun('run/id', { approved: false });
  await client.cancelRun('run/id');
  await client.retryRun('run/id');
  await client.triggers();
  await client.pauseTrigger('daily/report', 'every day', { paused: false });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    'http://gateway.test/v1/workflow-runs',
    'http://gateway.test/v1/workflow-runs?offset=20&project=project+%26+more',
    'http://gateway.test/v1/workflow-runs/run%2Fid',
    'http://gateway.test/v1/workflow-runs/run%2Fid/approve',
    'http://gateway.test/v1/workflow-runs/run%2Fid/cancel',
    'http://gateway.test/v1/workflow-runs/run%2Fid/retry',
    'http://gateway.test/v1/workflow-triggers',
    'http://gateway.test/v1/workflow-triggers/daily%2Freport/every%20day',
  ]);
  expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
    workflow: 'CodeReviewWorkflow', input: { diff: 'diff' }, project: 'engineering', request_id: 'request-1',
  });
  expect(JSON.parse(fetchMock.mock.calls[3][1]?.body as string)).toEqual({ approved: false });
  expect(JSON.parse(fetchMock.mock.calls[7][1]?.body as string)).toEqual({ paused: false });
  for (const [, init] of fetchMock.mock.calls) {
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer fixture-key' });
    expect(init?.headers).not.toHaveProperty('X-Sandbox-ID');
    expect(init?.redirect).toBe('error');
  }
});

it('reuses the same request UUID and body after an ambiguous workflow start', async () => {
  fetchMock.mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(ok({ run_id: 'run' }));
  await expect(client.startRun('SupportTriageWorkflow', { ticket: 'hello' })).resolves.toEqual({ run_id: 'run' });
  const first = fetchMock.mock.calls[0][1]?.body as string;
  expect(JSON.parse(first).request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(fetchMock.mock.calls[1][1]?.body).toBe(first);
  expect(setTimeout).toHaveBeenCalledWith(250);
});

it.each(['approveRun', 'retryRun', 'cancelRun'] as const)('does not automatically repeat %s on an ambiguous error', async (method) => {
  fetchMock.mockResolvedValue(new Response('{}', { status: 502 }));
  await expect(client[method]('run')).rejects.toBeInstanceOf(GatewayError);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(setTimeout).not.toHaveBeenCalled();
});

it('honors Retry-After and bounds transient retries', async () => {
  fetchMock.mockImplementation(async () => new Response('{}', { status: 429, headers: { 'Retry-After': '2' } }));
  await expect(client.run('run')).rejects.toMatchObject({ statusCode: 429 });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(vi.mocked(setTimeout).mock.calls).toEqual([[2000], [2000]]);
});

it('fails fast on retry delays above the cap', async () => {
  fetchMock.mockResolvedValue(new Response('{"detail":{"reason":"budget_exceeded"}}', {
    status: 429, headers: { 'Retry-After': '3600', 'X-Request-ID': 'request-1' },
  }));
  const error = await client.runs().catch((error: unknown) => error);
  expect(error).toBeInstanceOf(GatewayRetryAfterError);
  expect(error).toMatchObject({ retryAfter: 3600, reason: 'budget_exceeded', requestId: 'request-1' });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(setTimeout).not.toHaveBeenCalled();
});

it.each(['soon', '-1', '1.5'])('uses exponential backoff for malformed Retry-After %s', async (delay) => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503, headers: { 'Retry-After': delay } }))
    .mockResolvedValueOnce(ok());
  await client.runs();
  expect(setTimeout).toHaveBeenCalledWith(250);
});

it('retains typed gateway details and request IDs without validation inputs', async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: [
    { loc: ['body', 'input'], msg: 'bad', input: 'private input' },
  ] }), { status: 422, headers: { 'X-Request-ID': 'req-1' } }));
  const error = await client.startRun('bad', {}).catch((error: unknown) => error);
  expect(error).toMatchObject({ statusCode: 422, reason: 'invalid_request', requestId: 'req-1' });
  expect(String(error)).toContain('body.input');
  expect(JSON.stringify(error)).not.toContain('private input');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('handles OpenAI and non-JSON errors, and redacts transport errors', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{"error":{"reason":"model_not_allowed","request_id":"req-2"}}', { status: 400 }));
  await expect(client.run('run')).rejects.toMatchObject({ reason: 'model_not_allowed', requestId: 'req-2' });
  fetchMock.mockResolvedValueOnce(new Response('<html>proxy error</html>', { status: 403 }));
  await expect(client.run('run')).rejects.toMatchObject({ statusCode: 403 });
  fetchMock.mockRejectedValueOnce(new Error('secret fixture-key'));
  await expect(new GatewayClient('http://gateway.test', { maxRetries: 0 }).run('run')).rejects.toBeInstanceOf(GatewayTransportError);
});
