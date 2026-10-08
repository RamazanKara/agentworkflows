import { expect, test, type Page } from '@playwright/test';

const id = 'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb';
const run = (overrides = {}) => ({
  run_id: id, workflow: 'ResearchWorkflow', project: 'default', created_at: 1791316800,
  status: 'running', progress: { stage: 'awaiting_approval', draft: '<script>alert("untrusted")</script> A draft for review.' },
  budget: { tokens: 63, cost_usd: .0432, token_limit: 10000, cost_limit_usd: 5 },
  timeline: [{ step_id: 'draft', action: 'model_call', provider: 'anthropic', model: 'demo-anthropic', tokens: 63, cost_usd: .0332, duration_ms: 180, timestamp: 1791316802, status_code: 200, receipt_id: 'a'.repeat(64), chain_id: 'chain-one', attempts: [{ provider: 'openai', status_code: 503 }], receipt: { record_hash: 'a'.repeat(64), prev_hash: 'b'.repeat(64), provider: 'anthropic', workflow_step_id: 'draft' } }],
  ...overrides,
});
const policies = { workflows: { ResearchWorkflow: { inputSchema: {"type":"object","properties":{"topic":{"type":"string","default":"How should our team evaluate AI agents?"},"model":{"type":"string","default":"demo-openai"}},"required":["topic"]}, allowedModels: ['demo-openai'], allowedProviders: ['openai', 'anthropic'], tokenLimit: 10000, costLimitUsd: 5 }, CustomWorkflow: { allowedModels: [], allowedProviders: [], tokenLimit: 500, costLimitUsd: 1 } } };
const costs = { cost_usd: .0432, tokens: 63, calls: 2 };

test('schema forms render all supported field types and submit typed values', async ({ page }) => {
  await page.route('**/v1/workflow-policies', route => route.fulfill({ json: { workflows: { FormWorkflow: {
    allowedModels: [], allowedProviders: [], tokenLimit: 1000, costLimitUsd: 1,
    inputSchema: { type: 'object', description: 'A schema supplied by the team.', properties: {
      title: { type: 'string', description: 'Name this request', examples: ['Example title'] },
      body: { type: 'string', examples: ['First line\nSecond line'] },
      count: { type: 'integer', default: 2 },
      price: { type: 'number', default: 1.5 },
      enabled: { type: 'boolean', default: false },
      mode: { type: 'string', enum: ['fast', 'thorough'], default: 'fast' },
      priority: { type: 'integer', enum: [1, 2], default: 2 },
      tags: { type: 'array', items: { type: 'string' }, default: ['one', 'two'] },
      optional: { type: 'string' },
    }, required: ['title', 'count', 'enabled'] },
  } } } }));
  const submissions: Record<string, unknown>[] = [];
  await page.route('**/v1/workflow-runs', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { run_id: id } });
  });
  await login(page, 'builder');
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  await expect(page.getByLabel('Workflow input (JSON)')).toHaveCount(0);
  await expect(page.getByText('A schema supplied by the team.')).toBeVisible();
  await page.getByRole('button', { name: 'Start run' }).click();
  expect(submissions).toHaveLength(0);
  await page.getByLabel('Title', { exact: true }).fill('Form test');
  await expect(page.getByLabel('Price', { exact: true })).toBeHidden();
  await page.getByText('More options', { exact: true }).click();
  await expect(page.getByLabel('Body', { exact: true })).toHaveJSProperty('tagName', 'TEXTAREA');
  await page.getByLabel('Body', { exact: true }).fill('<script>untrusted</script>\nSecond line');
  await page.getByLabel('Count', { exact: true }).fill('3');
  await page.getByLabel('Price', { exact: true }).fill('2.25');
  await page.getByLabel('Mode', { exact: true }).selectOption({ label: 'thorough' });
  await page.getByLabel('Tags', { exact: true }).fill('one\nthree');
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
  expect(submissions[0].input).toEqual({ title: 'Form test', body: '<script>untrusted</script>\nSecond line', count: 3, price: 2.25, enabled: false, mode: 'thorough', priority: 2, tags: ['one', 'three'] });
});

test('schema validation errors identify fields and switching workflows clears form values', async ({ page }) => {
  await page.route('**/v1/workflow-runs', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({ status: 422, json: { detail: {
      reason: 'workflow_input_invalid', message: 'input.topic: Field is required.',
      fields: [{ field: 'input.topic', message: 'Field is required.' }],
    } } });
  });
  await login(page, 'builder');
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  await page.getByLabel('Topic', { exact: true }).fill('Changed topic');
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('alert')).toContainText('input.topic: Field is required.');
  await page.getByLabel('Workflow', { exact: true }).selectOption('CustomWorkflow');
  await expect(page.getByLabel('Workflow input (JSON)')).toHaveValue('{}');
  await page.getByLabel('Workflow', { exact: true }).selectOption('ResearchWorkflow');
  await expect(page.getByLabel('Topic', { exact: true })).toHaveValue('How should our team evaluate AI agents?');
});

for (const mode of ['redacted', 'full', 'capture_off', 'expired']) {
  test(`step content shows ${mode} with escaped text and capture notes`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const original = run();
    const content = ['redacted', 'full'].includes(mode) ? {
      input: '<script>throw new Error("unsafe")</script>\nPrompt',
      output: '<img src=x onerror=alert(1)>\nAnswer',
      redaction: mode, truncated: { input: true, output: true },
    } : null;
    await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: {
      ...original, timeline: [{ ...original.timeline[0], content, content_reason: content ? undefined : mode }],
    } }));
    await login(page, 'viewer');
    await page.goto(`/console/#run/${id}`);
    await page.getByText('Prompt and response', { exact: true }).click();
    if (content) {
      await expect(page.locator('.step-content').first()).toHaveText(content.input);
      await expect(page.locator('.step-content').last()).toHaveText(content.output);
      await expect(page.locator('.step-content script, .step-content img')).toHaveCount(0);
      await expect(page.locator('.step-content').first()).toHaveCSS('white-space', 'pre-wrap');
      await expect(page.getByText('Input truncated at the capture size limit.')).toBeVisible();
      await expect(page.getByText('Output truncated at the capture size limit.')).toBeVisible();
      await expect(page.getByText(mode === 'redacted' ? 'Saved with sensitive values masked.' : 'Saved in full after the gateway’s checks.')).toBeVisible();
    } else {
      await expect(page.getByText(mode === 'expired' ? 'Captured content expired.' : 'Content capture is off for this step.')).toBeVisible();
    }
    expect(errors).toEqual([]);
  });
}

async function login(page: Page, token = 'admin') {
  await page.goto('/console/');
  await page.getByLabel('API key', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Signed in as' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  let identity = '';
  await page.route('**/v1/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/v1/auth/config') return route.fulfill({ json: { api_key: true, jwt: true, oidc: { enabled: false } } });
    if (url.pathname === '/v1/auth/session') {
      if (route.request().method() === 'POST') {
        const key = route.request().postDataJSON().key;
        if (key === 'invalid') return route.fulfill({ status: 401, json: { detail: { message: 'Invalid credential. Ask your team admin for a valid key.' } } });
        identity = key;
        await page.context().addCookies([
          { name: 'aw_session', value: 'opaque-session', url: 'http://127.0.0.1:4175', httpOnly: true, sameSite: 'Lax' },
          { name: 'aw_csrf', value: 'csrf-fixture', url: 'http://127.0.0.1:4175', sameSite: 'Lax' },
        ]);
      }
      return route.fulfill(identity ? { json: { csrf_token: 'csrf-fixture', principal: { key_id: identity } } } : { status: 401, json: {} });
    }
    if (url.pathname === '/v1/auth/logout') {
      expect(route.request().headers()['x-csrf-token']).toBe('csrf-fixture');
      identity = '';
      await page.context().clearCookies();
      return route.fulfill({ json: { signed_out: true } });
    }
    expect(route.request().headers().authorization).toBeUndefined();
    if (route.request().method() !== 'GET') expect(route.request().headers()['x-csrf-token']).toBe('csrf-fixture');
    const token = identity;
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

test('model prompts read as a transcript and the answer is previewed on the step', async ({ page }) => {
  const original = run();
  const messages = [{ role: 'system', content: 'You are a careful analyst.' }, { role: 'user', content: [{ type: 'text', text: 'Compare <b>two</b> vendors.' }] }];
  await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: {
    ...original, timeline: [{ ...original.timeline[0], content: { input: JSON.stringify(messages), output: '"Vendor A wins on cost."', redaction: 'redacted', truncated: { input: false, output: false } } }],
  } }));
  await login(page, 'viewer');
  await page.goto(`/console/#run/${id}`);
  await expect(page.locator('.step-preview')).toHaveText('Vendor A wins on cost.');
  await page.getByText('Prompt and response', { exact: true }).click();
  await expect(page.locator('.transcript .speaker')).toHaveText(['System', 'User']);
  await expect(page.locator('.transcript pre').last()).toHaveText('Compare <b>two</b> vendors.');
  await expect(page.locator('.step-content').last()).toHaveText('Vendor A wins on cost.');
});

test('triggers show schedules and signed endpoints; pause and resume are accessible', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let paused = false;
  await page.route('**/v1/workflow-triggers**', async route => {
    if (route.request().method() === 'PATCH') {
      paused = route.request().postDataJSON().paused;
      return route.fulfill({ json: { paused } });
    }
    return route.fulfill({ json: { triggers: [
      { workflow: 'DailyReportWorkflow', name: 'daily', project: 'default', kind: 'cron', cron: '0 9 * * *', paused, configuration_paused: false, next_fire_at: ['2026-10-08T09:00:00Z'] },
      { workflow: 'GitHubIssueTriageWorkflow', name: 'github', project: 'engineering', kind: 'webhook', cron: '', paused: false, configuration_paused: false, secret_configured: true, url: '/v1/hooks/demo/GitHubIssueTriageWorkflow/github' },
    ] } });
  });
  await login(page);
  await page.getByRole('link', { name: 'Triggers', exact: true }).click();
  await expect(page).toHaveURL(/#triggers$/);
  await expect(page.getByRole('heading', { name: 'Triggers', exact: true })).toBeVisible();
  await expect(page.getByText('Every day at 09:00 UTC')).toBeVisible();
  await expect(page.getByText('/v1/hooks/demo/GitHubIssueTriageWorkflow/github')).toBeVisible();
  await page.getByRole('button', { name: 'Pause daily' }).click();
  await expect(page.getByRole('button', { name: 'Resume daily' })).toBeEnabled();
  await expect(page.getByRole('status')).toContainText('daily paused');
  await page.getByRole('button', { name: 'Resume daily' }).click();
  await expect(page.getByRole('button', { name: 'Pause daily' })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('viewer cannot pause triggers and configuration errors are actionable', async ({ page }) => {
  await page.route('**/v1/workflow-triggers', route => route.fulfill({ json: { triggers: [
    { workflow: 'Report', name: 'daily', project: 'default', kind: 'cron', cron: '0 9 * * *', paused: false, configuration_paused: false, error: 'Schedule unavailable; check Temporal and the gateway configuration.' },
  ] } }));
  await login(page, 'viewer');
  await page.getByRole('link', { name: 'Triggers', exact: true }).click();
  await expect(page.getByText('Builder access required')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Pause daily' })).toHaveCount(0);
  await expect(page.getByText('Schedule unavailable;', { exact: false })).toBeVisible();
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
  await page.getByRole('link', { name: 'Research', exact: true }).click();
  await page.getByText('Receipt', { exact: true }).first().click();
  await expect(page.getByText('"prev_hash"', { exact: false })).toBeVisible();
  await page.getByText('Step logs', { exact: true }).click();
  await expect(page.getByText('"status_code": 503', { exact: false })).toBeVisible();
  await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
  await expect(page.getByText('Key present', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'By workflow' })).toBeVisible();
  await expect(page.getByRole('row', { name: 'Research 2 63 $0.04' })).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, 'aw_csrf=csrf-fixture']);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByLabel('API key', { exact: true })).toHaveValue('');
  expect(errors).toEqual([]);
});

test('a fresh Helm install says which provider Secret to add, without demo wording', async ({ page }) => {
  await page.route('**/v1/team', route => route.fulfill({ json: { team_id: 'default', role: 'admin', projects: ['default'], providers: ['openai', 'anthropic'], cost_limit_usd: 50,
    provider_configuration: { openai: { configured: false, environment_variable: 'OPENAI_API_KEY' }, anthropic: { configured: false, environment_variable: 'ANTHROPIC_API_KEY' } } } }));
  await page.route('**/v1/models', route => route.fulfill({ json: { data: [{ id: 'research', owned_by: 'openai' }, { id: 'anthropic', owned_by: 'anthropic' }] } }));
  await login(page);
  await expect(page.getByRole('heading', { name: 'Add a provider key' })).toBeVisible();
  await expect(page.getByText('OpenAI and Anthropic keys are missing, so runs fail until you add one.', { exact: false })).toBeVisible();
  await expect(page.locator('.model-list li').first()).toContainText('researchOpenAIKey missing');
  await expect(page.getByText('Compose demo', { exact: false })).toHaveCount(0);
  await page.getByRole('link', { name: 'Run workflow', exact: true }).first().click();
  await expect(page.locator('.note-warn')).toContainText('model calls in this run will fail');
  await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
  const setup = page.locator('section.setup');
  await expect(setup.getByRole('heading', { name: 'Connect OpenAI' })).toBeVisible();
  await expect(setup.locator('pre')).toContainText('kubectl create secret generic openai-api-key -n aw --from-file=api-key=/dev/stdin');
  await expect(setup.locator('pre')).toContainText('--set providers.openai.existingSecret=openai-api-key');
  await expect(setup).toContainText('Then repeat for Anthropic.');
  await expect(setup.getByRole('button', { name: 'Copy commands' })).toBeVisible();
  await expect(setup).not.toContainText('model route');
  await expect(setup.getByRole('button', { name: 'Copy policy fragment' })).toHaveCount(0);
});

test('only gateways serving simulated models show the Compose demo note', async ({ page }) => {
  await page.route('**/v1/models', route => route.fulfill({ json: { data: [{ id: 'demo-openai', owned_by: 'openai', simulated: true }] } }));
  await login(page);
  await expect(page.getByText('Its models are simulated', { exact: false })).toBeVisible();
});

test('invalid auth and expiry give a clear recovery path', async ({ page }) => {
  await page.goto('/console/');
  await page.getByLabel('API key', { exact: true }).fill('invalid');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid credential');
  await page.getByLabel('API key', { exact: true }).fill('admin');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
  await page.route('**/v1/usage', route => route.fulfill({ status: 401, json: {} }));
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Your session expired');
  await expect(page.getByLabel('API key', { exact: true })).toHaveValue('');
});

test('template inputs are ready to run and completed results are readable', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/v1/workflow-policies', route => route.fulfill({ json: { workflows: {
    ...policies.workflows,
    SupportTriageWorkflow: { ...policies.workflows.ResearchWorkflow, inputSchema: { type: 'object', properties: { ticket: { type: 'string', default: 'Example input' } }, required: ['ticket'] } },
    CodeReviewWorkflow: { ...policies.workflows.ResearchWorkflow, inputSchema: { type: 'object', properties: { diff: { type: 'string', default: 'Example input' } }, required: ['diff'] } },
    WeeklyReportWorkflow: { ...policies.workflows.ResearchWorkflow, inputSchema: { type: 'object', properties: { period: { type: 'string', default: 'Example input' } }, required: ['period'] } },
    IncidentSummaryWorkflow: { ...policies.workflows.ResearchWorkflow, inputSchema: { type: 'object', properties: { incident_id: { type: 'string', default: 'Example input' } }, required: ['incident_id'] } },
    DocumentQAWorkflow: { ...policies.workflows.ResearchWorkflow, inputSchema: { type: 'object', properties: { question: { type: 'string', default: 'Example input' } }, required: ['question'] } },
  } } }));
  await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: run({
    status: 'completed', progress: undefined, result: 'Priority: high. Suggested owner: account support.',
  }) }));
  await login(page);
  await expect(page).toHaveTitle(/AgentWorkflows/);
  await page.getByRole('link', { name: 'Run workflow', exact: true }).click();
  for (const [workflow, field] of [
    ['CodeReviewWorkflow', 'diff'], ['WeeklyReportWorkflow', 'period'],
    ['IncidentSummaryWorkflow', 'incident_id'], ['DocumentQAWorkflow', 'question'],
  ]) {
    await page.getByRole('combobox', { name: 'Workflow', exact: true }).selectOption(workflow);
    await expect(page.locator(`[name="input.${field}"]`)).toHaveValue('Example input');
  }
  await page.getByRole('combobox', { name: 'Workflow', exact: true }).selectOption('SupportTriageWorkflow');
  await expect(page.getByLabel('Ticket', { exact: true })).toHaveValue('Example input');
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
  await page.getByLabel('API key', { exact: true }).fill('viewer');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Providers & budgets', exact: true })).toHaveCount(0);
  await page.goto('/console/#providers');
  await expect(page.getByRole('heading', { name: 'Team admin access required' })).toBeVisible();
});

test('team switching clears prior team data and reload restores the active session', async ({ page }) => {
  await login(page);
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await page.getByRole('button', { name: 'Switch account' }).click();
  await page.getByLabel('API key', { exact: true }).fill('other');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your first workflow starts here' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Research' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Switch account' }).click();
  await page.getByLabel('API key', { exact: true }).fill('admin');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Research' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('navigation')).toBeVisible();
  await expect(page.getByLabel('API key', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Signed in as' })).toHaveText(/demo\s*Admin/);
});

test('mobile navigation, keyboard skip link and bounded table scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await expect(page.getByRole('navigation')).toBeHidden();
  await page.getByRole('button', { name: 'Open menu' }).click();
  await expect(page.getByRole('link', { name: 'Get started', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Workflow runs', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Open menu' })).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toBeHidden();
  await page.getByRole('button', { name: 'Filters' }).click();
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toBeVisible();
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
  await page.getByRole('link', { name: 'Open the latest run' }).click();
  await expect(page.getByRole('heading', { name: 'Research', exact: true, level: 3 })).toBeVisible();
  await expect(page.locator('.step-facts')).toContainText('research');
  await expect(page.locator('.step-facts')).not.toContainText('demo-openai');
});

test('fallback holds are explained and notification receipts read as one step', async ({ page }) => {
  const detail = run();
  const step = detail.timeline[0];
  const notification = (channel: string, outcome: string, n: number) => ({
    ...step, step_id: 'notification', action: 'notification', provider: '', model: '', tokens: 0, cost_usd: 0, attempts: [],
    receipt_id: String(n).repeat(64), receipt: { channel, notification_event: 'awaiting_approval', outcome },
  });
  await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: { ...detail, timeline: [
    { ...step, tokens: 548, cost_usd: 1.634, attempts: [
      { provider: 'openai', status: 'failed', status_code: 503, reserved: { tokens: 541, cost_usd: 1.623 } },
      { provider: 'anthropic', status: 'served', reserved: { tokens: 541, cost_usd: 1.623 }, charged: { tokens: 7, cost_usd: .011 } },
    ] },
    notification('slack', 'attempted', 1), notification('slack', 'delivered', 2), notification('email', 'delivered', 3),
  ] } }));
  await login(page);
  await page.getByRole('link', { name: 'Open the latest run' }).click();
  await expect(page.getByText('OpenAI returned 503, so the gateway sent this step to Anthropic. $1.62 and 541 tokens stay held for the failed OpenAI attempt')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Notifications', exact: true })).toHaveCount(1);
  await expect(page.locator('.step-facts').first()).toContainText('$0.01');
  await expect(page.locator('.notifications li')).toHaveCount(2);
  await expect(page.locator('.notifications')).toContainText('Slack');
  await expect(page.getByText('3 receipts')).toBeVisible();
  await expect(page.locator('.timeline')).not.toContainText('Step notification');
});

test('rejected runs read as rejected in the run list, with a readable result', async ({ page }) => {
  await page.route('**/v1/workflow-runs?*', route => route.fulfill({ json: { runs: [run({ status: 'completed', progress: undefined, outcome: 'rejected' })], next_offset: null } }));
  await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: run({ status: 'completed', progress: undefined, result: { status: 'rejected', reviewer: 'approver' } }) }));
  await login(page);
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await expect(page.locator('.runs-stack .badge')).toHaveText('Rejected');
  await page.getByRole('link', { name: 'Research', exact: true }).click();
  await expect(page.getByText('Rejected by approver. Nothing was published.')).toBeVisible();
});

test('session restores after reload without retaining the API key', async ({ page }) => {
  await login(page);
  const restored = page.waitForRequest('**/v1/auth/session');
  await page.reload();
  expect((await restored).method()).toBe('GET');
  await expect(page.getByRole('navigation')).toBeVisible();
  await expect(page.getByLabel('API key', { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  const cookies = await page.context().cookies();
  expect(cookies.find(cookie => cookie.name === 'aw_session')?.httpOnly).toBe(true);
  expect(cookies.every(cookie => cookie.value !== 'admin')).toBe(true);
});

test('OIDC sign-in button is shown only when configured', async ({ page }) => {
  await page.goto('/console/');
  await expect(page.getByLabel('API key', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in with', exact: false })).toHaveCount(0);
  await page.route('**/v1/auth/config', route => route.fulfill({ json: {
    api_key: true, oidc: { enabled: true, provider_name: 'Company', login_url: '/v1/auth/login' },
  } }));
  await page.reload();
  await expect(page.getByRole('button', { name: 'Sign in with your company account' })).toBeVisible();
  await expect(page.getByLabel('API key', { exact: true })).toBeVisible();
  await page.route('**/v1/auth/login', route => route.fulfill({ contentType: 'text/html', body: '<p>Company sign-in</p>' }));
  await page.getByRole('button', { name: 'Sign in with your company account' }).click();
  await expect(page).toHaveURL(/\/v1\/auth\/login$/);
});

test('creating a key shows the secret once and copies it', async ({ page, context }) => {
  const secret = 'aw_' + 'a'.repeat(40);
  const keys: Record<string, unknown>[] = [];
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.route('**/v1/team/keys', route => {
    if (route.request().method() === 'POST') {
      expect(route.request().headers()['x-csrf-token']).toBe('csrf-fixture');
      expect(route.request().headers().authorization).toBeUndefined();
      keys.push({ key_id: 'key-1', ...route.request().postDataJSON(), last_used_at: null, revoked_at: null });
      return route.fulfill({ status: 201, json: { ...keys[0], key: secret } });
    }
    return route.fulfill({ json: { keys } });
  });
  await login(page);
  await page.getByRole('link', { name: 'Members & keys', exact: true }).click();
  await page.getByLabel('Name', { exact: true }).fill('Alice');
  await page.getByLabel('Role', { exact: true }).selectOption('builder');
  await page.getByRole('button', { name: 'Create key', exact: true }).click();
  await expect(page.getByLabel('New API key')).toHaveText(secret);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Copy key' }).click();
  await expect(page.getByRole('status')).toContainText('Key copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(secret);
  await page.getByRole('button', { name: 'Done', exact: true }).click();
  await expect(page.getByLabel('New API key')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('row', { name: /Alice.*Builder.*All projects.*Never.*Active.*Revoke/ })).toBeVisible();
  await expect(page.getByText(secret, { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test('revoking a key requires confirmation and updates its status', async ({ page }) => {
  let revoked = false;
  let deletes = 0;
  await page.route('**/v1/team/keys**', route => {
    if (route.request().method() === 'DELETE') {
      expect(route.request().headers()['x-csrf-token']).toBe('csrf-fixture');
      revoked = true; deletes++;
      return route.fulfill({ json: {} });
    }
    return route.fulfill({ json: { keys: [{ key_id: 'key-1', name: 'Alice', role: 'viewer', project: null, last_used_at: null, expires_at: null, revoked_at: revoked ? 1791316800 : null }] } });
  });
  await login(page);
  await page.getByRole('link', { name: 'Members & keys', exact: true }).click();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: 'Revoke Alice' }).click();
  expect(deletes).toBe(0);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Revoke Alice' }).click();
  await expect(page.getByRole('cell', { name: 'Revoked', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Revoke Alice' })).toHaveCount(0);
  expect(deletes).toBe(1);
});

test('viewers cannot open key management', async ({ page }) => {
  await login(page, 'viewer');
  await expect(page.getByRole('link', { name: 'Members & keys' })).toHaveCount(0);
  await page.goto('/console/#keys');
  await expect(page.getByRole('heading', { name: 'Team admin access required' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create key' })).toHaveCount(0);
});

test('a stale session can be replaced after reload using its CSRF cookie', async ({ page }) => {
  await page.context().addCookies([
    { name: 'aw_session', value: 'revoked-session', url: 'http://127.0.0.1:4175', httpOnly: true },
    { name: 'aw_csrf', value: 'csrf-fixture', url: 'http://127.0.0.1:4175' },
  ]);
  const request = page.waitForRequest(value => value.url().endsWith('/v1/auth/session') && value.method() === 'POST');
  await login(page);
  expect((await request).headers()['x-csrf-token']).toBe('csrf-fixture');
  await expect(page.getByRole('region', { name: 'Signed in as' })).toHaveText(/demo\s*Admin/);
});

test('company sign-in failures return as a readable message and a clean URL', async ({ page }) => {
  await page.goto('/console/?signin_error=rejected#runs');
  await expect(page.getByRole('alert')).toContainText('not linked to a team');
  await expect(page).toHaveURL(/\/console\/#runs$/);
  await expect(page.getByLabel('API key', { exact: true })).toBeVisible();
});
