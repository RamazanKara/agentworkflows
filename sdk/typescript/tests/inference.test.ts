import { setTimeout } from 'node:timers/promises';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GatewayClient, GatewayError, GatewayStreamError, GatewayTimeoutError, GatewayTransportError } from '../src';
import { withInputSchema } from '../src/workflows';

vi.mock('node:timers/promises', () => ({ setTimeout: vi.fn(async () => undefined) }));
const fetchMock = vi.fn<typeof fetch>();
const client = new GatewayClient('http://gateway.test', { apiKey: 'fixture-key' });
const ok = (body = {}) => new Response(JSON.stringify(body), { status: 200 });
const sent = (index = 0) => JSON.parse(String(fetchMock.mock.calls[index][1]?.body));
const route = (index = 0) => [fetchMock.mock.calls[index][1]?.method, String(fetchMock.mock.calls[index][0])];
/** A streamed body delivered in the given chunks, as a slow network would split it. */
const stream = (chunks: (string | Uint8Array)[], end = true) => new Response(new ReadableStream<Uint8Array>({
  start(controller) {
    for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? new TextEncoder().encode(chunk) : chunk);
    if (end) controller.close();
  },
}), { headers: { 'Content-Type': 'text/event-stream' } });
const collect = async (lines: AsyncIterable<string>) => {
  const out: string[] = [];
  for await (const line of lines) out.push(line);
  return out;
};
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); vi.mocked(setTimeout).mockClear(); });
afterEach(() => vi.unstubAllGlobals());

it('sends chat, completions, embeddings, moderations, batch inference and messages with pass-through fields', async () => {
  fetchMock.mockImplementation(async () => ok({ ok: true }));
  const messages = [{ role: 'user', content: 'hi' }];
  await client.chat(messages, { model: 'demo-openai', max_tokens: 8, temperature: 0 });
  await client.chat(messages);
  await client.completions('Once upon a time', { max_tokens: 8 });
  await client.embeddings(['a', 'b'], { model: 'embed' });
  await client.moderations('text');
  await client.batch([{ messages }]);
  await client.messages(messages, 64, { system: 'be brief', model: 'demo-anthropic' });
  expect(fetchMock.mock.calls.map((_call, index) => route(index))).toEqual([
    ['POST', 'http://gateway.test/v1/chat/completions'], ['POST', 'http://gateway.test/v1/chat/completions'],
    ['POST', 'http://gateway.test/v1/completions'], ['POST', 'http://gateway.test/v1/embeddings'],
    ['POST', 'http://gateway.test/v1/moderations'], ['POST', 'http://gateway.test/v1/batch-inference'],
    ['POST', 'http://gateway.test/v1/messages'],
  ]);
  expect(fetchMock.mock.calls.map((_call, index) => sent(index))).toEqual([
    { messages, model: 'demo-openai', max_tokens: 8, temperature: 0 }, { messages },
    { prompt: 'Once upon a time', max_tokens: 8 }, { input: ['a', 'b'], model: 'embed' }, { input: 'text' },
    { requests: [{ messages }] }, { messages, max_tokens: 64, system: 'be brief', model: 'demo-anthropic' },
  ]);
  expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ Authorization: 'Bearer fixture-key', 'Content-Type': 'application/json' });
});

it('retries inference after transport errors, as the Python client does', async () => {
  fetchMock.mockRejectedValueOnce(new TypeError('fetch failed')).mockResolvedValueOnce(ok({ choices: [] }));
  await expect(client.chat([{ role: 'user', content: 'hi' }])).resolves.toEqual({ choices: [] });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('carries the gateway reason and request ID on inference errors', async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({
    detail: { message: 'model is not approved', reason: 'model_not_allowed', request_id: 'req-1' },
  }), { status: 400 }));
  const error = await client.chat([{ role: 'user', content: 'hi' }], { model: 'x' }).catch((error: unknown) => error);
  expect(error).toBeInstanceOf(GatewayError);
  expect(error).toMatchObject({ statusCode: 400, reason: 'model_not_allowed', requestId: 'req-1' });
  expect(String(error)).toContain('model_not_allowed');
});

it('streams content deltas across arbitrary chunk boundaries and stops at [DONE]', async () => {
  const euro = new TextEncoder().encode('data: {"choices":[{"delta":{"content":"€"}}]}\n\n');
  fetchMock.mockResolvedValueOnce(stream([
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\r\n\r\n', 'data: {"choices":[{"del', 'ta":{"content":"lo"}}]}\n\n',
    ': keep-alive\n\n', 'data: {"choices":[{"delta":{}}]}\n\n', 'data: not json\n\n', 'data: []\n\n',
    euro.slice(0, 40), euro.slice(40),
    'data: [DONE]\n\n', 'data: {"error":{"message":"after completion"}}\n\n',
  ]));
  const messages = [{ role: 'user', content: 'hi' }];
  expect(await collect(client.chatStream(messages, { model: 'demo-openai', stream: false }))).toEqual(['Hel', 'lo', '€']);
  expect(sent()).toEqual({ messages, model: 'demo-openai', stream: true });
});

it('reads a final event without a trailing newline', async () => {
  fetchMock.mockResolvedValueOnce(stream(['data: {"choices":[{"delta":{"content":"end"}}]}']));
  expect(await collect(client.chatStream([]))).toEqual(['end']);
});

it('raises a terminal stream error event instead of returning a truncated answer', async () => {
  fetchMock.mockResolvedValueOnce(stream([
    'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
    'data: {"error":{"message":"runtime unavailable","reason":"upstream_error"}}\n\n',
  ]));
  const lines = client.chatStream([{ role: 'user', content: 'hi' }]);
  expect((await lines.next()).value).toBe('partial');
  const error = await lines.next().catch((error: unknown) => error);
  expect(error).toBeInstanceOf(GatewayStreamError);
  expect(error).toMatchObject({ message: 'runtime unavailable', reason: 'upstream_error', error: { reason: 'upstream_error' } });
});

it('raises the gateway reason for a refused stream and never retries it', async () => {
  fetchMock.mockResolvedValue(new Response('{"detail":{"message":"streaming disabled","reason":"streaming_disabled"}}', { status: 503 }));
  await expect(collect(client.chatStream([]))).rejects.toMatchObject({ statusCode: 503, reason: 'streaming_disabled' });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockRejectedValueOnce(new TypeError('fetch failed'));
  await expect(collect(client.chatStream([]))).rejects.toBeInstanceOf(GatewayTransportError);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('times out a stream that stops sending and cancels its body', async () => {
  let canceled = false;
  fetchMock.mockResolvedValueOnce(new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"a"}}]}\n\n')); },
    cancel() { canceled = true; },
  })));
  const lines = new GatewayClient('http://gateway.test', { timeoutMs: 20 }).chatStream([]);
  expect((await lines.next()).value).toBe('a');
  await expect(lines.next()).rejects.toBeInstanceOf(GatewayTimeoutError);
  expect(canceled).toBe(true);
});

it('stops a stream when the caller breaks out early', async () => {
  let canceled = false;
  fetchMock.mockResolvedValueOnce(new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"a"}}]}\n\ndata: {"choices":[{"delta":{"content":"b"}}]}\n\n'));
    },
    cancel() { canceled = true; },
  })));
  for await (const text of client.chatStream([])) {
    expect(text).toBe('a');
    break;
  }
  expect(canceled).toBe(true);
});

it('aborts a stream with the caller signal', async () => {
  const controller = new AbortController();
  fetchMock.mockImplementationOnce(async (_url, init) => new Response(new ReadableStream({
    start(stream) {
      stream.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"a"}}]}\n\n'));
      init?.signal?.addEventListener('abort', () => stream.error(init.signal?.reason));
    },
  })));
  const lines = client.withOptions({ signal: controller.signal }).chatStream([]);
  expect((await lines.next()).value).toBe('a');
  controller.abort(new Error('stop streaming'));
  await expect(lines.next()).rejects.toThrow('stop streaming');
});

it('uploads batch files as multipart and manages files and batches', async () => {
  fetchMock.mockImplementation(async (url) => String(url).endsWith('/content')
    ? new Response(new Uint8Array([123, 10]), { status: 200 }) : ok({ id: 'object-1' }));
  await expect(client.uploadBatchFile('{"custom_id":"a"}\n')).resolves.toEqual({ id: 'object-1' });
  await client.uploadBatchFile(new Uint8Array([1, 2]), 'input.jsonl');
  await client.getFile('file/1');
  await expect(client.getFileContent('file-out')).resolves.toEqual(new Uint8Array([123, 10]));
  await client.listFiles();
  await client.deleteFile('file-1');
  await client.createBatch('file-1');
  await client.createBatch('file-1', { endpoint: '/v1/embeddings', completionWindow: '1h', metadata: { k: 'v' } });
  await client.getBatch('batch-1');
  await client.cancelBatch('batch-1');
  await client.listBatches();
  await client.listBatches({ limit: 5, after: 'batch-0' });
  expect(fetchMock.mock.calls.map((_call, index) => route(index))).toEqual([
    ['POST', 'http://gateway.test/v1/files'], ['POST', 'http://gateway.test/v1/files'],
    ['GET', 'http://gateway.test/v1/files/file%2F1'], ['GET', 'http://gateway.test/v1/files/file-out/content'],
    ['GET', 'http://gateway.test/v1/files'], ['DELETE', 'http://gateway.test/v1/files/file-1'],
    ['POST', 'http://gateway.test/v1/batches'], ['POST', 'http://gateway.test/v1/batches'],
    ['GET', 'http://gateway.test/v1/batches/batch-1'], ['POST', 'http://gateway.test/v1/batches/batch-1/cancel'],
    ['GET', 'http://gateway.test/v1/batches?limit=20'], ['GET', 'http://gateway.test/v1/batches?limit=5&after=batch-0'],
  ]);
  const form = fetchMock.mock.calls[0][1]?.body as FormData;
  expect(form).toBeInstanceOf(FormData);
  expect(form.get('purpose')).toBe('batch');
  const file = form.get('file') as File;
  expect([file.name, file.type, await file.text()]).toEqual(['batch.jsonl', 'application/jsonl', '{"custom_id":"a"}\n']);
  expect((fetchMock.mock.calls[1][1]?.body as FormData).get('file')).toMatchObject({ name: 'input.jsonl', size: 2 });
  expect(fetchMock.mock.calls[0][1]?.headers).not.toHaveProperty('Content-Type');
  expect(sent(6)).toEqual({ input_file_id: 'file-1', endpoint: '/v1/chat/completions', completion_window: '24h' });
  expect(sent(7)).toEqual({ input_file_id: 'file-1', endpoint: '/v1/embeddings', completion_window: '1h', metadata: { k: 'v' } });
});

it.each([
  ['uploadBatchFile', () => client.uploadBatchFile('x')],
  ['createBatch', () => client.createBatch('file-1')],
  ['stored createResponse', () => client.createResponse('hi', { store: true })],
  ['recordReceipt', () => client.recordReceipt('tool_exec', 'allowed')],
] as const)('does not repeat %s after an ambiguous 502, but does after a refused connection', async (_name, create) => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 502 }));
  await expect(create()).rejects.toMatchObject({ statusCode: 502 });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockRejectedValueOnce(new TypeError('fetch failed', { cause: { code: 'ENOTFOUND' } })).mockResolvedValueOnce(ok({ id: 'x' }));
  await expect(create()).resolves.toEqual({ id: 'x' });
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it('creates, chains, reads and deletes Responses API responses', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 502 })).mockImplementation(async () => ok({ id: 'resp_1' }));
  await client.createResponse('hi', { model: 'demo-openai' });
  await client.createResponse([{ role: 'user', content: 'again' }], { store: true, previous_response_id: 'resp_0' });
  await client.getResponse('resp_1');
  await client.responseInputItems('resp_1');
  await client.deleteResponse('resp_1');
  expect(fetchMock.mock.calls.map((_call, index) => route(index))).toEqual([
    ['POST', 'http://gateway.test/v1/responses'], ['POST', 'http://gateway.test/v1/responses'],
    ['POST', 'http://gateway.test/v1/responses'], ['GET', 'http://gateway.test/v1/responses/resp_1'],
    ['GET', 'http://gateway.test/v1/responses/resp_1/input_items'], ['DELETE', 'http://gateway.test/v1/responses/resp_1'],
  ]);
  expect(sent(1)).toEqual({ input: 'hi', model: 'demo-openai' });
  expect(sent(2)).toEqual({ input: [{ role: 'user', content: 'again' }], store: true, previous_response_id: 'resp_0' });
});

it('records agent-action receipts with optional attributes', async () => {
  fetchMock.mockImplementation(async () => ok({ receipt_id: 'r' }));
  await client.recordReceipt('egress_denied', 'denied', { target: 'example.com:443' });
  expect(route()).toEqual(['POST', 'http://gateway.test/v1/receipts']);
  expect(sent()).toEqual({ action_type: 'egress_denied', decision: 'denied', target: 'example.com:443' });
});

it('discovers the team, usage, sandbox budget and readiness', async () => {
  fetchMock.mockResolvedValueOnce(ok({ team_id: 'demo', role: 'viewer' })).mockResolvedValueOnce(ok({ sandbox_id: 'demo' }))
    .mockResolvedValueOnce(ok({ usage: { estimated_tokens: 7 } })).mockResolvedValueOnce(ok({ status: 'ready' }))
    .mockResolvedValueOnce(new Response('{"status":"degraded"}', { status: 503 }))
    .mockRejectedValueOnce(new TypeError('fetch failed'));
  await expect(client.team()).resolves.toMatchObject({ team_id: 'demo', role: 'viewer' });
  await expect(client.usage()).resolves.toEqual({ sandbox_id: 'demo' });
  await expect(client.sandboxBudget()).resolves.toEqual({ usage: { estimated_tokens: 7 } });
  await expect(client.ready()).resolves.toBe(true);
  await expect(client.ready()).resolves.toBe(false);
  await expect(client.ready()).resolves.toBe(false);
  expect(fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)).toEqual([
    '/v1/team', '/v1/usage', '/v1/sandbox/budget', '/readyz', '/readyz', '/readyz',
  ]);
  expect(setTimeout).not.toHaveBeenCalled();
  const controller = new AbortController();
  controller.abort(new Error('stopped'));
  await expect(client.withOptions({ signal: controller.signal }).ready()).rejects.toThrow('stopped');
});

it('registers a declared workflow function by name with its input schema', async () => {
  const schema = { type: 'object' as const, properties: { topic: { type: 'string' as const } }, required: ['topic'] };
  const Research = withInputSchema(schema, async function ResearchWorkflow(input: { topic: string }) { return input.topic; });
  fetchMock.mockImplementation(async () => ok({ revision: 2 }));
  await client.registerWorkflow(Research, ['primary'], {}, 1);
  await client.registerWorkflow(Research, ['primary'], { input_schema: null }, 2);
  await client.registerWorkflow(async function Plain() { return 1; }, ['primary'], {}, 3);
  await client.removeWorkflow(Research, 4);
  expect(fetchMock.mock.calls.map((_call, index) => route(index))).toEqual([
    ['PUT', 'http://gateway.test/v1/team/workflows/ResearchWorkflow'], ['PUT', 'http://gateway.test/v1/team/workflows/ResearchWorkflow'],
    ['PUT', 'http://gateway.test/v1/team/workflows/Plain'], ['DELETE', 'http://gateway.test/v1/team/workflows/ResearchWorkflow'],
  ]);
  expect(sent(0)).toEqual({ allowed_models: ['primary'], input_schema: schema });
  expect(sent(1)).toEqual({ allowed_models: ['primary'], input_schema: null });
  expect(sent(2)).toEqual({ allowed_models: ['primary'] });
  await expect(client.registerWorkflow((() => async () => 1)(), ['primary'])).rejects.toThrow(TypeError);
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

it('types trigger listings and pause state', async () => {
  const listing = { triggers: [{ workflow: 'Daily', name: 'daily', kind: 'cron', project: null, cron: '0 9 * * *', paused: false, configuration_paused: false }] };
  fetchMock.mockResolvedValueOnce(ok(listing)).mockResolvedValueOnce(ok({ workflow: 'Daily', name: 'daily', paused: true }));
  const { triggers } = await client.triggers();
  expect(triggers[0].kind).toBe('cron');
  expect((await client.pauseTrigger('Daily', 'daily')).paused).toBe(true);
  expect(fetchMock.mock.calls[1][1]?.method).toBe('PATCH');
  expect(GatewayError).toBeDefined();
});
