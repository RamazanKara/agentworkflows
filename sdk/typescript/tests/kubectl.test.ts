import { chmod, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { kubectl } from '../src/containers';

// A stand-in kubectl on PATH: the first argument picks its behavior.
const FAKE = `#!/usr/bin/env node
const mode = process.argv[2];
let input = '';
process.stdin.on('data', chunk => { input += chunk; });
process.stdin.on('end', () => {
  if (mode === 'echo') process.stdout.write(input);
  if (mode === 'fail') process.exit(3);
  if (mode === 'big') { process.stdout.write('x'.repeat(2 * 1048576)); return; }
  if (mode === 'hang') setInterval(() => undefined, 1000);
});
`;

describe.skipIf(process.platform === 'win32')('kubectl runner', () => {
  let directory: string;
  let path: string | undefined;
  beforeAll(async () => {
    directory = await mkdtemp(join(tmpdir(), 'aw-kubectl-'));
    await writeFile(join(directory, 'kubectl'), FAKE);
    await chmod(join(directory, 'kubectl'), 0o755);
    path = process.env.PATH;
    process.env.PATH = `${directory}:${path}`;
  });
  afterAll(async () => {
    process.env.PATH = path;
    await rm(directory, { recursive: true, force: true });
  });

  it('writes stdin and returns stdout', async () => {
    expect(String(await kubectl(['echo'], { input: '{"api_key":"grant"}', timeoutMs: 10_000 }))).toBe('{"api_key":"grant"}');
  });

  it('reports a failed command without its output', async () => {
    await expect(kubectl(['fail'], { timeoutMs: 10_000 })).rejects.toMatchObject({ type: 'WorkspaceCommandFailed', nonRetryable: true });
  });

  it('stops output above 1 MiB', async () => {
    await expect(kubectl(['big'], { timeoutMs: 10_000 })).rejects.toMatchObject({ type: 'WorkspaceOutputTooLarge', nonRetryable: true });
  });

  it('kills a command that outlives its timeout', async () => {
    const started = performance.now();
    await expect(kubectl(['hang'], { timeoutMs: 300 })).rejects.toMatchObject({ type: 'WorkspaceTimeout' });
    expect(performance.now() - started).toBeLessThan(5_000);
  });

  it('kills a command when the activity is canceled', async () => {
    const controller = new AbortController();
    const running = kubectl(['hang'], { timeoutMs: 10_000, signal: controller.signal });
    setTimeout(() => controller.abort(), 200);
    await expect(running).rejects.toMatchObject({ type: 'WorkspaceCanceled' });
  });

  it('explains a worker without kubectl', async () => {
    process.env.PATH = '/nonexistent-aw-path';
    try {
      await expect(kubectl(['echo'], { timeoutMs: 10_000 })).rejects.toMatchObject({ type: 'KubectlMissing', nonRetryable: true });
    } finally {
      process.env.PATH = `${directory}:${path}`;
    }
  });
});
