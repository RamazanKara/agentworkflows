import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { setTimeout } from 'node:timers/promises';
import { Client, Connection } from '@temporalio/client';
import { NativeConnection, Worker, bundleWorkflowCode } from '@temporalio/worker';
import { GatewayClient } from '../dist/index.js';
import { GatewayActivities } from '../dist/activities.js';

const baseUrl = process.env.AGENTWORKFLOWS_URL ?? 'http://127.0.0.1:8080';
const address = process.env.TEMPORAL_ADDRESS ?? 'localhost:7233';
const namespace = process.env.TEMPORAL_NAMESPACE ?? 'default';
const builder = new GatewayClient(baseUrl, { apiKey: 'demo-builder' });
const approver = new GatewayClient(baseUrl, { apiKey: 'demo-approver' });
const otherTeam = new GatewayClient(baseUrl, { apiKey: 'demo-other-team' });
const connection = await NativeConnection.connect({ address });
let clientConnection;
const pending = new Set();

async function waitFor(runId, predicate) {
  for (let attempt = 0; attempt < 120; attempt++) {
    const run = await builder.run(runId);
    if (predicate(run)) return run;
    if (['failed', 'canceled', 'terminated', 'timed_out'].includes(run.status)) {
      throw new Error(`Unexpected ${run.status}: ${runId}`);
    }
    await setTimeout(500);
  }
  throw new Error(`Run did not reach expected state: ${runId}`);
}

async function start(workflow, input) {
  const run = await builder.startRun(workflow, input, { requestId: randomUUID() });
  pending.add(run.run_id);
  return run;
}

function receipts(run, actions) {
  assert.ok(run.budget.tokens > 0);
  assert.ok(run.budget.cost_usd > 0);
  for (const action of actions) assert.ok(run.timeline.some((row) => row.action === action));
  for (const row of run.timeline) {
    assert.ok(row.receipt_id && row.chain_id && row.step_id);
    assert.equal(row.receipt.workflow_run_id, run.run_id);
    assert.equal(row.receipt.workflow_step_id, row.step_id);
  }
}

try {
  clientConnection = await Connection.connect({ address });
  const temporal = new Client({ connection: clientConnection, namespace });
  const workflowBundle = await bundleWorkflowCode({
    workflowsPath: fileURLToPath(new URL('../tests/fixtures/workflows.ts', import.meta.url)),
  });
  const activities = new GatewayActivities(baseUrl, 'demo-worker');
  const options = {
    connection, namespace, taskQueue: 'demo-workflows', workflowBundle,
    activities: activities.activities(),
    maxConcurrentActivityTaskExecutions: 2, maxConcurrentWorkflowTaskExecutions: 2,
    workflowThreadPoolSize: 1, maxCachedWorkflows: 20,
  };
  let review;
  const first = await Worker.create(options);
  await first.runUntil(async () => {
    const triage = await start('SupportTriageWorkflow', { ticket: 'Cannot sign in after a password reset.' });
    const triaged = await waitFor(triage.run_id, (run) => run.status === 'completed');
    assert.equal(typeof triaged.result, 'string');
    assert.ok(triaged.result.length > 0);
    receipts(triaged, ['model_call']);
    pending.delete(triage.run_id);
    review = await start('CodeReviewWorkflow', { diff: '- return user.is_admin\n+ return True' });
    const waiting = await waitFor(review.run_id, (run) => run.progress?.stage === 'awaiting_approval');
    assert.ok(waiting.progress.draft);
    assert.equal(waiting.progress.run_id, review.run_id);
    await assert.rejects(builder.approveRun(review.run_id), { statusCode: 403 });
    await assert.rejects(otherTeam.run(review.run_id), { statusCode: 404 });
  });

  const second = await Worker.create(options);
  await second.runUntil(async () => {
    await waitFor(review.run_id, (run) => run.progress?.stage === 'awaiting_approval');
    await approver.approveRun(review.run_id);
    const approved = await waitFor(review.run_id, (run) => run.status === 'completed');
    assert.equal(approved.result.approved, true);
    assert.equal(approved.result.reviewer, 'demo-approver');
    receipts(approved, ['model_call', 'approval']);
    assert.equal(approved.timeline.filter((row) => row.action === 'model_call').length, 1);
    pending.delete(review.run_id);
    await Worker.runReplayHistory({ workflowBundle }, await temporal.workflow.getHandle(review.workflow_id, review.run_id).fetchHistory());

    const rejected = await start('CodeReviewWorkflow', { diff: '+ unsafe change' });
    await waitFor(rejected.run_id, (run) => run.progress?.stage === 'awaiting_approval');
    await approver.approveRun(rejected.run_id, { approved: false });
    assert.equal((await waitFor(rejected.run_id, (run) => run.status === 'completed')).result.approved, false);
    pending.delete(rejected.run_id);

    const research = await start('ResearchWorkflow', { topic: 'Tool correlation' });
    const researched = await waitFor(research.run_id, (run) => run.status === 'completed');
    receipts(researched, ['tool_exec', 'model_call']);
    assert.equal(new Set(researched.timeline.map((row) => row.step_id)).size, 2);
    pending.delete(research.run_id);

    for (const budget of [{ token_limit: 1 }, { cost_limit_usd: 0.001 }]) {
      const denied = await start('ResearchWorkflow', { topic: 'Budget denial', ...budget });
      const failed = await waitFor(denied.run_id, (run) => run.status === 'failed');
      assert.ok(failed.timeline.some((row) => row.status_code === 403));
      assert.ok(!failed.timeline.some((row) => row.action === 'model_call' && row.status_code === 200));
      pending.delete(denied.run_id);
    }

    const canceled = await start('CodeReviewWorkflow', { diff: '+ cancel this review' });
    await waitFor(canceled.run_id, (run) => run.progress?.stage === 'awaiting_approval');
    await builder.cancelRun(canceled.run_id);
    await waitFor(canceled.run_id, (run) => run.status === 'canceled');
    pending.delete(canceled.run_id);
    const retried = await builder.retryRun(canceled.run_id);
    pending.add(retried.run_id);
    assert.notEqual(retried.run_id, canceled.run_id);
    await waitFor(retried.run_id, (run) => run.progress?.stage === 'awaiting_approval');
    await approver.approveRun(retried.run_id, { approved: false });
    await waitFor(retried.run_id, (run) => run.status === 'completed');
    pending.delete(retried.run_id);
    assert.ok((await builder.runs()).runs.length > 0);
    assert.ok(Array.isArray((await builder.triggers()).triggers));
  });
  console.log('TypeScript Compose smoke passed: templates, approval/rejection, restart/replay, tools, budgets, roles, receipts, cancel/retry.');
} finally {
  for (const runId of pending) await builder.cancelRun(runId).catch(() => undefined);
  await clientConnection?.close();
  await connection.close();
}
