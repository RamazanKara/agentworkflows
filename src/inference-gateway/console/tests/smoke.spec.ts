import { expect, test, type Page } from '@playwright/test';

async function signIn(page: Page, credential: string) {
  await page.goto('/console/');
  await page.getByLabel('Team credential').fill(credential);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
}

test('Compose first run, approval, receipts, provider budgets, costs and team isolation', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await signIn(page, 'local-development-only');
  await expect(page.getByRole('heading', { name: 'Your first governed workflow' })).toBeVisible();
  await page.getByRole('link', { name: 'Triggers', exact: true }).click();
  await expect(page.getByRole('row').filter({ hasText: 'Daily report' })).toContainText('0 9 * * *');
  await page.getByRole('button', { name: 'Pause daily' }).click();
  await expect(page.getByRole('button', { name: 'Resume daily' })).toBeEnabled();
  await page.getByRole('button', { name: 'Resume daily' }).click();
  await expect(page.getByRole('button', { name: 'Pause daily' })).toBeEnabled();
  await page.getByRole('link', { name: 'Get started', exact: true }).click();
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  await page.getByLabel('Research topic').fill('Console smoke: evaluate governed team agents');
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
  const runUrl = page.url();
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    return page.getByRole('heading', { name: 'Review the waiting draft' }).count();
  }, { timeout: 90000, intervals: [1000, 2000, 5000] }).toBe(1);
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  const review = page.locator('article.approval-item').filter({ has: page.locator(`a[href="#${new URL(runUrl).hash.slice(1)}"]`) });
  await expect(review.locator('.draft')).not.toBeEmpty();
  await review.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(review).toHaveCount(0);
  await page.goto(runUrl);
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    return page.locator('.run-meta .badge').textContent().catch(() => '');
  }, { timeout: 60000, intervals: [1000, 2000] }).toBe('Completed');
  await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible();
  await expect(page.getByText('"status": "published"', { exact: false })).toBeVisible();
  await page.getByText('Receipt ·', { exact: false }).first().click();
  await expect(page.getByText('"record_hash"', { exact: false }).first()).toBeVisible();
  await page.getByText('Step logs', { exact: true }).first().click();
  await expect(page.locator('details[open]').filter({ hasText: 'Step logs' }).locator('pre')).toContainText('"status_code"');
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('completed');
  await expect(page.getByRole('link', { name: 'Research', exact: true }).first()).toBeVisible();
  await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
  await expect(page.getByText('Key present', { exact: true })).toHaveCount(5);
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('region', { name: 'By workflow table' })).toContainText('Research');
  await page.getByRole('button', { name: 'Switch identity' }).click();
  await page.getByLabel('Team credential').fill('demo-other-team');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.goto(runUrl);
  await expect(page.getByRole('alert')).toContainText('in your team');
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  expect(errors).toEqual([]);
});
