import type { AddressInfo } from 'node:net';
import { NativeConnection, Worker } from '@temporalio/worker';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { runWorker, serveHealth } from '../src/worker';

const mocks = vi.hoisted(() => ({
  connection: { close: vi.fn(async () => undefined) },
  worker: { run: vi.fn(async () => undefined), getState: vi.fn(() => 'RUNNING') },
}));
vi.mock('@temporalio/worker', () => ({
  NativeConnection: { connect: vi.fn(async () => mocks.connection) },
  Worker: { create: vi.fn(async () => mocks.worker) },
}));

const environment = { ...process.env };
beforeEach(() => {
  vi.clearAllMocks();
  process.env = { ...environment, AGENTWORKFLOWS_API_KEY: 'fixture-worker' };
  for (const name of ['AGENTWORKFLOWS_URL', 'AGENTWORKFLOWS_TEAM', 'TEMPORAL_ADDRESS', 'TEMPORAL_NAMESPACE', 'TEMPORAL_TASK_QUEUE']) {
    delete process.env[name];
  }
});
afterEach(() => { process.env = environment; });

it('requires the worker key before connecting', async () => {
  delete process.env.AGENTWORKFLOWS_API_KEY;
  await expect(runWorker('/workflows.js')).rejects.toThrow('AGENTWORKFLOWS_API_KEY');
  expect(NativeConnection.connect).not.toHaveBeenCalled();
});

it('explains an unreachable Temporal address and keeps the cause', async () => {
  process.env.TEMPORAL_ADDRESS = 'temporal.test:7233';
  const cause = new Error('transport error');
  vi.mocked(NativeConnection.connect).mockRejectedValueOnce(cause);
  const error = await runWorker('/workflows.js').catch((error: unknown) => error);
  expect(error).toMatchObject({ message: expect.stringContaining('Cannot connect to Temporal at temporal.test:7233'), cause });
});

it('registers governed activities and agents on the team queue with bounded concurrency', async () => {
  process.env.AGENTWORKFLOWS_TEAM = 'research';
  const agents = { briefing: async () => 'done' };
  await runWorker('/workflows.js', { agents });
  const options = vi.mocked(Worker.create).mock.calls[0][0];
  expect(options).toMatchObject({
    workflowsPath: '/workflows.js', namespace: 'default', taskQueue: 'research-workflows',
    maxConcurrentActivityTaskExecutions: 2, maxConcurrentWorkflowTaskExecutions: 2,
  });
  expect(Object.keys(options.activities ?? {})).toEqual(['agentworkflows.call', 'agentworkflows.trigger']);
  expect(mocks.worker.run).toHaveBeenCalledTimes(1);
  expect(mocks.connection.close).toHaveBeenCalledTimes(1);
});

it('closes the connection when the worker fails', async () => {
  mocks.worker.run.mockRejectedValueOnce(new Error('worker failed'));
  await expect(runWorker('/workflows.js')).rejects.toThrow('worker failed');
  expect(mocks.connection.close).toHaveBeenCalledTimes(1);
});

it('serves liveness always and readiness only when the check passes', async () => {
  let ready = true;
  const server = await serveHealth(0, async () => {
    if (ready === undefined) throw new Error('check failed');
    return ready;
  });
  const { port } = server.address() as AddressInfo;
  const status = async (path: string) => (await fetch(`http://127.0.0.1:${port}${path}`)).status;
  try {
    expect([await status('/healthz'), await status('/readyz'), await status('/other')]).toEqual([200, 200, 503]);
    ready = false;
    expect(await status('/readyz')).toBe(503);
    ready = undefined as unknown as boolean;
    expect(await status('/readyz')).toBe(503);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

it('refuses to start when the health port is taken', async () => {
  const first = await serveHealth(0, async () => true);
  const { port } = first.address() as AddressInfo;
  try {
    await expect(serveHealth(port, async () => true)).rejects.toMatchObject({ code: 'EADDRINUSE' });
  } finally {
    await new Promise(resolve => first.close(resolve));
  }
});
