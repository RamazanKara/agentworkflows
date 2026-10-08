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

it('lists, creates, updates and revokes managed keys without filling omitted fields', async () => {
  fetchMock.mockResolvedValueOnce(ok({ keys: [{ key_id: 'id', revoked_at: 10 }] }))
    .mockResolvedValueOnce(ok({ key_id: 'id', key: 'one-time-key' }))
    .mockImplementation(async () => ok({ key_id: 'id' }));
  await expect(client.listKeys()).resolves.toEqual({ keys: [{ key_id: 'id', revoked_at: 10 }] });
  await expect(client.createKey('CI')).resolves.toEqual({ key_id: 'id', key: 'one-time-key' });
  await client.createKey('Builder', { role: 'builder', project: 'demo', expires_at: '2030-01-01T00:00:00Z' });
  await client.updateKey('key/id', { name: 'Renamed' });
  await client.updateKey('key/id', { project: null, expires_at: null });
  await client.revokeKey('key/id');
  expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
    ['http://gateway.test/v1/team/keys', 'GET', undefined],
    ['http://gateway.test/v1/team/keys', 'POST', JSON.stringify({ name: 'CI' })],
    ['http://gateway.test/v1/team/keys', 'POST', JSON.stringify({
      name: 'Builder', role: 'builder', project: 'demo', expires_at: '2030-01-01T00:00:00Z',
    })],
    ['http://gateway.test/v1/team/keys/key%2Fid', 'PATCH', JSON.stringify({ name: 'Renamed' })],
    ['http://gateway.test/v1/team/keys/key%2Fid', 'PATCH', JSON.stringify({ project: null, expires_at: null })],
    ['http://gateway.test/v1/team/keys/key%2Fid', 'DELETE', undefined],
  ]);
  for (const [, init] of fetchMock.mock.calls) expect(init?.headers).toMatchObject({ Authorization: 'Bearer fixture-key' });
});

it('reads settings and sends explicit revisions including zero and quoted ETags on writes', async () => {
  const settings = {
    revision: 0, updated_by: null, updated_at: null,
    fields: { cost_limit_usd: { value: null, source: 'policy', policy_default: null } },
    routes: ['openai'], providers: ['openai'], approver_roles: ['admin', 'approver'],
  };
  fetchMock.mockImplementation(async () => ok(settings));
  await expect(client.teamSettings()).resolves.toEqual(settings);
  await expect(client.updateTeamSettings({ cost_limit_usd: 0, 'workflows.Report.approval_required': false },
    { revision: 0 })).resolves.toEqual(settings);
  await expect(client.resetTeamSetting('model_routes.team/a & b', { revision: '"1"' })).resolves.toEqual(settings);
  expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
    ['http://gateway.test/v1/team/settings', 'GET'],
    ['http://gateway.test/v1/team/settings', 'PATCH'],
    ['http://gateway.test/v1/team/settings/model_routes.team%2Fa%20%26%20b', 'DELETE'],
  ]);
  expect(fetchMock.mock.calls[0][1]?.headers).not.toHaveProperty('If-Match');
  expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ 'If-Match': '0', Authorization: 'Bearer fixture-key' });
  expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
    fields: { cost_limit_usd: 0, 'workflows.Report.approval_required': false },
  });
  expect(fetchMock.mock.calls[2][1]?.headers).toMatchObject({ 'If-Match': '"1"' });
  expect(fetchMock.mock.calls[2][1]?.body).toBeUndefined();
});

it.each([409, 422])('retains settings error details for %s without retrying either write', async (status) => {
  const detail = {
    reason: status === 409 ? 'team_settings_conflict' : 'team_settings_invalid',
    fields: [{ field: 'cost_limit_usd', message: 'Use a finite USD amount.' }],
  };
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ detail }), {
    status, headers: { 'X-Request-ID': 'settings-req' },
  }));
  for (const write of [
    () => client.updateTeamSettings({ cost_limit_usd: -1 }, { revision: 0 }),
    () => client.resetTeamSetting('cost_limit_usd', { revision: 0 }),
  ]) {
    await expect(write()).rejects.toMatchObject({ statusCode: status, requestId: 'settings-req', detail });
  }
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(setTimeout).not.toHaveBeenCalled();
});

it.each([
  ['createKey', () => client.createKey('CI')],
  ['updateKey', () => client.updateKey('id', { name: 'CI' })],
  ['revokeKey', () => client.revokeKey('id')],
  ['updateTeamSettings', () => client.updateTeamSettings({ cost_limit_usd: 5 }, { revision: 0 })],
  ['resetTeamSetting', () => client.resetTeamSetting('cost_limit_usd', { revision: 0 })],
] as const)('does not repeat %s on ambiguous HTTP or transport failures', async (_name, write) => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 502 }));
  await expect(write()).rejects.toBeInstanceOf(GatewayError);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockRejectedValueOnce(new TypeError('response lost'));
  await expect(write()).rejects.toBeInstanceOf(GatewayTransportError);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(setTimeout).not.toHaveBeenCalled();
});

it('encodes audit filters and cursor without losing zero timestamps or envelope metadata', async () => {
  const entry = {
    id: '5-0', chain_id: 'chain', sequence: 9, team_sequence: 2, record_hash: 'record',
    view_prev_hash: 'previous', view_hash: 'current', event: { event: 'team_key' },
  };
  const page = { enabled: true, events: [entry], next_cursor: '5-0' };
  fetchMock.mockResolvedValueOnce(ok(page));
  await expect(client.audit({ from: 0, to: 100.5, eventType: 'team_key', actor: 'Ada & Bob', project: 'a/b',
    runId: 'run/id', cursor: '9-0', limit: 200 })).resolves.toEqual(page);
  const [url, init] = fetchMock.mock.calls[0];
  expect(Object.fromEntries(new URL(String(url)).searchParams)).toEqual({
    from: '0', to: '100.5', event_type: 'team_key', actor: 'Ada & Bob', project: 'a/b',
    run_id: 'run/id', cursor: '9-0', limit: '200',
  });
  expect(init?.headers).toMatchObject({ Authorization: 'Bearer fixture-key' });
  expect(init?.method).toBe('GET');
});

it.each([true, false, null])('preserves verification results, boundaries and disabled state (ok=%s)', async (valid) => {
  const result = {
    enabled: valid !== null, ok: valid, checked: valid === null ? 0 : 2,
    first_break: valid === false ? { chain_id: 'chain', sequence: 3, reason: 'record_hash_mismatch' } : null,
    boundaries: [{ chain_id: 'chain', sequence: 2, reason: 'time_range' }],
  };
  fetchMock.mockResolvedValueOnce(ok(result));
  await expect(client.verifyAudit({ from: 0, to: 100 })).resolves.toEqual(result);
  expect(fetchMock.mock.calls[0][0]).toBe('http://gateway.test/v1/team/audit/verify?from=0&to=100');
});

it.each([undefined, 0, 100.5])('exports original JSON Lines across pages with a fixed upper bound (%s)', async (end) => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(200_500);
  const events = [{ event: 'team_key', name: 'Grüße\nCI' }, { event: 'team_key', name: 'older' }];
  fetchMock.mockResolvedValueOnce(ok({ enabled: true, events: [{ event: events[0] }], next_cursor: '5-0' }))
    .mockResolvedValueOnce(ok({ enabled: true, events: [{ event: events[1] }], next_cursor: null }));
  const options = { from: 0, to: end, eventType: 'team_key', actor: 'Ada & Bob', project: 'demo',
    runId: 'run', cursor: '9-0', limit: 1 };
  const lines: string[] = [];
  try {
    for await (const line of client.exportAudit(options)) {
      lines.push(line);
      now.mockReturnValue(300_500);
    }
  } finally {
    now.mockRestore();
  }
  expect(lines.map(line => JSON.parse(line))).toEqual(events);
  expect(lines.every(line => line.endsWith('\n') && line.split('\n').length === 2)).toBe(true);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  for (const [index, [url]] of fetchMock.mock.calls.entries()) {
    expect(Object.fromEntries(new URL(String(url)).searchParams)).toEqual({
      from: '0', to: String(end ?? 200.5), event_type: 'team_key', actor: 'Ada & Bob', project: 'demo',
      run_id: 'run', cursor: index === 0 ? '9-0' : '5-0', limit: '1',
    });
  }
  expect(options.cursor).toBe('9-0');
});

it('omits unspecified audit filters and ends an empty export', async () => {
  fetchMock.mockImplementation(async () => ok({ enabled: true, events: [], next_cursor: null }));
  await client.audit();
  await client.verifyAudit();
  await expect(client.exportAudit({ to: 0 }).next()).resolves.toEqual({ done: true, value: undefined });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    'http://gateway.test/v1/team/audit', 'http://gateway.test/v1/team/audit/verify', 'http://gateway.test/v1/team/audit?to=0',
  ]);
});

it('rejects export when the view is disabled', async () => {
  fetchMock.mockResolvedValueOnce(ok({ enabled: false, message: 'Enable Redis.' }));
  await expect(client.exportAudit().next()).rejects.toThrow('Enable Redis.');
});

it('propagates later page failures instead of treating partial exports as complete', async () => {
  fetchMock.mockResolvedValueOnce(ok({ enabled: true, events: [{ event: { event: 'team_key' } }], next_cursor: '5-0' }))
    .mockResolvedValueOnce(new Response('{"detail":{"reason":"audit_view_unavailable"}}', { status: 503 }));
  const lines = new GatewayClient('http://gateway.test', { maxRetries: 0 }).exportAudit();
  expect(JSON.parse((await lines.next()).value as string)).toEqual({ event: 'team_key' });
  await expect(lines.next()).rejects.toMatchObject({ statusCode: 503, reason: 'audit_view_unavailable' });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
