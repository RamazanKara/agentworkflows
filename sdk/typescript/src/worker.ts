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
   * Serve `/healthz` and `/readyz` on this port, as the workflows Helm chart probes (8081). `/readyz` succeeds
   * while the worker runs and both the gateway and Temporal answer. Off by default.
   */
  healthPort?: number;
}

/** Answer `/healthz` while the process runs and `/readyz` when `ready()` resolves true, on all interfaces. */
export async function serveHealth(port: number, ready: () => Promise<boolean>): Promise<Server> {
  const server = createServer((request, response) => {
    const check = request.url === '/readyz' ? ready().catch(() => false) : Promise.resolve(request.url === '/healthz');
    void check.then((ok) => {
      response.writeHead(ok ? 200 : 503, { 'Content-Length': '0', Connection: 'close' }).end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '0.0.0.0', () => {
      server.off('error', reject);
      resolve();
    });
  });
  return server;
}

/**
 * Run a worker for the compiled workflow module at `workflowsPath`, configured from the same environment variables as
 * the Python worker: AGENTWORKFLOWS_API_KEY (required), AGENTWORKFLOWS_URL, AGENTWORKFLOWS_TEAM, TEMPORAL_ADDRESS,
 * TEMPORAL_NAMESPACE and TEMPORAL_TASK_QUEUE. Concurrency is capped at two; SIGINT/SIGTERM drain it gracefully.
 */
export async function runWorker(workflowsPath: string, options: WorkerOptions = {}): Promise<void> {
  const apiKey = process.env.AGENTWORKFLOWS_API_KEY;
  if (!apiKey) throw new Error("Set AGENTWORKFLOWS_API_KEY to your team's worker key (demo-worker for Compose).");
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
  try {
    const activities = new GatewayActivities(gatewayUrl, apiKey, { agents: options.agents, kubectl: options.kubectl });
    const worker = await Worker.create({
      connection, workflowsPath, activities: activities.activities(),
      namespace: process.env.TEMPORAL_NAMESPACE ?? 'default',
      taskQueue: process.env.TEMPORAL_TASK_QUEUE ?? `${process.env.AGENTWORKFLOWS_TEAM ?? 'demo'}-workflows`,
      maxConcurrentActivityTaskExecutions: 2, maxConcurrentWorkflowTaskExecutions: 2,
      workflowThreadPoolSize: 1, maxCachedWorkflows: 100, shutdownGraceTime: '180 seconds',
    });
    if (options.healthPort !== undefined) {
      health = await serveHealth(options.healthPort, async () => {
        if (worker.getState() !== 'RUNNING') return false;
        const gateway = await fetch(`${gatewayUrl.replace(/\/+$/, '')}/readyz`, { signal: AbortSignal.timeout(2000), redirect: 'error' });
        if (gateway.status !== 200) return false;
        const temporal = await connection.withDeadline(Date.now() + 2000, () => connection.healthService.check({}));
        return temporal.status === 1;
      });
    }
    await worker.run();
  } finally {
    if (health) {
      health.closeAllConnections();
      await new Promise(resolve => health?.close(resolve));
    }
    await connection.close();
  }
}
