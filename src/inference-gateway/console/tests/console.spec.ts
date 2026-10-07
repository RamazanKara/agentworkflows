import { expect, test, type Page } from '@playwright/test';

const id = 'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb';
const run = (overrides = {}) => ({
  run_id: id, workflow: 'ResearchWorkflow', project: 'default', created_at: 1791316800,
  status: 'running', progress: { stage: 'awaiting_approval', draft: '<script>alert("untrusted")</script> A draft for review.' },
  budget: { tokens: 63, cost_usd: .0432, token_limit: 10000, cost_limit_usd: 5 },
  timeline: [{ step_id: 'draft', action: 'model_call', provider: 'anthropic', model: 'demo-anthropic', tokens: 63, cost_usd: .0332, duration_ms: 180, timestamp: 1791316802, status_code: 200, receipt_id: 'a'.repeat(64), chain_id: 'chain-one', attempts: [{ provider: 'openai', status_code: 503 }], receipt: { record_hash: 'a'.repeat(64), prev_hash: 'b'.repeat(64), provider: 'anthropic', workflow_step_id: 'draft' } }],
  ...overrides,
});
const policies = { workflows: { ResearchWorkflow: { allowedModels: ['demo-openai'], allowedProviders: ['openai', 'anthropic'], tokenLimit: 10000, costLimitUsd: 5 }, CustomWorkflow: { allowedModels: [], allowedProviders: [], tokenLimit: 500, costLimitUsd: 1 } } };
const costs = { cost_usd: .0432, tokens: 63, calls: 2 };

async function login(page: Page, token = 'admin') {
  await page.goto('/console/');
  await page.getByLabel('Team credential').fill(token);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.route('**/v1/**', async route => {
    const url = new URL(route.request().url());
    const token = route.request().headers().authorization?.replace('Bearer ', '') || '';
    const team = token === 'other' ? 'other' : 'demo';
    const role = token === 'other' ? 'viewer' : token;
    if (token === 'invalid') return route.fulfill({ status: 401, json: { detail: { message: 'Invalid credential. Ask your team admin for a valid key.' } } });
    let body: unknown;
    if (url.pathname === '/v1/team') body = { team_id: team, role, projects: ['default', 'engineering'], providers: ['openai'], cost_limit_usd: 50, ...(role === 'admin' ? { provider_configuration: { openai: { configured: true, environment_variable: 'TEAM_OPENAI_KEY' } } } : {}) };
    else if (url.pathname === '/v1/workflow-policies') body = policies;
    else if (url.pathname === '/v1/models') body = { data: [{ id: 'demo-openai' }] };
    else if (url.pathname === '/v1/sandbox/budget') body = { usage: { estimated_tokens: 63 }, limits: { estimated_tokens: 200000 } };
    else if (url.pathname === '/v1/usage') body = { estimated_cost: .0432, spend: { project: null, window_start: 1791316800, window_seconds: 86400, cost_limit_usd: 50, reserved_and_spent_usd: .0432, providers: { anthropic: costs }, workflows: { ResearchWorkflow: costs }, accounting: 'Configured prices, not provider invoices.' } };
    else if (url.pathname === '/v1/workflow-runs' && route.request().method() === 'POST') body = { run_id: id };
    else if (url.pathname === '/v1/workflow-runs') body = { runs: team === 'other' || url.searchParams.get('project') === 'engineering' || url.searchParams.get('status') === 'failed' ? [] : [run()], next_offset: null };
    else if (url.pathname.endsWith('/approve')) body = { approved: route.request().postDataJSON().approved, reviewer: token };
    else if (url.pathname === `/v1/workflow-runs/${id}`) body = run();
    else return route.fulfill({ status: 404, json: {} });
    return route.fulfill({ json: body });
  });
});

test('sign in, all main pages, receipts, costs and sign out without persisted credentials', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page);
  await expect(page.getByRole('heading', { name: 'Your first governed workflow' })).toBeVisible();
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'Awaiting approval' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('failed');
  await expect(page.getByRole('heading', { name: 'No matching runs on this page' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('');
  await page.getByRole('link', { name: 'ResearchWorkflow', exact: true }).click();
  await page.getByText('Receipt ·', { exact: false }).click();
  await expect(page.getByText('"prev_hash"', { exact: false })).toBeVisible();
  await page.getByText('Step logs', { exact: true }).click();
  await expect(page.getByText('"status_code": 503', { exact: false })).toBeVisible();
  await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
  await expect(page.getByText('Key present', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'By workflow' })).toBeVisible();
  await expect(page.getByRole('row', { name: 'ResearchWorkflow 2 63 $0.0432' })).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, '']);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByLabel('Team credential')).toHaveValue('');
  expect(errors).toEqual([]);
});

test('invalid auth and expiry give a clear recovery path', async ({ page }) => {
  await page.goto('/console/');
  await page.getByLabel('Team credential').fill('invalid');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid credential');
  await page.getByLabel('Team credential').fill('admin');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
  await page.route('**/v1/usage', route => route.fulfill({ status: 401, json: {} }));
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Your session expired');
  await expect(page.getByLabel('Team credential')).toHaveValue('');
});

test('template inputs are ready to run and completed results are readable', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/v1/workflow-policies', route => route.fulfill({ json: { workflows: {
    ...policies.workflows,
    SupportTriageWorkflow: policies.workflows.ResearchWorkflow,
    CodeReviewWorkflow: policies.workflows.ResearchWorkflow,
  } } }));
  await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: run({
    status: 'completed', progress: undefined, result: 'Priority: high. Suggested owner: account support.',
  }) }));
  await login(page);
  await expect(page).toHaveTitle(/AgentWorkflows/);
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  await page.getByRole('combobox', { name: 'Workflow', exact: true }).selectOption('CodeReviewWorkflow');
  expect(JSON.parse(await page.getByLabel('Workflow input (JSON)').inputValue())).toHaveProperty('diff');
  await page.getByRole('combobox', { name: 'Workflow', exact: true }).selectOption('SupportTriageWorkflow');
  expect(JSON.parse(await page.getByLabel('Workflow input (JSON)').inputValue())).toHaveProperty('ticket');
  const request = page.waitForRequest(r => r.url().endsWith('/v1/workflow-runs') && r.method() === 'POST');
  await page.getByRole('button', { name: 'Start run' }).click();
  expect((await request).postDataJSON().input).toHaveProperty('ticket');
  await expect(page.getByRole('heading', { name: 'Result', exact: true })).toBeVisible();
  await expect(page.getByText('Priority: high. Suggested owner: account support.', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('start is idempotent after a lost response and validates custom JSON', async ({ page }) => {
  const starts: Record<string, unknown>[] = [];
  await page.route('**/v1/workflow-runs', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    starts.push(route.request().postDataJSON());
    return route.fulfill(starts.length === 1 ? { status: 503, json: {} } : { status: 201, json: { run_id: id } });
  });
  await login(page, 'builder');
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('alert')).toContainText('unavailable');
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
  expect(starts[0]).toEqual(starts[1]);
  expect(starts[0].input).toEqual({ topic: 'How should our team evaluate AI agents?', model: 'demo-openai' });
  await page.goto('/console/#new');
  await page.getByRole('combobox', { name: 'Workflow', exact: true }).selectOption('CustomWorkflow');
  await page.getByLabel('Workflow input (JSON)').fill('{bad');
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('alert')).toContainText('valid JSON');
  expect(starts).toHaveLength(2);
});

test('approval inbox follows empty filtered pages in every project and rejects safely', async ({ page }) => {
  await page.route('**/v1/workflow-runs?**', route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('status') !== 'awaiting_approval') return route.fallback();
    return route.fulfill({ json: url.searchParams.get('project') === 'default' ? { runs: [], next_offset: null } : url.searchParams.get('offset') === '0' ? { runs: [], next_offset: 100 } : { runs: [run({ project: 'engineering' })], next_offset: null } });
  });
  await login(page, 'approver');
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  await expect(page.locator('.draft')).toContainText('<script>alert("untrusted")</script>');
  expect(await page.locator('.draft script').count()).toBe(0);
  const decision = page.waitForRequest(request => request.url().endsWith('/approve'));
  await page.getByRole('button', { name: 'Reject', exact: true }).click();
  expect((await decision).postDataJSON()).toEqual({ approved: false });
  await expect(page.getByRole('heading', { name: 'You’re all caught up' })).toBeVisible();
});

test('conflicting approval is actionable, viewer cannot decide or configure', async ({ page }) => {
  await login(page, 'approver');
  await page.route('**/approve', route => route.fulfill({ status: 409, json: { detail: { message: 'This run has no undecided draft. Inspect its current status before reviewing.' } } }));
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('no undecided draft');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.getByLabel('Team credential').fill('viewer');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Providers & budgets', exact: true })).toHaveCount(0);
  await page.goto('/console/#providers');
  await expect(page.getByRole('heading', { name: 'Team admin access required' })).toBeVisible();
});

test('team switching clears prior team data and reload clears identities', async ({ page }) => {
  await login(page);
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await page.getByRole('button', { name: 'Add team / identity' }).click();
  await page.getByLabel('Team credential').fill('other');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your first workflow starts here' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'ResearchWorkflow' })).toHaveCount(0);
  await page.getByLabel('Team / identity', { exact: true }).selectOption({ label: 'demo / admin' });
  await expect(page.getByRole('link', { name: 'ResearchWorkflow' })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Team credential')).toBeVisible();
});

test('mobile navigation, keyboard skip link and bounded table scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Workflow runs', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.keyboard.press('Control+Home');
  await page.getByRole('link', { name: 'Skip to content' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
});

test('tool steps show the tool name even when a receipt carries the gateway default model', async ({ page }) => {
  const detail = run();
  await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: {
    ...detail, timeline: [{ ...detail.timeline[0], tool: 'research', action: 'tool_exec', model: 'demo-openai' }],
  } }));
  await login(page);
  await page.getByRole('link', { name: 'Open latest run and receipts' }).click();
  await expect(page.getByRole('heading', { name: 'research', exact: true })).toBeVisible();
  await expect(page.locator('.step-facts')).toContainText('research');
  await expect(page.locator('.step-facts')).not.toContainText('demo-openai');
});
