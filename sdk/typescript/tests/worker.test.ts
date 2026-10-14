import type { AddressInfo } from 'node:net';
import { NativeConnection, Worker } from '@temporalio/worker';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { healthAddress, runWorker, serveHealth } from '../src/worker';

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
  for (const name of ['AGENTWORKFLOWS_URL', 'AGENTWORKFLOWS_TEAM', 'TEMPORAL_ADDRESS', 'TEMPORAL_NAMESPACE', 'TEMPORAL_TASK_QUEUE', 'AGENTWORKFLOWS_HEALTH_ADDRESS']) {
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

it.each([
  [undefined, undefined, undefined],
  [undefined, 'off', undefined],
  [undefined, ' OFF ', undefined],
  [undefined, '0.0.0.0:8081', { host: '0.0.0.0', port: 8081 }],
  [undefined, '127.0.0.1:9000', { host: '127.0.0.1', port: 9000 }],
  [undefined, '[::1]:9000', { host: '::1', port: 9000 }],
  [undefined, ':9000', { host: '0.0.0.0', port: 9000 }],
  [8081, 'off', { host: '0.0.0.0', port: 8081 }],
] as const)('reads the health address from healthPort %s and AGENTWORKFLOWS_HEALTH_ADDRESS %s', (port, setting, expected) => {
  expect(healthAddress(port, setting)).toEqual(expected);
});

it.each(['8081', 'localhost', 'host:http', 'host:70000'])('rejects the health address %s with the expected form', (setting) => {
  expect(() => healthAddress(undefined, setting)).toThrow('AGENTWORKFLOWS_HEALTH_ADDRESS to host:port');
});

it('serves health from the environment and names the setting when its port is taken', async () => {
  const taken = await serveHealth(0, async () => true, '127.0.0.1');
  const { port } = taken.address() as AddressInfo;
  try {
    process.env.AGENTWORKFLOWS_HEALTH_ADDRESS = `127.0.0.1:${port}`;
    await expect(runWorker('/workflows.js')).rejects.toThrow(`Cannot serve health checks on 127.0.0.1:${port} (EADDRINUSE)`);
    expect(Worker.create).not.toHaveBeenCalled();
    expect(mocks.connection.close).toHaveBeenCalledTimes(1);
    process.env.AGENTWORKFLOWS_HEALTH_ADDRESS = 'nonsense';
    await expect(runWorker('/workflows.js')).rejects.toThrow('AGENTWORKFLOWS_HEALTH_ADDRESS');
    expect(NativeConnection.connect).toHaveBeenCalledTimes(1);
  } finally {
    await new Promise(resolve => taken.close(resolve));
  }
});
