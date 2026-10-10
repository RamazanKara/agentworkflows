// Captures the README console gallery (docs/assets/console-*.png) from the live Compose stack.
// Usage: make compose-up, then node scripts/console-gallery.mjs (uses the console's Playwright).
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'src/inference-gateway/console/package.json'));
const { chromium, expect } = require('@playwright/test');
const out = path.join(root, process.env.OUT || 'docs/assets');
mkdirSync(out, { recursive: true });
const base = process.env.AGENTWORKFLOWS_GATEWAY_URL || 'http://127.0.0.1:8080';

async function signIn(page) {
  await page.goto(`${base}/console/`);
  await page.getByLabel('API key', { exact: true }).fill('local-development-only');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Signed in as' })).toBeVisible();
}
async function open(page, route, ready) {
  await page.goto(`${base}/console/#${route}`);
  if (ready) await expect(ready(page)).toBeVisible({ timeout: 90000 });
  await page.waitForTimeout(1500);
}

const browser = await chromium.launch();
const shots = [];
try {
  // Desktop, light and dark.
  for (const scheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: scheme, locale: 'en-US', timezoneId: 'Europe/Berlin' });
    const page = await context.newPage();
    await signIn(page);
    if (scheme === 'light') {
      // A fresh Research run waits for review, so Approvals has something to show.
      await page.evaluate(async () => {
        const csrf = document.cookie.split('; ').find(value => value.startsWith('aw_csrf='))?.slice(8) || '';
        await fetch('/v1/workflow-runs', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ workflow: 'ResearchWorkflow', project: 'default', input: { topic: 'Which support tickets should an agent answer first?' }, request_id: crypto.randomUUID() }) });
      });
      await open(page, 'approvals', p => p.locator('section.review').first());
      await page.screenshot({ path: `${out}/console-approvals.png` }); shots.push('console-approvals.png');
      await open(page, 'templates', p => p.getByRole('heading', { name: 'Workflow templates' }));
      await page.screenshot({ path: `${out}/console-templates.png` }); shots.push('console-templates.png');
      await open(page, 'insights', p => p.locator('.insights-table'));
      await page.screenshot({ path: `${out}/console-insights.png` }); shots.push('console-insights.png');
      await open(page, 'costs', p => p.getByRole('heading', { name: 'Costs' }));
      await page.screenshot({ path: `${out}/console-costs.png` }); shots.push('console-costs.png');
    } else {
      const runs = await page.evaluate(async () => (await (await fetch('/v1/workflow-runs?project=default&workflow=ResearchWorkflow&status=completed&limit=20', { credentials: 'include' })).json()));
      await open(page, `run/${runs.runs.find(run => run.outcome !== 'rejected')?.run_id ?? runs.runs[0].run_id}`, p => p.getByRole('heading', { name: 'Step timeline' }));
      await page.screenshot({ path: `${out}/console-dark.png` }); shots.push('console-dark.png');
    }
    await context.close();
  }
  // Phones.
  const phone = await browser.newContext({ viewport: { width: 393, height: 852 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'en-US', timezoneId: 'Europe/Berlin' });
  const page = await phone.newPage();
  await signIn(page);
  await open(page, 'runs', p => p.locator('.runs-stack'));
  await page.screenshot({ path: `${out}/console-phone-runs.png` }); shots.push('console-phone-runs.png');
  await open(page, 'approvals', p => p.locator('section.review').first());
  await page.screenshot({ path: `${out}/console-phone-approvals.png` }); shots.push('console-phone-approvals.png');
  await phone.close();
} finally {
  await browser.close();
}
console.log(`Captured ${shots.join(', ')} in ${path.relative(root, out)}.`);
