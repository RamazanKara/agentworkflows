import { runWorker } from '../worker';

runWorker(require.resolve('./workflows')).catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : 'Worker failed');
  process.exitCode = 1;
});
