// Records docs/assets/console-demo.gif: one Research run from the live Compose console, through
// review and approval to the published result and its cost.
// Usage: make compose-up, then node scripts/console-demo.mjs (needs ffmpeg and the console's Playwright).
import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
const require = createRequire(path.join(root, 'src/inference-gateway/console/package.json'));
const { chromium, expect } = require('@playwright/test');
const output = path.join(root, '.out/console-demo');
mkdirSync(output, { recursive: true });
const baseURL = process.env.AGENTWORKFLOWS_GATEWAY_URL
  || `http://127.0.0.1:${process.env.AGENTWORKFLOWS_GATEWAY_PORT || 8080}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

const browser = await chromium.launch();
let video;
try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: output, size: { width: 1280, height: 800 } },
    colorScheme: process.env.SCHEME || 'light', locale: 'en-US', timezoneId: 'Europe/Berlin',
  });
  const page = await context.newPage();
  video = page.video();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${baseURL}/console/`);
  await pause(1200);
  await page.getByLabel('API key', { exact: true }).pressSequentially('local-development-only', { delay: 25 });
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
  await page.goto(`${baseURL}/console/#templates`);
  await expect(page.getByRole('heading', { name: 'Workflow templates' })).toBeVisible();
  await pause(1800);
  await page.goto(`${baseURL}/console/#new/ResearchWorkflow`);
  const topic = page.getByLabel('Topic', { exact: true });
  await topic.fill('');
  await topic.pressSequentially('How can our team ship agents with budgets and human review?', { delay: 18 });
  await pause(900);
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
  const runURL = page.url();
  await expect(page.getByRole('heading', { name: 'Review the draft' })).toBeVisible({ timeout: 90000 });
  await pause(2200);
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  const review = page.locator('section.review').filter({ has: page.locator(`a[href="${new URL(runURL).hash}"]`) });
  await expect(review.locator('.draft')).not.toBeEmpty();
  await pause(2200);
  await review.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(review).toHaveCount(0);
  await pause(900);
  await page.goto(runURL);
  await expect(page.locator('.run-meta .badge')).toHaveText('Completed', { timeout: 60000 });
  await pause(2200);
  await page.mouse.wheel(0, 520);
  await pause(2000);
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Costs' })).toBeVisible();
  await pause(2400);
  await page.getByRole('link', { name: 'Insights', exact: true }).click();
  await pause(2600);
  expect(errors).toEqual([]);
  await context.close();
} finally {
  await browser.close();
}

const target = process.env.OUT || 'docs/assets/console-demo.gif';
const result = spawnSync('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y', '-i', await video.path(), '-filter_complex',
  'fps=8,scale=960:-1:flags=lanczos,split[x][y];[x]palettegen=max_colors=160:stats_mode=diff[p];[y][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle',
  '-loop', '0', target,
], { stdio: 'inherit' });
if (result.status !== 0) throw new Error('ffmpeg failed');
console.log(`Recorded ${target} from the live Compose console.`);
