import { MockActivityEnvironment } from '@temporalio/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GatewayActivities } from '../src/activities';
import type { Call } from '../src/types';

const runId = 'ebf9363a-7912-4a62-89b4-b0a28c169731';
const call: Call = {
  kind: 'model', payload: { messages: [{ role: 'user', content: 'hello' }], max_tokens: 32 },
  budget: { token_limit: 300, cost_limit_usd: 0.5 }, tool: '', data_classification: 'confidential',
};
const environment = () => new MockActivityEnvironment({
  workflowExecution: { workflowId: 'demo/default/review', runId },
  workflowType: 'CodeReviewWorkflow', activityId: 'draft',
});
const activities = new GatewayActivities('http://gateway.test/', 'fixture-worker');
const fetchMock = vi.fn<typeof fetch>();
const ok = (body = {}) => new Response(JSON.stringify(body), { status: 200 });

beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
afterEach(() => vi.unstubAllGlobals());

describe('governed activities', () => {
  it('initializes the budget and correlates model receipts with stable Temporal IDs', async () => {
    fetchMock.mockImplementation(async () => ok({ choices: [] }));
    const env = environment();
    await env.run(activities.call.bind(activities), call);
    await env.run(activities.call.bind(activities), call);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`http://gateway.test/v1/workflow-runs/${runId}`);
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(init?.body as string)).toEqual({ ...call.budget, workflow: 'CodeReviewWorkflow' });
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer fixture-worker', 'X-Workflow-ID': 'demo/default/review' });
    for (const index of [1, 3]) {
      expect(fetchMock.mock.calls[index][0]).toBe('http://gateway.test/v1/chat/completions');
      expect(fetchMock.mock.calls[index][1]?.headers).toMatchObject({
        Authorization: 'Bearer fixture-worker', 'X-Workflow-Run-ID': runId,
        'X-Workflow-Step-ID': 'draft', 'X-Data-Classification': 'confidential',
      });
      expect(JSON.parse(fetchMock.mock.calls[index][1]?.body as string)).toEqual(call.payload);
    }
  });

  it('routes tools through the gateway and encodes their names', async () => {
    fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({ result: ['source'] }));
    const result = await environment().run(activities.call.bind(activities), {
      ...call, kind: 'tool' as const, tool: 'team/search', payload: { arguments: { query: 'topic' } },
    });
    expect(result).toEqual({ result: ['source'] });
    expect(fetchMock.mock.calls[1][0]).toBe('http://gateway.test/v1/tools/team%2Fsearch/call');
  });

  it.each([
    [403, 'workflow_token_budget_exceeded', true],
    [403, 'workflow_cost_budget_exceeded', true],
    [400, 'tool_not_allowed', true],
    [503, 'workflow_store_required', true],
    [503, 'provider_not_configured', true],
    [503, 'tool_not_configured', true],
    [503, 'workflow_store_unavailable', false],
    [429, 'sandbox_token_budget_exceeded', false],
  ])('maps %i %s to a Temporal failure', async (status, reason, nonRetryable) => {
    fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(new Response(JSON.stringify({
      detail: { reason, message: 'private-response-payload fixture-worker' },
    }), { status, headers: { 'X-Request-ID': 'req-1', 'Retry-After': '60' } }));
    const error = await environment().run(activities.call.bind(activities), call).catch((error: unknown) => error);
    expect(error).toMatchObject({ type: reason, nonRetryable, message: expect.stringContaining('req-1') });
    expect(error).toHaveProperty('cause', undefined);
    if (!nonRetryable) expect(error).toHaveProperty('nextRetryDelay', 60_000);
    expect(String(error)).not.toContain('fixture-worker');
    expect(String(error)).not.toContain('private-response-payload');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not call a model after budget initialization is denied', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 403 }));
    await expect(environment().run(activities.call.bind(activities), call)).rejects.toMatchObject({ nonRetryable: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('redacts transport failures and leaves retry scheduling to Temporal', async () => {
    fetchMock.mockRejectedValue(new Error('fixture-worker private request content'));
    await expect(environment().run(activities.call.bind(activities), call)).rejects.toMatchObject({
      type: 'GatewayUnavailable', nonRetryable: false,
      message: 'Cannot reach the gateway; check AGENTWORKFLOWS_URL and gateway health.',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('cancels in-flight HTTP when Temporal cancels the activity', async () => {
    const env = environment();
    fetchMock.mockImplementation(async (_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
      env.cancel();
    }));
    await expect(env.run(activities.call.bind(activities), call)).rejects.toMatchObject({ name: 'CancelledFailure' });
  });

  it('notifies approval without resetting a custom budget, tolerating legacy unindexed runs', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 404 }));
    await expect(environment().run(activities.call.bind(activities), { ...call, kind: 'approval_waiting' as const }))
      .resolves.toMatchObject({ queued: false, policy_version: 1, required_approvals: 1 });
    expect(fetchMock.mock.calls[0][0]).toBe(`http://gateway.test/v1/workflow-runs/${runId}/approval-waiting`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('requests a versioned approval policy without initializing a new budget', async () => {
    const gate = { policy_version: 1, required_approvals: 2, approval_timeout_seconds: 60, queued: true };
    fetchMock.mockResolvedValue(ok(gate));
    await expect(environment().run(activities.call.bind(activities), {
      ...call, kind: 'approval_waiting' as const, payload: { policy_version: 1 },
    })).resolves.toEqual(gate);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ policy_version: 1 });
  });

  it('fires governed triggers, polls their runs, and handles paused triggers', async () => {
    fetchMock.mockResolvedValueOnce(ok({ run_id: runId })).mockResolvedValueOnce(ok({ status: 'completed' }))
      .mockResolvedValueOnce(new Response('{"detail":{"reason":"trigger_paused"}}', { status: 409 }));
    const request = { workflow: 'Daily/Report', trigger: 'daily', firing_id: runId };
    const env = environment();
    await expect(env.run(activities.trigger.bind(activities), request)).resolves.toEqual({ run_id: runId });
    expect(fetchMock.mock.calls[0][0]).toBe('http://gateway.test/v1/workflow-triggers/Daily%2FReport/daily/fire');
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({ firing_id: runId });
    await expect(env.run(activities.trigger.bind(activities), { ...request, run_id: runId })).resolves.toEqual({ status: 'completed' });
    await expect(env.run(activities.trigger.bind(activities), request)).resolves.toEqual({ paused: true });
  });
});
