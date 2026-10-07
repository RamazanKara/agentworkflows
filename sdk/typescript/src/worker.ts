import { NativeConnection, Worker } from '@temporalio/worker';
import { GatewayActivities } from './activities';

export async function runWorker(workflowsPath: string): Promise<void> {
  const apiKey = process.env.AGENTWORKFLOWS_API_KEY;
  if (!apiKey) throw new Error("Set AGENTWORKFLOWS_API_KEY to your team's worker key (demo-worker for Compose).");
  const connection = await NativeConnection.connect({ address: process.env.TEMPORAL_ADDRESS ?? 'localhost:7233' });
  try {
    const activities = new GatewayActivities(process.env.AGENTWORKFLOWS_URL ?? 'http://127.0.0.1:8080', apiKey);
    const worker = await Worker.create({
      connection, workflowsPath, activities: activities.activities(),
      namespace: process.env.TEMPORAL_NAMESPACE ?? 'default',
      taskQueue: process.env.TEMPORAL_TASK_QUEUE ?? `${process.env.AGENTWORKFLOWS_TEAM ?? 'demo'}-workflows`,
      maxConcurrentActivityTaskExecutions: 2, maxConcurrentWorkflowTaskExecutions: 2,
      workflowThreadPoolSize: 1, maxCachedWorkflows: 100, shutdownGraceTime: '180 seconds',
    });
    await worker.run();
  } finally {
    await connection.close();
  }
}
