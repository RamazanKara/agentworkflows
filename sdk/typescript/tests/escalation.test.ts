import { setTimeout } from 'node:timers/promises';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GatewayClient, GatewayError } from '../src';
import type { WorkflowRun } from '../src';
import { withInputSchema } from '../src/workflows';

vi.mock('node:timers/promises', () => ({ setTimeout: vi.fn(async () => undefined) }));
const fetchMock = vi.fn<typeof fetch>();
const client = new GatewayClient('http://gateway.test', { apiKey: 'admin-key' });
const ok = (body: unknown = {}) => new Response(JSON.stringify(body), { status: 200 });
const route = (index: number) => [fetchMock.mock.calls[index][1]?.method, String(fetchMock.mock.calls[index][0])];
const header = (index: number, name: string) => (fetchMock.mock.calls[index][1]?.headers as Record<string, string>)[name];
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); vi.mocked(setTimeout).mockClear(); });
afterEach(() => vi.unstubAllGlobals());

it('lists, sets and removes approval escalation at the reviewed or current revision', async () => {
  const escalation = {
    workflow: 'Release Notes', after_seconds: 14_400, approver_role: 'approver', reviewers: ['oidc|lead'],
    channels: null, effective: true, updated_by: 'admin', updated_at: 1,
  };
  fetchMock.mockResolvedValueOnce(ok({ revision: 7, escalations: [], available_channels: ['slack'] }))
    .mockResolvedValueOnce(ok({ revision: 8, escalation }))
    .mockResolvedValueOnce(ok({ revision: 9, escalation }))
    .mockResolvedValueOnce(ok({ revision: 10, removed: 'Release Notes' }));
  await expect(client.setApprovalEscalation('Release Notes', 14_400, { approver_role: 'approver', reviewers: ['oidc|lead'] }))
    .resolves.toEqual({ revision: 8, escalation });
  await client.setApprovalEscalation('Release Notes', 3_600, { channels: [] }, '"8"');
  await expect(client.removeApprovalEscalation('Release Notes', 9)).resolves.toEqual({ revision: 10, removed: 'Release Notes' });
  expect([0, 1, 2, 3].map(route)).toEqual([
    ['GET', 'http://gateway.test/v1/team/approval-escalations'],
    ['PUT', 'http://gateway.test/v1/team/workflows/Release%20Notes/approval-escalation'],
    ['PUT', 'http://gateway.test/v1/team/workflows/Release%20Notes/approval-escalation'],
    ['DELETE', 'http://gateway.test/v1/team/workflows/Release%20Notes/approval-escalation'],
  ]);
  expect([1, 2, 3].map(index => header(index, 'If-Match'))).toEqual(['7', '"8"', '9']);
  expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
    after_seconds: 14_400, approver_role: 'approver', reviewers: ['oidc|lead'],
  });
  expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({ after_seconds: 3_600, channels: [] });
  expect(fetchMock.mock.calls[3][1]?.body).toBeUndefined();
});

it('names escalation by workflow function and reads the current revision before removing', async () => {
  const Triage = withInputSchema({ type: 'object', properties: {} }, async function TriageWorkflow() { return 1; });
  fetchMock.mockResolvedValueOnce(ok({ revision: 3, escalations: [], available_channels: [] }))
    .mockResolvedValueOnce(ok({ revision: 4, removed: 'TriageWorkflow' }));
  await client.removeApprovalEscalation(Triage);
  expect(route(1)).toEqual(['DELETE', 'http://gateway.test/v1/team/workflows/TriageWorkflow/approval-escalation']);
  expect(header(1, 'If-Match')).toBe('3');
});

it.each([
  ['set', () => client.setApprovalEscalation('Flow', 600, {}, 1)],
  ['remove', () => client.removeApprovalEscalation('Flow', 1)],
] as const)('does not repeat an ambiguous escalation %s, but retries a load-shed one', async (_name, write) => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 502 }));
  await expect(write()).rejects.toBeInstanceOf(GatewayError);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 })).mockResolvedValueOnce(ok({ revision: 2 }));
  await expect(write()).resolves.toEqual({ revision: 2 });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: { reason: 'team_settings_conflict' } }), { status: 409 }));
  await expect(write()).rejects.toMatchObject({ statusCode: 409, reason: 'team_settings_conflict' });
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

it('yields only the waiting runs this credential may review now, across empty cursor pages', async () => {
  const run = (id: string, access?: WorkflowRun['approval_access']) => ({ run_id: id, status: 'running', ...(access ? { approval_access: access } : {}) });
  fetchMock.mockResolvedValueOnce(ok({
    runs: [
      run('role', { eligible: true, via: 'role', eligible_at: null }),
      run('later', { eligible: false, via: null, eligible_at: 1_700_000_000 }),
    ], next_cursor: 'older',
  }))
    .mockResolvedValueOnce(ok({ runs: [], next_cursor: 'oldest' }))
    .mockResolvedValueOnce(ok({ runs: [run('escalated', { eligible: true, via: 'escalation', eligible_at: null }), run('legacy')], next_cursor: null }));
  const ids: string[] = [];
  for await (const waiting of client.reviewQueue({ project: 'A & B', workflow: 'Release Notes', limit: 50 })) ids.push(waiting.run_id);
  expect(ids).toEqual(['role', 'escalated', 'legacy']);
  const queries = fetchMock.mock.calls.map(([url]) => Object.fromEntries(new URL(String(url)).searchParams));
  expect(queries[0]).toEqual({ offset: '0', status: 'awaiting_approval', limit: '50', project: 'A & B', workflow: 'Release Notes' });
  expect(queries.slice(1).map(query => query.cursor)).toEqual(['older', 'oldest']);
  fetchMock.mockReset();
  fetchMock.mockResolvedValueOnce(ok({ runs: [], next_cursor: null }));
  for await (const waiting of client.reviewQueue()) ids.push(waiting.run_id);
  expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.get('limit')).toBe('100');
  expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.has('project')).toBe(false);
});

it('types the run escalation and access fields', async () => {
  const detail = {
    run_id: 'run', approval_access: { eligible: false, via: null, eligible_at: 2 },
    approval_escalation: {
      after_seconds: 60, approver_role: null, reviewers: [], channels: ['slack'], waiting_since: 1,
      escalates_at: 61, escalated: false, recorded_at: null,
    },
  };
  fetchMock.mockResolvedValueOnce(ok(detail));
  const view = await client.run('run');
  expect(view.approval_escalation?.escalates_at).toBe(61);
  expect(view.approval_access?.eligible_at).toBe(2);
});
