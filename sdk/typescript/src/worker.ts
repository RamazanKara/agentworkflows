import { createServer } from 'node:http';
import type { Server } from 'node:http';
import { NativeConnection, Worker } from '@temporalio/worker';
import { GatewayActivities } from './activities';
import type { AgentHandler } from './activities';
import type { Kubectl } from './containers';

export interface WorkerOptions {
  /** Framework agents by name, for `WorkflowGateway.agent`. */
  agents?: Record<string, AgentHandler>;
  /** Runs kubectl for container steps (default: the `kubectl` binary on PATH). */
  kubectl?: Kubectl;
  /**
   * Serve `/healthz` and `/readyz` on this port on all interfaces, as the workflows Helm chart probes (8081).
   * Overrides AGENTWORKFLOWS_HEALTH_ADDRESS. `/readyz` succeeds while the worker runs and both the gateway and
   * Temporal answer.
   */
  healthPort?: number;
}

const HEALTH_SETTING = 'AGENTWORKFLOWS_HEALTH_ADDRESS';

/**
 * Where the health server listens: `healthPort` on all interfaces, else AGENTWORKFLOWS_HEALTH_ADDRESS as `host:port`
 * (`off` or unset turns it off, so a local worker opens no port).
 */
export function healthAddress(healthPort?: number, setting = process.env[HEALTH_SETTING]): { host: string; port: number } | undefined {
  if (healthPort !== undefined) return { host: '0.0.0.0', port: healthPort };
  const value = (setting ?? '').trim();
  if (!value || value.toLowerCase() === 'off') return undefined;
  const separator = value.lastIndexOf(':');
  const port = value.slice(separator + 1);
  if (separator < 0 || !/^\d+$/.test(port) || Number(port) > 65535) {
    throw new Error(`Set ${HEALTH_SETTING} to host:port (for example 0.0.0.0:8081 for the workflows chart) or off.`);
  }
  return { host: value.slice(0, separator).replace(/^\[|\]$/g, '') || '0.0.0.0', port: Number(port) };
}

/** Answer `/healthz` while the process runs and `/readyz` when `ready()` resolves true. */
export async function serveHealth(port: number, ready: () => Promise<boolean>, host = '0.0.0.0'): Promise<Server> {
  const server = createServer((request, response) => {
    const check = request.url === '/readyz' ? ready().catch(() => false) : Promise.resolve(request.url === '/healthz');
    void check.then((ok) => {
      response.writeHead(ok ? 200 : 503, { 'Content-Length': '0', Connection: 'close' }).end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.off('error', reject);
      resolve();
    });
  });
  return server;
}

/**
 * Run a worker for the compiled workflow module at `workflowsPath`, configured from the same environment variables as
 * the Python worker: AGENTWORKFLOWS_API_KEY (required), AGENTWORKFLOWS_URL, AGENTWORKFLOWS_TEAM, TEMPORAL_ADDRESS,
 * TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE and AGENTWORKFLOWS_HEALTH_ADDRESS. Concurrency is capped at two;
 * SIGINT/SIGTERM drain it gracefully.
 */
export async function runWorker(workflowsPath: string, options: WorkerOptions = {}): Promise<void> {
  const apiKey = process.env.AGENTWORKFLOWS_API_KEY;
  if (!apiKey) throw new Error("Set AGENTWORKFLOWS_API_KEY to your team's worker key (demo-worker for Compose).");
  const probes = healthAddress(options.healthPort);
  const address = process.env.TEMPORAL_ADDRESS ?? 'localhost:7233';
  const gatewayUrl = process.env.AGENTWORKFLOWS_URL ?? 'http://127.0.0.1:8080';
  let connection: NativeConnection;
  try {
    connection = await NativeConnection.connect({ address });
  } catch (error) {
    throw new Error(`Cannot connect to Temporal at ${address}; check TEMPORAL_ADDRESS and that Temporal (or the Compose stack) is running.`,
      { cause: error });
  }
  let health: Server | undefined;
  let worker: Worker | undefined;
  try {
    if (probes) {
      health = await serveHealth(probes.port, async () => {
        if (worker?.getState() !== 'RUNNING') return false;
        const gateway = await fetch(`${gatewayUrl.replace(/\/+$/, '')}/readyz`, { signal: AbortSignal.timeout(2000), redirect: 'error' });
        if (gateway.status !== 200) return false;
        const temporal = await connection.withDeadline(Date.now() + 2000, () => connection.healthService.check({}));
        return temporal.status === 1;
      }, probes.host).catch((error: NodeJS.ErrnoException) => {
        throw new Error(`Cannot serve health checks on ${probes.host}:${probes.port} (${error.code ?? 'error'}). Stop the other ` +
          `process or set ${HEALTH_SETTING} to a free host:port, or off for a local worker.`, { cause: error });
      });
    }
    const activities = new GatewayActivities(gatewayUrl, apiKey, { agents: options.agents, kubectl: options.kubectl });
    worker = await Worker.create({
      connection, workflowsPath, activities: activities.activities(),
      namespace: process.env.TEMPORAL_NAMESPACE ?? 'default',
      taskQueue: process.env.TEMPORAL_TASK_QUEUE ?? `${process.env.AGENTWORKFLOWS_TEAM ?? 'demo'}-workflows`,
      maxConcurrentActivityTaskExecutions: 2, maxConcurrentWorkflowTaskExecutions: 2,
      workflowThreadPoolSize: 1, maxCachedWorkflows: 100, shutdownGraceTime: '180 seconds',
    });
    await worker.run();
  } finally {
    if (health) {
      health.closeAllConnections();
      await new Promise(resolve => health?.close(resolve));
    }
    await connection.close();
  }
}
