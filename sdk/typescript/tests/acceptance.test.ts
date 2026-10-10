import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AcceptanceError, GatewayClient, firstApprovedRun } from '../src';

const RUN = '11111111-2222-3333-4444-555555555555';
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
afterEach(() => vi.unstubAllGlobals());

type Options = Partial<{
  blockers: string[]; installed: boolean; simulated: boolean; installStatus: number; draftAfter: number; required: number;
  approveStatus: number; final: string; audit: Record<string, unknown>; auditStatus: number; summary: boolean;
  approvalReceipt: boolean; endStatus: string;
}>;

function gateway(overrides: Options = {}) {
  const o = {
    blockers: [], installed: true, simulated: true, installStatus: 200, draftAfter: 2, required: 1, approveStatus: 200,
    final: 'published', audit: { enabled: true, ok: true, checked: 4 }, auditStatus: 200, summary: true, approvalReceipt: true,
    endStatus: 'completed', ...overrides,
  };
  const calls: string[] = [];
  const approvals: string[] = [];
  let polls = 0;
  fetchMock.mockImplementation(async (input, init) => {
    const path = new URL(String(input)).pathname;
    const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    calls.push(`${init?.method ?? 'GET'} ${path}`);
    if (path === '/v1/team/onboarding') {
      return reply({ providers: [], blockers: o.blockers, sample: { template_id: 'research', version: '0.9.0', workflow: 'ResearchWorkflow',
        installed: o.installed, ready: !o.blockers.length, input: { topic: 't', model: 'demo-openai' } } });
    }
    if (path === '/v1/models') return reply({ data: [{ id: 'demo-openai', simulated: o.simulated }] });
    if (path.endsWith('/install')) return reply({ detail: { message: 'Admin required.' } }, o.installStatus);
    if (path === '/v1/workflow-runs') return reply({ run_id: RUN }, 201);
    if (path === `/v1/workflow-runs/${RUN}/approve`) {
      if (o.approveStatus !== 200) return reply({ detail: { message: 'Not a reviewer.' } }, o.approveStatus);
      approvals.push(String((init?.headers as Record<string, string>).Authorization));
      return reply({ approved: true });
    }
    if (path === `/v1/workflow-runs/${RUN}`) {
      polls++;
      if (approvals.length >= o.required) {
        return reply({ status: o.endStatus, result: { status: o.final }, error: { code: 'GatewayUnavailable' },
          timeline: [{ action: 'model_call' }, ...(o.approvalReceipt ? [{ action: 'approval' }] : [])], ...(o.summary ? { summary: { elapsed_seconds: 5 } } : {}) });
      }
      return reply({ status: 'running', progress: { stage: polls > o.draftAfter ? 'awaiting_approval' : 'researching', required_approvals: o.required } });
    }
    if (path === '/v1/team/audit/verify') return reply(o.audit, o.auditStatus);
    throw new Error(`unexpected ${path}`);
  });
  return { calls, approvals, client: new GatewayClient('http://gateway.test', { apiKey: 'admin-key', maxRetries: 0 }) };
}

function ticking() {
  let milliseconds = 0;
  return { now: () => milliseconds, sleep: async (waited: number) => { milliseconds += waited; }, pollSeconds: 2 };
}

const failure = async (promise: Promise<unknown>) => {
  const error = await promise.then(() => undefined, (value: unknown) => value);
  expect(error).toBeInstanceOf(AcceptanceError);
  return error as AcceptanceError;
};

it('completes every stage with timings for a ready install', async () => {
  const { client, calls, approvals } = gateway();
  const shown: string[] = [];
  const result = await firstApprovedRun(client, { ...ticking(), onStage: row => shown.push(row.stage) });
  expect(result.stages.map(row => row.stage)).toEqual(['ready', 'installed', 'started', 'drafted', 'approved', 'completed', 'evidence', 'audit']);
  expect(shown).toEqual(result.stages.map(row => row.stage));
  expect(result).toMatchObject({ ok: true, run_id: RUN, deadline_seconds: 300, total_seconds: 4, notes: [] });
  expect(result.stages[0].detail).toMatch(/\(simulated\)$/);
  expect(result.stages.at(-1)?.detail).toBe('Verified 4 events');
  expect(calls).toContain('POST /v1/workflow-runs');
  expect(approvals).toEqual(['Bearer admin-key']);
});

it('stops on readiness blockers or an unapproved real provider before starting a run', async () => {
  const blocked = gateway({ blockers: ['Connect a provider for an approved Research model, then refresh readiness.'] });
  const error = await failure(firstApprovedRun(blocked.client, ticking()));
  expect(error.stage).toBe('ready');
  expect(error.message).toContain('Connect a provider');
  expect(blocked.calls).not.toContain('POST /v1/workflow-runs');
  const paid = gateway({ simulated: false });
  expect((await failure(firstApprovedRun(paid.client, ticking()))).message).toContain('allowPaid');
  expect(paid.calls).not.toContain('POST /v1/workflow-runs');
  const allowed = gateway({ simulated: false });
  expect((await firstApprovedRun(allowed.client, { ...ticking(), allowPaid: true })).stages[0].detail).toMatch(/\(real provider\)$/);
});

it('installs the sample only when the credential may, and needs distinct reviewers for a quorum', async () => {
  expect((await failure(firstApprovedRun(gateway({ installed: false, installStatus: 403 }).client, ticking()))).stage).toBe('installed');
  expect((await firstApprovedRun(gateway({ installed: false }).client, ticking())).stages[1].detail).toBe('Installed research 0.9.0');
  const short = gateway({ required: 2 });
  const error = await failure(firstApprovedRun(short.client, ticking()));
  expect(error.stage).toBe('approved');
  expect(error.message).toContain('2 distinct reviewers');
  expect(short.approvals).toEqual([]);
  const shared = gateway({ required: 2 });
  const second = new GatewayClient('http://gateway.test', { apiKey: 'second-reviewer', maxRetries: 0 });
  await firstApprovedRun(shared.client, { ...ticking(), approvers: [second] });
  expect(shared.approvals).toEqual(['Bearer admin-key', 'Bearer second-reviewer']);
});

it('names the failing stage and what to do, and enforces the deadline', async () => {
  expect((await failure(firstApprovedRun(gateway({ approveStatus: 403 }).client, ticking()))).message).toContain('admin or approver role');
  const ended = await failure(firstApprovedRun(gateway({ endStatus: 'failed', required: 0 }).client, ticking()));
  expect(ended.stage).toBe('drafted');
  expect(ended.message).toContain('GatewayUnavailable');
  expect((await failure(firstApprovedRun(gateway({ required: 0 }).client, ticking()))).message).toContain('without pausing for approval');
  const late = await failure(firstApprovedRun(gateway({ draftAfter: 10_000 }).client, { ...ticking(), deadlineSeconds: 20 }));
  expect(late.message).toContain('Timed out after 20 seconds');
  expect(late.stages.map(row => row.stage)).toEqual(['ready', 'installed', 'started']);
  const slow = await failure(firstApprovedRun(gateway({ draftAfter: 2 }).client, { ...ticking(), deadlineSeconds: 3.5 }));
  expect(slow.message).toContain('over the 3.5-second budget');
  expect((await failure(firstApprovedRun(gateway({ final: 'rejected' }).client, ticking()))).message).toContain('outcome rejected');
  expect((await failure(firstApprovedRun(gateway({ approvalReceipt: false }).client, ticking()))).stage).toBe('evidence');
});

it('reports optional evidence as notes and a broken audit chain as a failure', async () => {
  for (const [options, note] of [
    [{ auditStatus: 403 }, 'needs a team admin credential'], [{ audit: { enabled: false } }, 'audit chain is disabled'], [{ summary: false }, 'predates run insights'],
  ] as [Options, string][]) {
    const result = await firstApprovedRun(gateway(options).client, ticking());
    expect(result.notes.join(' ')).toContain(note);
  }
  const broken = await failure(firstApprovedRun(gateway({ audit: { enabled: true, ok: false, checked: 4, first_break: { sequence: 3 } } }).client, ticking()));
  expect(broken.stage).toBe('audit');
  expect(broken.message).toContain('did not verify');
});
