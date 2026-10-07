import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
process.env.AGENTWORKFLOWS_DEMO_ROOT = root.replaceAll('\\', '/');
const require = createRequire(path.join(root, 'src/inference-gateway/console/package.json'));
const { chromium, expect } = require('@playwright/test');
const output = path.join(root, '.out/compose-demo');
mkdirSync(output, { recursive: true });
const baseURL = process.env.AGENTWORKFLOWS_GATEWAY_URL
  || `http://127.0.0.1:${process.env.AGENTWORKFLOWS_GATEWAY_PORT || 8080}`;

function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (${result.status})`);
}

run('vhs', ['scripts/compose-demo.tape']);
const browser = await chromium.launch();
let video;
try {
  const context = await browser.newContext({
    viewport: { width: 1100, height: 760 },
    recordVideo: { dir: output, size: { width: 1100, height: 760 } },
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  video = page.video();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${baseURL}/console/`);
  await expect(page).toHaveTitle(/AgentWorkflows/);
  await page.getByLabel('Team credential').fill('local-development-only');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
  await page.waitForTimeout(1800);
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  await page.getByLabel('Research topic').fill('How can our team ship agents with budgets and human review?');
  await page.waitForTimeout(1800);
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
  const runURL = page.url();
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    return page.getByRole('heading', { name: 'Review the waiting draft' }).count();
  }, { timeout: 90000, intervals: [1000, 2000] }).toBe(1);
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  const review = page.locator('article.approval-item').filter({
    has: page.locator(`a[href="${new URL(runURL).hash}"]`),
  });
  await expect(review.locator('.draft')).not.toBeEmpty();
  await page.waitForTimeout(3500);
  await review.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(review).toHaveCount(0);
  await page.goto(runURL);
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    return page.locator('.run-meta .badge').textContent();
  }, { timeout: 60000, intervals: [1000, 2000] }).toBe('Completed');
  await expect(page.getByText('"status": "published"', { exact: false })).toBeVisible();
  await page.waitForTimeout(3500);
  await page.getByText('Receipt ·', { exact: false }).first().click();
  await expect(page.getByText('"record_hash"', { exact: false }).first()).toBeVisible();
  await page.screenshot({ path: path.join(output, 'receipts.png') });
  await page.waitForTimeout(3500);
  await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
  await expect(page.getByText('Key present', { exact: true })).toHaveCount(5);
  await page.waitForTimeout(3500);
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('region', { name: 'By workflow table' })).toContainText('ResearchWorkflow');
  await page.screenshot({ path: path.join(output, 'costs.png') });
  await page.waitForTimeout(3500);
  expect(errors).toEqual([]);
  await context.close();
} finally {
  await browser.close();
}

const consoleVideo = await video.path();
run('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2',
  '-i', path.join(output, 'terminal.mp4'), '-i', consoleVideo,
  '-filter_complex_threads', '2', '-filter_complex',
  '[0:v]fps=8,setsar=1,setpts=PTS-STARTPTS[a];[1:v]fps=8,setsar=1,setpts=PTS-STARTPTS[b];'
    + '[a][b]concat=n=2:v=1:a=0,split[x][y];[x]palettegen=max_colors=128[p];'
    + '[y][p]paletteuse=dither=none:diff_mode=rectangle',
  '-threads', '2', '-loop', '0', 'docs/assets/compose-demo.gif',
]);
console.log('Recorded docs/assets/compose-demo.gif from the live Compose trial and console.');
