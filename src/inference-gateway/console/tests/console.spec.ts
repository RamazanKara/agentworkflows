import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import type { CostRow, Run, TeamSettings, TeamSSO } from '../src/api';

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

const ssoPolicy = (): TeamSSO => ({
  team_id: 'demo', enabled: true, provider_name: 'idp.example', role_source: 'groups',
  team_claim: 'team', project_claim: 'project', role_claim: null, default_role: null,
  groups_claim: 'groups', group_role_mappings: { 'Engineering builders': 'builder', 'Engineering reviewers': 'approver' },
});

for (const width of [360, 393, 1440]) {
  test(`company group access is readable and refreshes at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    let policy = ssoPolicy();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/v1/team/sso', route => route.fulfill({ json: policy }));
    await login(page, 'admin', '/console/#keys');
    await expect(page).toHaveTitle('Members & keys · AgentWorkflows Console');
    const panel = page.getByRole('region', { name: 'Company sign-in', exact: true });
    await expect(panel.getByRole('rowheader', { name: 'Engineering builders' })).toBeVisible();
    await expect(panel.getByRole('cell', { name: /Approver$/ })).toBeVisible();
    await expect(panel).toContainText('matching groups grant different roles, sign-in is denied');
    await expect(panel.getByRole('link', { name: 'Company sign-in setup' })).toHaveAttribute('href', /#sso-group-to-role-mapping$/);
    policy = { ...policy, group_role_mappings: { ['<script>unsafe</script>' + 'x'.repeat(100)]: 'viewer' } };
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(panel.getByRole('cell', { name: /Viewer$/ })).toBeVisible();
    await expect(panel.locator('script')).toHaveCount(0);
    await expect(panel).not.toContainText('Engineering builders');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });
}

test('company sign-in explains unmapped teams and legacy role claims', async ({ page }) => {
  let policy = { ...ssoPolicy(), group_role_mappings: {} };
  await page.route('**/v1/team/sso', route => route.fulfill({ json: policy }));
  await login(page, 'admin', '/console/#keys');
  const panel = page.getByRole('region', { name: 'Company sign-in', exact: true });
  await expect(panel).toContainText('No groups are mapped for this team. Company sign-in is denied');
  policy = { ...policy, role_source: 'claim', groups_claim: null, role_claim: 'access_role', default_role: 'viewer' };
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(panel).toContainText('Roles come from the access_role claim, with Viewer when it is absent.');
  await expect(panel).not.toContainText('No groups are mapped');
});

test('company sign-in policy errors can be retried without hiding key management', async ({ page }) => {
  let unavailable = true;
  await page.route('**/v1/team/sso', route => unavailable
    ? route.fulfill({ status: 503, json: { detail: 'Sign-in policy unavailable' } })
    : route.fulfill({ json: ssoPolicy() }));
  await login(page, 'admin', '/console/#keys');
  await expect(page.getByRole('alert')).toContainText('Sign-in policy unavailable');
  await expect(page.getByRole('heading', { name: 'Create a key' })).toBeVisible();
  unavailable = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('rowheader', { name: 'Engineering builders' })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('non-admins cannot open company group access', async ({ page }) => {
  let requests = 0;
  await page.route('**/v1/team/sso', route => { requests++; return route.fulfill({ json: ssoPolicy() }); });
  await login(page, 'viewer', '/console/#keys');
  await expect(page.getByRole('heading', { name: 'Team admin access required' })).toBeVisible();
  expect(requests).toBe(0);
});

test('viewer exports CSV from Costs and can retry a failed download', async ({ page }) => {
  const csv = 'team_id,name,cost_usd\r\ndemo,"Grüße, team",0.000000001\r\n';
  let attempts = 0;
  await page.route('**/v1/usage/export', route => {
    expect(route.request().headers().accept).toBe('text/csv');
    expect(route.request().headers().cookie).toContain('aw_session=opaque-session');
    return ++attempts === 1 ? route.fulfill({ status: 503, json: { detail: 'Usage unavailable' } })
      : route.fulfill({ body: csv, contentType: 'text/csv;charset=utf-8' });
  });
  await login(page, 'viewer', '/console/#costs');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  await expect(page.getByRole('alert')).toContainText('Usage unavailable');
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe('usage.csv');
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(Buffer.concat(chunks).toString('utf8')).toBe(csv);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

for (const width of [393, 1440]) {
  test(`trigger history links to paged runs and receipts at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('**/v1/workflow-triggers', route => route.fulfill({ json: { triggers: [
      { workflow: 'ResearchWorkflow', name: 'daily', kind: 'cron', project: 'default', cron: '0 9 * * *', paused: false },
    ] } }));
    const queries: URLSearchParams[] = [];
    await page.route('**/v1/workflow-runs?*', route => {
      const query = new URL(route.request().url()).searchParams;
      queries.push(query);
      return route.fulfill({ json: { runs: query.has('cursor') ? [run()] : [], next_cursor: query.has('cursor') ? null : 'older' } });
    });
    await page.route(`**/v1/workflow-runs/${id}`, route => route.fulfill({ json: run({ trigger: { kind: 'cron', name: 'daily' } }) }));
    await login(page, 'viewer', '/console/#triggers');
    await page.getByRole('link', { name: 'Run history for daily' }).click();
    await expect(page.getByText('Started by trigger')).toBeVisible();
    await page.getByRole('button', { name: 'Load more' }).click();
    await expect(page.getByRole('link', { name: 'Research', exact: true })).toBeVisible();
    expect(queries.map(query => query.get('trigger'))).toEqual(['daily', 'daily']);
    expect(queries.map(query => query.get('workflow'))).toEqual(['ResearchWorkflow', 'ResearchWorkflow']);
    expect(queries.map(query => query.get('project'))).toEqual(['default', 'default']);
    expect(queries[1].get('cursor')).toBe('older');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('link', { name: 'Research', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
    await page.getByRole('link', { name: 'Schedule: daily' }).click();
    await expect(page.getByText('Started by trigger')).toBeVisible();
    await page.getByRole('link', { name: 'All workflow runs', exact: true }).click();
    await expect(page.getByText('Started by trigger')).toHaveCount(0);
    await expect.poll(() => queries.at(-1)?.get('trigger')).toBeNull();
  });
}
const settings = (): TeamSettings => ({
  revision: 0, updated_by: null, updated_at: null, routes: ['demo-openai', 'demo-anthropic'],
  providers: ['openai', 'anthropic'], approver_roles: ['admin', 'approver'], fields: {
    cost_limit_usd: { value: 50, policy_default: 50, source: 'policy' },
    soft_cost_limit_usd: { value: null, policy_default: null, source: 'policy' },
    capture_content: { value: 'none', policy_default: 'none', source: 'policy' },
    'project_budgets.default': { value: null, policy_default: null, source: 'policy' },
    'workflows.ResearchWorkflow.token_limit': { value: 10000, policy_default: 10000, source: 'policy' },
    'workflows.ResearchWorkflow.cost_limit_usd': { value: 5, policy_default: 5, source: 'policy' },
    'workflows.ResearchWorkflow.approval_required': { value: true, policy_default: true, source: 'policy' },
    'workflows.ResearchWorkflow.approval_threshold_usd': { value: 0, policy_default: 0, source: 'policy' },
      'workflows.ResearchWorkflow.approver_role': { value: 'approver', policy_default: 'approver', source: 'policy' },
      'workflows.ResearchWorkflow.required_approvals': { value: 1, policy_default: 1, source: 'policy' },
      'workflows.ResearchWorkflow.approval_timeout_seconds': { value: 604800, policy_default: 604800, source: 'policy' },
    'workflows.ResearchWorkflow.allowed_providers': { value: ['openai', 'anthropic'], policy_default: ['openai', 'anthropic'], source: 'policy' },
    'workflows.ResearchWorkflow.capture_content': { value: 'none', policy_default: 'none', source: 'policy' },
    'model_routes.research': { value: 'demo-openai', policy_default: 'demo-openai', source: 'policy' },
  },
});

const auditEntry = (sequence = 1) => ({
  id: `1791316800000-${sequence}`, chain_id: 'gateway:test', sequence, team_sequence: sequence,
  record_hash: 'a'.repeat(64), view_prev_hash: 'b'.repeat(64), view_hash: 'c'.repeat(64),
  event: { event: 'inference_request', ts: 1791316800, actor: 'alice', project: 'default', workflow_run_id: id,
    record_hash: 'a'.repeat(64), detail: '<script>throw new Error("unsafe")</script>' },
});

test('audit list expands escaped JSON and links to the run', async ({ page }) => {
  await page.route('**/v1/team/audit?*', route => route.fulfill({ json: { enabled: true, events: [auditEntry()], next_cursor: null } }));
  await login(page);
  await page.getByRole('link', { name: 'Audit log', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'alice', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Model call', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: id.slice(0, 8) })).toHaveAttribute('href', `#run/${id}`);
  await page.getByRole('button', { name: 'Show JSON' }).click();
  await expect(page.locator('pre')).toHaveText(JSON.stringify(auditEntry().event, null, 2));
  await expect(page.locator('pre script')).toHaveCount(0);
  await page.getByRole('button', { name: 'Hide JSON' }).click();
  await expect(page.locator('pre')).toHaveCount(0);
});

test('audit actors use current key display names and label unnamed keys without IDs', async ({ page }) => {
  await page.route('**/v1/team/keys', route => route.fulfill({ json: { keys: [{ key_id: 'worker-key-123', name: 'Research worker' }] } }));
  await page.route('**/v1/team/audit?*', route => route.fulfill({ json: { enabled: true, events: [
    { ...auditEntry(1), event: { ...auditEntry().event, principal: { key_id: 'worker-key-123', name: 'Previous key name' } } },
    { ...auditEntry(2), event: { ...auditEntry().event, principal: { key_id: 'retired-key-456' } } },
  ], next_cursor: null } }));
  await login(page, 'admin', '/console/#audit');
  await expect(page.locator('td[data-label="Actor"]')).toHaveText(['Research workerAPI key', 'Unnamed keyAPI key']);
  await expect(page.locator('.audit-table')).not.toContainText(/worker-key|retired-key|Previous key name/);
});

test('audit filters and cursor pagination use the applied range', async ({ page }) => {
  const queries: URLSearchParams[] = [];
  await page.route('**/v1/team/audit?*', route => {
    const params = new URL(route.request().url()).searchParams;
    queries.push(params);
    return route.fulfill({ json: { enabled: true, events: [auditEntry(params.has('cursor') ? 1 : 2)], next_cursor: params.has('cursor') ? null : '1791316800000-2' } });
  });
  await login(page, 'admin', '/console/#audit');
  await page.getByLabel('From', { exact: true }).fill('2026-10-01T10:00');
  await page.getByLabel('To', { exact: true }).fill('2026-10-09T10:00');
  await page.getByLabel('Actor', { exact: true }).fill('alice');
  await page.getByLabel('Event type', { exact: true }).selectOption('inference_request');
  await page.getByLabel('Project', { exact: true }).fill('default');
  await page.getByLabel('Run ID', { exact: true }).fill(id);
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect.poll(() => queries.at(-1)?.get('actor')).toBe('alice');
  const filtered = queries.at(-1)!;
  expect(Number(filtered.get('from'))).toBeGreaterThan(0);
  expect(Number(filtered.get('to'))).toBeGreaterThan(Number(filtered.get('from')));
  expect(filtered.get('event_type')).toBe('inference_request');
  expect(filtered.get('project')).toBe('default');
  expect(filtered.get('run_id')).toBe(id);
  await page.getByRole('button', { name: 'Older events' }).click();
  await expect.poll(() => queries.at(-1)?.get('cursor')).toBe('1791316800000-2');
  expect(queries.at(-1)?.get('from')).toBe(filtered.get('from'));
});

for (const ok of [true, false]) {
  test(`audit verification shows ${ok ? 'success and range boundary' : 'the first break'}`, async ({ page }) => {
    await page.route('**/v1/team/audit?*', route => route.fulfill({ json: { enabled: true, events: [auditEntry()], next_cursor: null } }));
    await page.route('**/v1/team/audit/verify?*', route => {
      expect(new URL(route.request().url()).searchParams.has('actor')).toBe(false);
      return route.fulfill({ json: { enabled: true, ok, checked: 7,
        first_break: ok ? null : { chain_id: 'gateway:test', sequence: 8, reason: 'record_hash_mismatch' },
        boundaries: ok ? [{ chain_id: 'gateway:test', sequence: 2, reason: 'retained_range_start' }] : [],
      } });
    });
    await login(page, 'admin', '/console/#audit');
    await page.getByRole('button', { name: 'Verify chain' }).click();
    await expect(page.getByRole('heading', { name: ok ? 'Chain verified' : 'Chain break found' })).toBeVisible();
    await expect(page.getByText(ok ? '7 events checked. None were changed' : '6 events passed before the break.')).toBeVisible();
    await expect(page.getByText(ok ? /This check starts at event 2 in chain gateway:test/ : /First break at event 8 in chain gateway:test\. The event no longer matches its stored hash/)).toBeVisible();
  });
}

test('audit export follows every filtered page and writes original events as JSON Lines', async ({ page }) => {
  await page.route('**/v1/team/audit?*', route => {
    const params = new URL(route.request().url()).searchParams;
    const exporting = params.get('limit') === '200';
    if (exporting) expect(params.get('actor')).toBe('alice');
    return route.fulfill({ json: { enabled: true, events: [auditEntry(params.has('cursor') ? 1 : 2)],
      next_cursor: exporting && !params.has('cursor') ? '1791316800000-2' : null,
    } });
  });
  await login(page, 'admin', '/console/#audit');
  await page.getByLabel('Actor', { exact: true }).fill('alice');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON Lines' }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe('audit-log.jsonl');
  const stream = await download.createReadStream();
  let content = '';
  for await (const chunk of stream!) content += chunk.toString();
  expect(content.trim().split('\n').map(line => JSON.parse(line))).toEqual([auditEntry(2).event, auditEntry(1).event]);
});

test('audit disabled view explains how to enable it', async ({ page }) => {
  await page.route('**/v1/team/audit?*', route => route.fulfill({ json: { enabled: false, events: [], next_cursor: null,
    message: 'Set SANDBOX_BUDGET_BACKEND=redis and AUDIT_LOG_ENABLED=true.',
  } }));
  await login(page, 'admin', '/console/#audit');
  await expect(page.getByRole('heading', { name: 'Audit log is turned off' })).toBeVisible();
  await expect(page.getByText(/SANDBOX_BUDGET_BACKEND=redis/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply filters' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Verify chain' })).toHaveCount(0);
});

test('leaving the audit page cancels an in-progress export', async ({ page }) => {
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => { release = resolve; });
  const downloads: string[] = [];
  page.on('download', download => downloads.push(download.suggestedFilename()));
  await page.route('**/v1/team/audit?*', async route => {
    if (new URL(route.request().url()).searchParams.get('limit') === '200') await pending;
    await route.fulfill({ json: { enabled: true, events: [auditEntry()], next_cursor: null } });
  });
  await login(page, 'admin', '/console/#audit');
  const request = page.waitForRequest(value => value.url().includes('/v1/team/audit?') && value.url().includes('limit=200'));
  await page.getByRole('button', { name: 'Export JSON Lines' }).click();
  await request;
  const cancelled = page.waitForEvent('requestfailed', { predicate: value => value.url().includes('limit=200') });
  await page.getByRole('link', { name: 'Workflow runs', exact: true }).click();
  await cancelled;
  release();
  await expect(page.getByRole('heading', { name: 'Workflow runs', exact: true })).toBeVisible();
  expect(downloads).toEqual([]);
});

for (const role of ['builder', 'approver', 'viewer']) {
  test(`audit is hidden from ${role}, including direct navigation`, async ({ page }) => {
    let requests = 0;
    await page.route('**/v1/team/audit**', route => { requests++; return route.fulfill({ status: 403, json: {} }); });
    await login(page, role, '/console/#audit');
    await expect(page.getByRole('link', { name: 'Audit log', exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Team admin access required' })).toBeVisible();
    expect(requests).toBe(0);
  });
}

test('schema forms render all supported field types and submit typed values', async ({ page }) => {
  await page.route('**/v1/workflow-policies', route => route.fulfill({ json: { workflows: { FormWorkflow: {
    allowedModels: [], allowedProviders: [], tokenLimit: 1000, costLimitUsd: 1,
    inputSchema: { type: 'object', description: 'A schema supplied by the team.', properties: {
      title: { type: 'string', description: 'Name this request', examples: ['Example title'] },
      body: { type: 'string', examples: ['First line\nSecond line'] },
      count: { type: 'integer', default: 10000, minimum: 1, maximum: 20000 },
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
  await expect(page.getByLabel('Count', { exact: true })).toHaveValue('10,000');
  await page.getByRole('button', { name: 'Start run' }).click();
  expect(submissions).toHaveLength(0);
  await page.getByLabel('Title', { exact: true }).fill('Form test');
  await expect(page.getByLabel('Price', { exact: true })).toBeHidden();
  await page.getByText('More options', { exact: true }).click();
  await expect(page.getByLabel('Body', { exact: true })).toHaveJSProperty('tagName', 'TEXTAREA');
  await page.getByLabel('Body', { exact: true }).fill('<script>untrusted</script>\nSecond line');
  await page.getByLabel('Count', { exact: true }).fill('3.5');
  await page.getByRole('button', { name: 'Start run' }).click();
  expect(submissions).toHaveLength(0);
  await page.getByLabel('Count', { exact: true }).fill('12,345');
  await page.getByLabel('Price', { exact: true }).fill('2.25');
  await page.getByLabel('Mode', { exact: true }).selectOption({ label: 'thorough' });
  await page.getByLabel('Tags', { exact: true }).fill('one\nthree');
  await page.getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByRole('heading', { name: 'Step timeline' })).toBeVisible();
  expect(submissions[0].input).toEqual({ title: 'Form test', body: '<script>untrusted</script>\nSecond line', count: 12345, price: 2.25, enabled: false, mode: 'thorough', priority: 2, tags: ['one', 'three'] });
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

async function login(page: Page, token = 'admin', path = '/console/#start') {
  await page.goto(path);
  await page.getByLabel('API key', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Signed in as' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  let identity = '';
  const currentSettings = settings();
  await page.route('**/v1/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/v1/auth/config') return route.fulfill({ json: { api_key: true, jwt: true, oidc: { enabled: false } } });
    if (url.pathname === '/v1/team/sso') return route.fulfill({ json: { ...ssoPolicy(), enabled: false } });
    if (url.pathname === '/v1/team/keys') return route.fulfill({ json: { keys: [] } });
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
    if (url.pathname.startsWith('/v1/team/settings')) {
      if (role !== 'admin') return route.fulfill({ status: 403, json: { detail: { reason: 'team_role_required' } } });
      const method = route.request().method();
      if (method !== 'GET') {
        expect(route.request().headers()['if-match']).toBe(String(currentSettings.revision));
        if (method === 'PATCH') {
          for (const [field, value] of Object.entries(route.request().postDataJSON().fields)) currentSettings.fields[field] = { ...currentSettings.fields[field], value: value as TeamSettings['fields'][string]['value'], source: 'override' };
        } else {
          const field = decodeURIComponent(url.pathname.slice('/v1/team/settings/'.length));
          currentSettings.fields[field] = { ...currentSettings.fields[field], value: currentSettings.fields[field].policy_default, source: 'policy' };
        }
        currentSettings.revision++;
        currentSettings.updated_by = 'admin'; currentSettings.updated_at = 1791316800;
      }
      return route.fulfill({ json: currentSettings });
    }
    if (token === 'invalid') return route.fulfill({ status: 401, json: { detail: { message: 'Invalid credential. Ask your team admin for a valid key.' } } });
    let body: unknown;
    if (url.pathname === '/v1/team') body = { team_id: team, role, projects: ['default', 'engineering'], providers: ['openai'], cost_limit_usd: 50, ...(role === 'admin' ? { provider_configuration: { openai: { configured: true, environment_variable: 'TEAM_OPENAI_KEY' } } } : {}) };
    else if (url.pathname === '/v1/workflow-policies') body = policies;
    else if (url.pathname === '/v1/models') body = { data: [{ id: 'demo-openai' }] };
    else if (url.pathname === '/v1/sandbox/budget') body = { usage: { estimated_tokens: 63 }, limits: { estimated_tokens: 200000 }, window_seconds: 86400 };
    else if (url.pathname === '/v1/team/spend') body = { team_id: team, window_start: 1790812800, window_end: 1793491200, soft_limit_usd: null, hard_limit_usd: 50, reserved_and_spent_usd: .0432, status: 'ok', alerts: [] };
    else if (url.pathname === '/v1/usage') body = { estimated_cost: .0432, spend: { project: null, window_start: 1791316800, window_seconds: 86400, cost_limit_usd: 50, reserved_and_spent_usd: .0432, providers: { anthropic: costs }, workflows: { ResearchWorkflow: costs }, accounting: 'Configured prices, not provider invoices.' } };
    else if (url.pathname === '/v1/workflow-runs' && route.request().method() === 'POST') body = { run_id: id };
    else if (url.pathname === '/v1/workflow-runs') body = { runs: team === 'other' || url.searchParams.get('project') === 'engineering' || url.searchParams.get('status') === 'failed' ? [] : [run()], next_cursor: null };
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
  await expect(page.locator('.transcript .prose').last()).toHaveText('Compare <b>two</b> vendors.');
  await expect(page.locator('.step-content').last()).toHaveText('Vendor A wins on cost.');
});

for (const width of [360, 393, 1440]) {
  test(`release console layouts at ${width}px`, async ({ page }) => {
    test.setTimeout(60000);
    await page.setViewportSize({ width, height: width < 760 ? 852 : 1000 });
    await page.clock.setFixedTime(new Date('2026-10-09T10:00:00Z'));
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const capture = async (name: string) => {
      await expect(page.locator('main .loading')).toHaveCount(0);
      await expect(page).toHaveTitle(/ · AgentWorkflows Console$/);
      await expect(page.locator('main h1')).toBeVisible();
      await expect(page.locator('vite-error-overlay')).toHaveCount(0);
      await expect(page.locator('main')).not.toContainText(/aaaaaaaa|demo-openai|demo-anthropic|example\.com/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator('.dot-list').evaluateAll(lists => lists.every(list => list.scrollWidth <= list.clientWidth))).toBe(true);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      const splitWords = await page.locator('main').evaluate(main => {
        const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
        const split: string[] = [];
        while (walker.nextNode()) {
          const node = walker.currentNode;
          if (node.parentElement?.closest('pre, code, textarea, select, .copy-field, .visually-hidden')) continue;
          const header = node.parentElement?.closest('thead');
          if (header && getComputedStyle(header).clipPath !== 'none') continue;
          for (const match of (node.textContent || '').matchAll(/[\p{L}\p{N}]{4,}/gu)) {
            const range = document.createRange();
            range.setStart(node, match.index); range.setEnd(node, match.index + match[0].length);
            if (range.getClientRects().length > 1) split.push(match[0]);
          }
        }
        return split;
      });
      expect(splitWords, `${name}: words must wrap at spaces`).toEqual([]);
      await page.screenshot({ path: `../../../.out/console-v1.0.0-rc.1/${width}-${name}.png`, fullPage: true });
    };
    const runId = '7e2b941c-65af-4d83-9c12-34b08f6ea725';
    const started = Date.parse('2026-10-09T09:40:00Z') / 1000;
    const models = [{ id: 'gpt-4.1-mini', owned_by: 'openai' }, { id: 'claude-haiku-4-5', owned_by: 'anthropic' }];
    const draft = '# Briefing: Agent workflow evaluation\n\nStart with a small set of representative tasks. Compare answer quality, cost and review effort before expanding access.';
    const timeline = [
      { step_id: 'research', action: 'tool_call', provider: 'tool', model: '', tool: 'research', tokens: 0, cost_usd: .01, duration_ms: 320,
        input: JSON.stringify({ topic: 'Agent workflow evaluation' }),
        output: JSON.stringify({ sources: [{ title: 'Evaluation guide', url: 'https://docs.insights.internal/research/agent-workflows/evaluation-and-human-approvals' }] }) },
      { step_id: 'analyze', action: 'model_call', provider: 'openai', model: models[0].id, tool: '', tokens: 1800, cost_usd: .02, duration_ms: 1420,
        input: JSON.stringify([{ role: 'user', content: 'Compare practical ways to evaluate agent workflows for our team.' }]),
        output: 'Track task completion, answer quality, cost per run and the time reviewers spend correcting drafts.' },
      { step_id: 'draft', action: 'model_call', provider: 'anthropic', model: models[1].id, tool: '', tokens: 2400, cost_usd: .03, duration_ms: 1860,
        input: JSON.stringify([{ role: 'user', content: 'Draft a short briefing from the evaluation findings for human review.' }]), output: draft },
    ].map(({ input, output, ...step }, index) => {
      const receipt = { event: step.tool ? 'agent_action' : 'inference_request', ts: started + index * 4 + 2,
        action_type: step.action, provider: step.provider, model: step.model, workflow_step_id: step.step_id,
        workflow_run_id: runId, project: 'default', principal: { key_id: 'key-research-worker', name: 'Research worker' },
        tokens: step.tokens, cost_usd: step.cost_usd };
      const hash = createHash('sha256').update(JSON.stringify(receipt)).digest('hex');
      return { ...step, timestamp: receipt.ts, status_code: 200, attempts: [], chain_id: 'gateway:insights', receipt_id: hash,
        receipt: { ...receipt, record_hash: hash }, content: { input, output, redaction: 'redacted' as const, truncated: { input: false, output: false } } };
    });
    const totals = { calls: timeline.length, tokens: timeline.reduce((sum, step) => sum + step.tokens, 0), cost_usd: timeline.reduce((sum, step) => sum + step.cost_usd, 0) };
    const providers = Object.fromEntries(timeline.map(step => [step.provider, { calls: 1, tokens: step.tokens, cost_usd: step.cost_usd }])) satisfies Record<string, CostRow>;
    const activeRun: Run = { run_id: runId, workflow: 'ResearchWorkflow', project: 'default', created_at: started, status: 'running',
      progress: { stage: 'awaiting_approval', draft, required_approvals: 2, approved_by: [], expires_at: '2026-10-09T10:40:00Z', approver_role: 'approver' },
      trigger: { kind: 'cron', name: 'morning-briefing' }, budget: { ...totals, token_limit: 10000, cost_limit_usd: 5 }, timeline };
    const teamSettings = settings();
    teamSettings.routes = models.map(model => model.id);
    teamSettings.fields['model_routes.research'] = { value: models[0].id, policy_default: models[0].id, source: 'policy' };
    teamSettings.fields['soft_cost_limit_usd'] = { value: 40, policy_default: 40, source: 'policy' };
    teamSettings.fields['capture_content'] = { value: 'redacted', policy_default: 'redacted', source: 'policy' };
    teamSettings.fields['workflows.ResearchWorkflow.capture_content'] = { value: 'redacted', policy_default: 'redacted', source: 'policy' };
    teamSettings.fields['workflows.ResearchWorkflow.required_approvals'] = { value: 2, policy_default: 2, source: 'policy' };
    teamSettings.fields['workflows.ResearchWorkflow.approval_timeout_seconds'] = { value: 3600, policy_default: 3600, source: 'policy' };
    let openaiConfigured = false;
    let anthropicConfigured = false;
    let recorded = false;
    let signedIn = false;
    await page.route('**/v1/auth/config', route => route.fulfill({ json: { api_key: true, jwt: true, oidc: { enabled: true, provider_name: 'identity.insights.internal', login_url: '/v1/auth/login' } } }));
    await page.route('**/v1/auth/session', async route => {
      if (route.request().method() === 'POST') {
        signedIn = true;
        await page.context().addCookies([
          { name: 'aw_session', value: 'opaque-session', url: 'http://127.0.0.1:4175', httpOnly: true, sameSite: 'Lax' },
          { name: 'aw_csrf', value: 'csrf-fixture', url: 'http://127.0.0.1:4175', sameSite: 'Lax' },
        ]);
      }
      return route.fulfill(signedIn ? { json: { csrf_token: 'csrf-fixture', principal: { key_id: 'admin', name: 'Maya Chen' } } } : { status: 401, json: {} });
    });
    await page.route('**/v1/team/sso', route => route.fulfill({ json: { ...ssoPolicy(), team_id: 'insights', provider_name: 'identity.insights.internal' } }));
    await page.route('**/v1/team/spend', route => route.fulfill({ json: {
      team_id: 'insights', window_start: 1790812800, window_end: 1793491200,
      soft_limit_usd: 40, hard_limit_usd: 50, reserved_and_spent_usd: recorded ? totals.cost_usd : 0, status: 'ok', alerts: [],
    } }));
    await page.route('**/v1/workflow-triggers', route => route.fulfill({ json: { triggers: [
      { workflow: 'ResearchWorkflow', name: 'morning-briefing', kind: 'cron', project: 'default', cron: '40 9 * * *', paused: false, configuration_paused: false, next_fire_at: ['2026-10-10T09:40:00Z'] },
      { workflow: 'ResearchWorkflow', name: 'requested-briefing', kind: 'webhook', project: 'default', paused: false, configuration_paused: false, secret_configured: true, url: '/v1/hooks/insights/ResearchWorkflow/requested-briefing' },
    ] } }));
    const csvRows = [['total', 'all', totals], ...Object.entries(providers).map(([name, row]) => ['provider', name, row]), ['workflow', 'ResearchWorkflow', totals]] as [string, string, CostRow][];
    const csv = 'team_id,project,period_start,period_end,dimension,name,calls,tokens,cost_usd,currency\r\n' + csvRows.map(([dimension, name, row]) =>
      `insights,,2026-10-01T00:00:00Z,2026-11-01T00:00:00Z,${dimension},${name},${row.calls},${row.tokens},${row.cost_usd!.toFixed(9)},USD\r\n`).join('');
    await page.route('**/v1/usage/export', route => route.fulfill({ body: csv, contentType: 'text/csv;charset=utf-8' }));
    await page.route('**/v1/team', route => route.fulfill({ json: {
      team_id: 'insights', role: 'admin', projects: ['default'], providers: ['openai', 'anthropic'], cost_limit_usd: 50,
      provider_configuration: { openai: { configured: openaiConfigured, environment_variable: 'OPENAI_API_KEY' }, anthropic: { configured: anthropicConfigured, environment_variable: 'ANTHROPIC_API_KEY' } },
    } }));
    await page.route('**/v1/models', route => route.fulfill({ json: { data: models } }));
    await page.route('**/v1/team/settings', route => route.fulfill({ json: teamSettings }));
    await page.route('**/v1/workflow-policies', route => route.fulfill({ json: { workflows: { ResearchWorkflow: {
      ...policies.workflows.ResearchWorkflow, allowedModels: models.map(model => model.id),
      inputSchema: { type: 'object', properties: { topic: { type: 'string', default: 'Agent workflow evaluation' }, model: { type: 'string', default: models[0].id } }, required: ['topic'] },
    } } } }));
    await page.route('**/v1/workflow-runs?*', route => route.fulfill({ json: { runs: recorded ? [activeRun] : [], next_cursor: null } }));
    await page.route(`**/v1/workflow-runs/${runId}`, route => route.fulfill({ json: activeRun }));
    await page.route('**/v1/sandbox/budget', route => route.fulfill({ json: { usage: { estimated_tokens: recorded ? totals.tokens : 0 }, limits: { estimated_tokens: 200000 }, window_seconds: 86400 } }));
    await page.route('**/v1/usage', route => route.fulfill({ json: { estimated_cost: recorded ? totals.cost_usd : 0, spend: {
      period: 'month', project: null, window_start: Date.parse('2026-10-01T00:00:00Z') / 1000, window_seconds: 2678400,
      cost_limit_usd: 50, reserved_and_spent_usd: recorded ? totals.cost_usd : 0, providers: recorded ? providers : {}, workflows: recorded ? { ResearchWorkflow: totals } : {},
    } } }));
    await page.route('**/v1/team/keys', route => route.fulfill({ json: { keys: [
      { key_id: 'admin', name: 'Maya Chen', role: 'admin', project: null, created_at: started - 86400, last_used_at: started + 1200, expires_at: null, revoked_at: null },
      { key_id: 'key-research-worker', name: 'Research worker', role: 'builder', project: 'default', created_at: started - 3600, last_used_at: timeline.at(-1)!.timestamp, expires_at: started + 90 * 86400, revoked_at: null },
    ] } }));
    await page.route('**/v1/team/audit?*', route => route.fulfill({ json: { enabled: true, events: timeline.map((step, index) => ({
      id: `${step.timestamp * 1000}-0`, chain_id: step.chain_id, sequence: index + 1, event: step.receipt,
    })).reverse(), next_cursor: null } }));
    await page.goto('/console/');
    await expect(page.getByRole('button', { name: 'Sign in with your company account' })).toBeVisible();
    await page.screenshot({ path: `../../../.out/console-v1.0.0-rc.1/${width}-sso-sign-in.png`, fullPage: true });
    await login(page);
    await expect(page.locator('.identity .team')).toHaveText('Insights');
    await expect(page.locator('.identity .who')).toHaveText('Maya Chen');
    page.on('console', message => { if (['error', 'warning'].includes(message.type())) errors.push(message.text()); });
    const labels = page.locator('.model-list li > span.muted');
    await expect(labels).toHaveText(['OpenAI', 'Anthropic']);
    const styles = await labels.evaluateAll(nodes => nodes.map(node => {
      const style = getComputedStyle(node); return [style.color, style.fontSize, style.fontWeight];
    }));
    expect(styles[0]).toEqual(styles[1]);
    await expect(page.locator('.model-list code')).toHaveText(models.map(model => model.id));
    await expect(page.locator('.model-list .badge')).toHaveText(['Key missing', 'Key missing']);
    if (width < 760) {
      const chips = await page.locator('.model-list li').evaluateAll(nodes => nodes.map(node => {
        const provider = node.querySelector('.muted')!.getBoundingClientRect();
        const badge = node.querySelector('.badge')!.getBoundingClientRect();
        return { height: node.getBoundingClientRect().height, together: provider.top < badge.bottom && badge.top < provider.bottom };
      }));
      expect(chips[0].height).toBe(chips[1].height);
      expect(chips.every(chip => chip.together)).toBe(true);
      const menu = await page.getByRole('button', { name: 'Open menu' }).boundingBox();
      const brand = await page.locator('.sidebar .brand').boundingBox();
      expect(Math.abs(menu!.y + menu!.height / 2 - brand!.y - brand!.height / 2)).toBeLessThanOrEqual(1);
    }
    const numberStyle = await page.locator('.onboarding-number').first().evaluate(node => {
      const style = getComputedStyle(node); return [style.fontFamily, style.fontVariantNumeric, style.color, style.fontWeight];
    });
    await capture('get-started');
    await page.goto('/console/#providers');
    const helm = page.locator('.setup pre');
    await expect(helm).toContainText('# Release and namespace are "aw", as in\n# the install guide. Change if needed.');
    await expect(page.getByText('Tokens in this 24-hour window', { exact: true })).toBeVisible();
    if (width < 760) {
      await expect(helm).toHaveCSS('white-space', 'pre');
      await expect(helm).toHaveCSS('overflow-wrap', 'normal');
      expect(await helm.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
      expect(await helm.locator('..').evaluate(node => getComputedStyle(node, '::after').backgroundImage)).toContain('linear-gradient');
      await helm.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      expect(await helm.evaluate(node => node.scrollLeft + node.clientWidth >= node.scrollWidth - 1)).toBe(true);
      await helm.evaluate(node => { node.scrollLeft = 0; });
      for (const provider of ['openai', 'anthropic']) await expect(helm).toContainText(`existingSecret=${provider}-api-key`);
    }
    await capture('providers');
    openaiConfigured = true;
    await page.goto('/console/#start');
    await page.reload();
    await expect(page.locator('.model-list .badge')).toHaveText(['Ready', 'Key missing']);
    await expect(page.locator('.onboarding .note-warn a')).toHaveCSS('white-space', 'nowrap');
    await capture('get-started-key-warning');
    anthropicConfigured = true;
    recorded = true;
    await page.reload();
    await expect(page.locator('.model-list .badge')).toHaveText(['Ready', 'Ready']);
    await capture('get-started-ready');
    if (width < 760) {
      await page.getByRole('button', { name: 'Open menu' }).click();
      await expect(page.getByRole('navigation').getByRole('link')).toHaveCount(9);
      await capture('navigation');
      await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Open menu' })).toBeVisible();
    }
    await page.goto('/console/#providers');
    await expect(page.getByText('Key present', { exact: true })).toHaveCount(2);
    expect(await page.locator('.usage-bar > span').first().evaluate(node => node.getBoundingClientRect().width)).toBeGreaterThanOrEqual(4);
    await capture('providers-ready');
    await page.goto('/console/#costs');
    await expect(page.getByRole('heading', { name: 'By provider' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'By workflow table' }).locator('tbody td')).toHaveText(['3', '4,200', '$0.06']);
    await expect(page.getByText('Tokens in this 24-hour window', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Spending period' })).toContainText('This month (UTC)');
    await expect(page.getByRole('region', { name: 'Spending period' })).not.toContainText('24-hour window');
    await expect(page.getByLabel('Research · Token limit per run')).toHaveValue('10,000');
    expect(await page.getByRole('region', { name: 'Spend alerts', exact: true }).evaluate(node => parseFloat(getComputedStyle(node).paddingLeft))).toBeGreaterThanOrEqual(18);
    await capture('costs');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export CSV', exact: true }).click();
    const exported = await download;
    await exported.saveAs(`../../../.out/console-v1.0.0-rc.1/${width}-usage.csv`);
    const stream = await exported.createReadStream();
    const chunks = [];
    for await (const chunk of stream!) chunks.push(chunk);
    expect(Buffer.concat(chunks).toString('utf8')).toBe(csv);
    await capture('csv-export');
    await page.goto('/console/#keys');
    await expect(page.getByRole('rowheader', { name: 'Maya Chen You' })).toBeVisible();
    const revoke = await page.getByRole('button', { name: 'Revoke Research worker', exact: true }).boundingBox();
    const edit = await page.getByRole('button', { name: 'Edit Research worker', exact: true }).boundingBox();
    expect(edit!.x - revoke!.x - revoke!.width).toBeGreaterThanOrEqual(8);
    await capture('members-keys');
    await expect(page.getByRole('region', { name: 'Company sign-in', exact: true })).toContainText('Engineering reviewers');
    await capture('sso-policy');
    await page.getByRole('button', { name: 'Edit Research worker', exact: true }).click();
    await expect(page.getByLabel('Edit name', { exact: true })).toHaveValue('Research worker');
    await capture('edit-key');
    await page.goto('/console/#runs');
    await expect(page.getByRole('link', { name: 'Research', exact: true })).toBeVisible();
    await capture('workflow-runs');
    await page.goto('/console/#triggers');
    await expect(page.getByRole('link', { name: 'Run history for morning-briefing' })).toBeVisible();
    const history = await page.getByRole('link', { name: 'Run history for morning-briefing' }).boundingBox();
    const pause = await page.getByRole('button', { name: 'Pause morning-briefing', exact: true }).boundingBox();
    expect(pause!.x - history!.x - history!.width).toBeGreaterThanOrEqual(8);
    await capture('triggers');
    await page.getByRole('link', { name: 'Run history for morning-briefing' }).click();
    await expect(page.getByText('Started by trigger')).toBeVisible();
    await capture('trigger-history');
    await page.goto('/console/#new/ResearchWorkflow');
    await expect(page.getByLabel('Topic', { exact: true })).toHaveValue('Agent workflow evaluation');
    await capture('template-research');
    await page.goto(`/console/#run/${runId}`);
    await expect(page.locator('.draft h3')).toHaveText('Briefing: Agent workflow evaluation');
    await expect(page.locator('.step-preview h3')).toHaveText('Briefing: Agent workflow evaluation');
    await expect(page.locator('.metrics dd')).toHaveText(['4,200 of 10,000', '$0.06 of $5.00', '3']);
    await expect(page.locator('.step-number')).toHaveText(['01', '02', '03']);
    expect(await page.locator('.step-number').first().evaluate(node => {
      const style = getComputedStyle(node); return [style.fontFamily, style.fontVariantNumeric, style.color, style.fontWeight];
    })).toEqual(numberStyle);
    await page.getByText('Arguments and result', { exact: true }).click();
    const json = page.locator('.step-content.json').last();
    await expect(json).toHaveCSS('white-space', 'pre');
    await expect(json).toHaveCSS('overflow-wrap', 'normal');
    if (width < 760) {
      expect(await json.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
      expect(await json.locator('..').evaluate(node => getComputedStyle(node, '::after').backgroundImage)).toContain('linear-gradient');
      const input = page.locator('.step-content.json').first();
      expect(await input.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await json.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      expect(await json.evaluate(node => node.scrollLeft + node.clientWidth >= node.scrollWidth - 1)).toBe(true);
      await json.evaluate(node => { node.scrollLeft = 0; });
    }
    await capture('run-detail');
    await page.getByText('Prompt and response', { exact: true }).last().click();
    await expect(page.locator('.step-content h3')).toHaveText('Briefing: Agent workflow evaluation');
    await capture('run-step-output');
    await page.goto('/console/#approvals');
    await expect(page.locator('.review h2')).toHaveText('Briefing: Agent workflow evaluation');
    await capture('approvals');
    await page.goto('/console/#team');
    await expect(page.getByLabel('Team monthly budget (USD)', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Model for research')).toHaveValue(models[0].id);
    await expect(page.getByLabel('Model for research').locator('option')).toHaveText(['gpt-4.1-mini · OpenAI', 'claude-haiku-4-5 · Anthropic']);
    await expect(page.locator('.workflow-card > summary .dot-list > span')).toHaveText(['$5.00 per run', 'Approval on every run', 'OpenAI, Anthropic']);
    await expect(page.getByLabel('Research · Token limit per run')).toHaveValue('10,000');
    if (width === 1440) {
      const columns = await page.locator('.workflow-card .card-row').first().evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').filter(size => parseFloat(size) > 0));
      expect(columns).toHaveLength(2);
    }
    await capture('team-settings');
    await capture('spend-limits');
    await page.goto('/console/#audit');
    await expect(page.locator('.audit-table').getByText('Model call', { exact: true })).toHaveCount(2);
    await expect(page.locator('.audit-table').getByText('Agent action', { exact: true })).toHaveCount(1);
    await expect(page.locator('.audit-table td[data-label="Actor"]')).toHaveText(['Research workerAPI key', 'Research workerAPI key', 'Research workerAPI key']);
    await expect(page.locator('.audit-table')).not.toContainText('key-rese');
    await capture('audit');
    const templates = [
      ['ResearchWorkflow', 'research', 'topic', 'Agent workflow evaluation', 'A question or subject, in a sentence.', 'How should our team evaluate AI agents?'],
      ['CodeReviewWorkflow', 'code-review', 'diff', '- const timeout = 1000;\n+ const timeout = 5000;', 'Paste a unified diff (git diff output).', '- return user.is_admin\n+ return True'],
      ['SupportTriageWorkflow', 'support-triage', 'ticket', 'Password reset blocks sign-in.', "Paste the customer's message.", 'I cannot sign in after resetting my password.'],
      ['WeeklyReportWorkflow', 'weekly-report', 'period', '2026-09-28/2026-10-04', 'Start and end dates, as YYYY-MM-DD/YYYY-MM-DD.', '2026-09-28/2026-10-04'],
      ['IncidentSummaryWorkflow', 'incident-summary', 'incident_id', 'INC-1042', 'The ID from your incident tracker.', 'INC-1042'],
      ['DocumentQAWorkflow', 'document-qa', 'question', 'Who can approve workflows?', "A question about your team's documents.", 'Who can approve a workflow, and when does approval expire?'],
    ];
    await page.route('**/v1/workflow-policies', route => route.fulfill({ json: { workflows: Object.fromEntries(templates.map(([workflow, , field, , description, example]) => [workflow, {
      allowedModels: [models[0].id], allowedProviders: ['openai'], tokenLimit: 10000, costLimitUsd: 5,
      inputSchema: { type: 'object', properties: {
        [field]: { type: 'string', description, examples: [example], minLength: 1, pattern: '\\S' },
        model: { type: 'string', default: models[0].id, minLength: 1, pattern: '\\S' },
        ...(workflow === 'ResearchWorkflow' ? { token_limit: { type: 'integer', default: 10000, minimum: 1, maximum: 1000000000 }, cost_limit_usd: { type: 'number', default: 5, exclusiveMinimum: 0, maximum: 1000000 } } : {}),
      }, required: [field], additionalProperties: false },
    }])) } }));
    for (const [workflow, name, field, value] of templates) {
      await page.goto(`/console/#new/${workflow}`);
      await expect(page.getByLabel('Workflow', { exact: true }).locator('option')).toHaveCount(6);
      await page.locator(`[name="input.${field}"]`).fill(value);
      if (workflow === 'CodeReviewWorkflow') await expect(page.getByLabel('Diff', { exact: true })).toHaveJSProperty('tagName', 'TEXTAREA');
      await page.getByRole('heading', { name: 'Run workflow', exact: true }).click();
      await capture(`template-${name}`);
    }
    expect(errors).toEqual([]);
  });
}

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
  await expect(page.getByRole('heading', { name: 'No matching runs' })).toBeVisible();
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
  await expect(page.getByText('OpenAI and Anthropic keys are missing, so runs fail until you add them.', { exact: false })).toBeVisible();
  await expect(page.locator('.model-list li').first()).toContainText('researchOpenAIKey missing');
  await expect(page.locator('.model-list li').last()).toHaveText('anthropicAnthropicKey missing');
  await expect(page.getByText('Compose demo', { exact: false })).toHaveCount(0);
  await page.getByRole('link', { name: 'Run workflow', exact: true }).first().click();
  await expect(page.locator('.note-warn')).toContainText('model calls in this run will fail');
  await page.getByRole('link', { name: 'Providers & budgets', exact: true }).click();
  const setup = page.locator('section.setup');
  await expect(setup.getByRole('heading', { name: 'Connect OpenAI and Anthropic' })).toBeVisible();
  const commands = await setup.locator('pre').innerText();
  for (const provider of ['openai', 'anthropic']) {
    expect(commands).toContain(`secret generic ${provider}-api-key \\\n  -n aw --from-file=api-key=/dev/stdin`);
    expect(commands).toContain(`--set providers.${provider}.existingSecret=${provider}-api-key`);
  }
  expect(commands.match(/helm upgrade/g)).toHaveLength(1);
  // Lines stay short enough for a phone; only the --set lines run longer.
  expect(commands.split('\n').filter(line => line.length > 42 && !line.includes('--set'))).toEqual([]);
  await expect(setup).not.toContainText('Then repeat');
  await expect(page.getByRole('columnheader', { name: 'Environment variable' })).toBeVisible();
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
    return route.fulfill({ json: url.searchParams.get('project') === 'default' ? { runs: [], next_cursor: null } : !url.searchParams.has('cursor') ? { runs: [], next_cursor: 'older' } : { runs: [run({ project: 'engineering' })], next_cursor: null } });
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
  await expect(page.getByRole('link', { name: 'Providers & budgets', exact: true })).toBeVisible();
  await page.goto('/console/#providers');
  await expect(page.getByText('Read-only. Your team admin can change these settings.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save settings' })).toHaveCount(0);
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
  await expect(page.getByRole('region', { name: 'Signed in as' })).toHaveText(/Demo\s*Admin/);
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
  await page.route('**/v1/workflow-runs?*', route => route.fulfill({ json: { runs: [run({ status: 'completed', progress: undefined, outcome: 'rejected' })], next_cursor: null } }));
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
  // Format the expected date in the page, so it uses the same locale and time zone as the console, whatever browser or machine runs the test.
  const revokedOn = await page.evaluate(() => new Date(1791316800 * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }));
  await expect(page.getByRole('cell', { name: /^Revoked/ })).toContainText(revokedOn);
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
  await expect(page.getByRole('region', { name: 'Signed in as' })).toHaveText(/Demo\s*Admin/);
});

test('company sign-in failures return as a readable message and a clean URL', async ({ page }) => {
  await page.goto('/console/?signin_error=rejected#runs');
  await expect(page.getByRole('alert')).toContainText('not linked to a team');
  await expect(page).toHaveURL(/\/console\/#runs$/);
  await expect(page.getByLabel('API key', { exact: true })).toBeVisible();
});

test('opening the console lands on approvals, runs or Get started', async ({ page }) => {
  await login(page, 'admin', '/console/');
  await expect(page).toHaveURL(/#approvals$/);
  await expect(page.getByRole('heading', { name: 'Approvals', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await login(page, 'viewer', '/console/');
  await expect(page).toHaveURL(/#runs$/);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await login(page, 'other', '/console/');
  await expect(page).toHaveURL(/#start$/);
  await expect(page.getByRole('heading', { name: 'Your first governed workflow' })).toBeVisible();
});

test('a workflow that needs a provider without a key warns before it runs', async ({ page }) => {
  await page.route('**/v1/team', route => route.fulfill({ json: { team_id: 'demo', role: 'admin', projects: ['default'], providers: ['openai', 'anthropic'], cost_limit_usd: 50,
    provider_configuration: { openai: { configured: true, environment_variable: 'OPENAI_API_KEY' }, anthropic: { configured: false, environment_variable: 'ANTHROPIC_API_KEY' } } } }));
  await login(page);
  await expect(page.locator('.onboarding .note-warn')).toHaveText('The example uses Anthropic, which has no key yet, so those steps will fail. Add the key first.');
  await page.getByRole('link', { name: 'Run workflow', exact: true }).first().click();
  await expect(page.locator('.note-warn')).toHaveText('Anthropic has no key yet, so this workflow’s calls there fail. Add the key first.');
  await page.getByLabel('Workflow', { exact: true }).selectOption('CustomWorkflow');
  await expect(page.locator('.note-warn')).toHaveCount(0);
});

test('approval cards use the draft heading as the title without repeating it', async ({ page }) => {
  await page.route('**/v1/workflow-runs?*', route => route.fulfill({ json: { runs: [{ ...run(), progress: { stage: 'awaiting_approval', draft: '# Briefing: agent evaluation\n\nThe first paragraph.' } }], next_cursor: null } }));
  await login(page, 'approver');
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  await expect(page.locator('.review h2').first()).toHaveText('Briefing: agent evaluation');
  await expect(page.locator('.review .draft').first()).toHaveText('The first paragraph.');
});

for (const surface of ['Team settings', 'Providers & budgets', 'Costs']) {
  test(`admin edits and saves budgets from ${surface}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await login(page);
    await page.getByRole('link', { name: surface, exact: true }).click();
    await page.getByLabel('Team monthly budget (USD)', { exact: true }).fill('125');
    await page.getByLabel('Project default monthly budget (USD)', { exact: true }).fill('40');
    await page.getByLabel('Research · Budget per run (USD)', { exact: true }).fill('3');
    await page.getByLabel('Research · Token limit per run', { exact: true }).fill('12,345');
    await page.getByLabel('Team monthly budget (USD)', { exact: true }).focus();
    await expect(page.getByLabel('Research · Token limit per run', { exact: true })).toHaveValue('12,345');
    const request = page.waitForRequest(value => value.url().endsWith('/v1/team/settings') && value.method() === 'PATCH');
    await page.getByRole('button', { name: 'Save settings' }).click();
    expect((await request).postDataJSON()).toEqual({ fields: { cost_limit_usd: 125, 'project_budgets.default': 40, 'workflows.ResearchWorkflow.cost_limit_usd': 3, 'workflows.ResearchWorkflow.token_limit': 12345 } });
    expect((await request).headers()['if-match']).toBe('0');
    expect((await request).headers()['x-csrf-token']).toBe('csrf-fixture');
    await expect(page.getByRole('status').filter({ hasText: 'Settings saved.' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reset Team monthly budget (USD) to policy default' })).toBeVisible();
    // Right after saving, the bar shows only the confirmation.
    await expect(page.getByRole('button', { name: 'Save settings' })).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test('admin saves approval rules and selects an existing alias route', async ({ page }) => {
  await login(page);
  await page.getByRole('link', { name: 'Team settings', exact: true }).click();
  await page.getByLabel('Research · Approval threshold (USD)', { exact: true }).fill('0.75');
  await page.getByLabel('Research · Require approval', { exact: true }).uncheck();
  // With approval off, the threshold no longer applies and cannot be edited.
  await expect(page.getByLabel('Research · Approval threshold (USD)', { exact: true })).toBeDisabled();
  await page.getByLabel('Research · Approver role', { exact: true }).selectOption('admin');
  await page.getByRole('group', { name: 'Research · Allowed providers' }).getByLabel('OpenAI', { exact: true }).uncheck();
  await page.getByLabel('Model for research', { exact: true }).selectOption('demo-anthropic');
  const request = page.waitForRequest(value => value.method() === 'PATCH');
  await page.getByRole('button', { name: 'Save settings' }).click();
  expect((await request).postDataJSON().fields).toEqual({
    'workflows.ResearchWorkflow.approval_required': false, 'workflows.ResearchWorkflow.approval_threshold_usd': 0.75,
    'workflows.ResearchWorkflow.approver_role': 'admin', 'workflows.ResearchWorkflow.allowed_providers': ['anthropic'],
    'model_routes.research': 'demo-anthropic',
  });
  await expect(page.getByRole('status')).toContainText('Settings saved.');
  await page.reload();
  await expect(page.getByLabel('Model for research', { exact: true })).toHaveValue('demo-anthropic');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('a stale settings revision requires reloading before another save', async ({ page }) => {
  let stale = false;
  await page.route('**/v1/team/settings', route => {
    if (route.request().method() === 'PATCH') {
      stale = true;
      return route.fulfill({ status: 409, json: { detail: { reason: 'team_settings_conflict' } } });
    }
    const current = settings();
    if (stale) { current.revision = 4; current.fields.cost_limit_usd.value = 99; }
    return route.fulfill({ json: current });
  });
  await login(page);
  await page.getByRole('link', { name: 'Team settings', exact: true }).click();
  await page.getByLabel('Team monthly budget (USD)', { exact: true }).fill('100');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByRole('alert')).toContainText('Settings changed since you opened this page');
  await expect(page.getByLabel('Team monthly budget (USD)', { exact: true })).toHaveValue('100');
  await expect(page.getByRole('button', { name: 'Save settings' })).toBeDisabled();
  await page.getByRole('button', { name: 'Reload settings' }).click();
  await expect(page.getByLabel('Team monthly budget (USD)', { exact: true })).toHaveValue('99');
});

test('reset waits for Save, Discard undoes it, and Save sends it before other edits', async ({ page }) => {
  const sent: string[] = [];
  page.on('request', request => { if (['PATCH', 'DELETE'].includes(request.method())) sent.push(`${request.method()} ${new URL(request.url()).pathname} ${request.headers()['if-match']}`); });
  await login(page);
  await page.getByRole('link', { name: 'Team settings', exact: true }).click();
  const budget = page.getByLabel('Team monthly budget (USD)', { exact: true });
  const reset = page.getByRole('button', { name: 'Reset Team monthly budget (USD) to policy default' });
  await budget.fill('100');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByRole('status')).toContainText('Settings saved.');
  await reset.click();
  await expect(budget).toHaveValue('50');
  await expect(page.getByRole('status')).toHaveText('1 unsaved change');
  await page.getByRole('button', { name: 'Discard' }).click();
  await expect(budget).toHaveValue('100');
  expect(sent).toEqual(['PATCH /v1/team/settings 0']);
  await reset.click();
  await expect(reset).toHaveCount(0);
  await page.getByLabel('Project default monthly budget (USD)', { exact: true }).fill('20');
  await expect(page.getByRole('status')).toHaveText('2 unsaved changes');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByRole('status')).toContainText('Settings saved.');
  expect(sent).toEqual(['PATCH /v1/team/settings 0', 'DELETE /v1/team/settings/cost_limit_usd 1', 'PATCH /v1/team/settings 2']);
  await expect(budget).toHaveValue('50');
  await expect(page.getByLabel('Project default monthly budget (USD)', { exact: true })).toHaveValue('20');
});

for (const role of ['builder', 'approver', 'viewer']) {
  test(`${role} sees read-only settings without calling the admin API`, async ({ page }) => {
    let adminRequests = 0;
    page.on('request', request => { if (request.url().includes('/v1/team/settings')) adminRequests++; });
    await login(page, role);
    for (const surface of ['Team settings', 'Providers & budgets', 'Costs']) {
      await page.getByRole('link', { name: surface, exact: true }).click();
      await expect(page.getByText('Read-only. Your team admin can change these settings.')).toBeVisible();
      await expect(page.getByText('Team monthly budget', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Save settings' })).toHaveCount(0);
      await expect(page.getByRole('spinbutton')).toHaveCount(0);
    }
    expect(adminRequests).toBe(0);
  });
}

test('settings validation shows the rejected field and keeps the draft', async ({ page }) => {
  await page.route('**/v1/team/settings', route => route.request().method() === 'PATCH' ? route.fulfill({ status: 422, json: { detail: {
    message: 'Check settings.', fields: [{ field: 'cost_limit_usd', message: 'Use a finite non-negative USD amount.' }],
  } } }) : route.fallback());
  await login(page);
  await page.getByRole('link', { name: 'Team settings', exact: true }).click();
  await page.getByLabel('Team monthly budget (USD)', { exact: true }).fill('100');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByLabel('Team monthly budget (USD)', { exact: true })).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByText('Use a finite non-negative USD amount.', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Team monthly budget (USD)', { exact: true })).toHaveValue('100');
});

test('monthly spend displays a zero limit as a limit', async ({ page }) => {
  await page.route('**/v1/usage', route => route.fulfill({ json: { estimated_cost: 0, spend: {
    period: 'month', window_start: 1790812800, window_seconds: 2678400, project: null,
    cost_limit_usd: 0, reserved_and_spent_usd: 0, providers: {}, workflows: {},
  } } }));
  await login(page, 'viewer');
  await page.getByRole('link', { name: 'Costs', exact: true }).click();
  await expect(page.getByText('Spent this month', { exact: true })).toBeVisible();
  await expect(page.getByText('of $0.00', { exact: true })).toBeVisible();
  await expect(page.getByText('No team limit', { exact: true })).toHaveCount(0);
});

test('admins save soft limits and capture defaults with workflow overrides', async ({ page }) => {
  await login(page, 'admin', '/console/#team');
  await page.getByLabel('Team monthly soft limit (USD)', { exact: true }).fill('40');
  await page.getByLabel('Default step content capture', { exact: true }).selectOption('redacted');
  await page.getByLabel('Research · Step content capture', { exact: true }).selectOption('full');
  const request = page.waitForRequest(req => req.method() === 'PATCH' && req.url().endsWith('/v1/team/settings'));
  await page.getByRole('button', { name: 'Save settings' }).click();
  expect((await request).postDataJSON()).toEqual({ fields: {
    soft_cost_limit_usd: 40, capture_content: 'redacted', 'workflows.ResearchWorkflow.capture_content': 'full',
  } });
  await expect(page.getByText('Settings saved. They apply to the next request.')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Default step content capture', { exact: true })).toHaveValue('redacted');
});

test('Costs shows hard-limit and webhook alerts on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 900 });
  await page.route('**/v1/team/spend', route => route.fulfill({ json: {
    team_id: 'demo', window_start: 1790812800, window_end: 1793491200, soft_limit_usd: 40, hard_limit_usd: 50,
    reserved_and_spent_usd: 50, status: 'hard_limit', alerts: [{ id: 'alert', level: 'hard', limit_usd: 50,
      reserved_and_spent_usd: 50, requested_usd: 1, created_at: 1791316800, webhook_status: 'failed', attempts: 5 }],
  } }));
  await login(page, 'viewer', '/console/#costs');
  await expect(page.getByRole('heading', { name: 'Spend alerts' })).toBeVisible();
  await expect(page.getByText('Hard limit reached. Further paid calls return 429', { exact: false })).toBeVisible();
  await expect(page.getByText('after five attempts', { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('key access editing submits changed fields and preserves drafts after a conflict', async ({ page }) => {
  let conflict = true;
  const key = { key_id: 'edit-me', name: 'Alice', role: 'viewer', project: null, expires_at: null, revoked_at: null, last_used_at: null, created_at: 1 };
  await page.route('**/v1/team/keys**', route => {
    if (route.request().method() === 'PATCH') {
      expect(route.request().postDataJSON()).toEqual({ name: 'Build bot', role: 'builder', project: 'engineering', expires_at: '2027-01-01T12:00:00.000Z' });
      if (conflict) return route.fulfill({ status: 409, json: { detail: 'This key is already revoked.' } });
      Object.assign(key, route.request().postDataJSON(), { expires_at: 1798804800 });
      return route.fulfill({ json: key });
    }
    return route.fulfill({ json: { keys: [key] } });
  });
  await login(page, 'admin', '/console/#keys');
  await page.getByRole('button', { name: 'Edit Alice', exact: true }).click();
  await page.getByLabel('Edit name', { exact: true }).fill('Build bot');
  await page.getByLabel('Edit role', { exact: true }).selectOption('builder');
  await page.getByLabel('Edit project', { exact: true }).selectOption('engineering');
  await page.getByLabel('Edit expiry (UTC)', { exact: true }).fill('2027-01-01T12:00');
  await page.getByRole('button', { name: 'Save key', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('already revoked');
  await expect(page.getByLabel('Edit name', { exact: true })).toHaveValue('Build bot');
  conflict = false;
  await page.getByRole('button', { name: 'Save key', exact: true }).click();
  await expect(page.getByText('Build bot updated. Access changes apply immediately.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit Build bot', exact: true })).toBeVisible();
});


for (const width of [393, 1440]) {
  test(`partial approvals stay visible with quorum and deadline at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const current: Run = run({ progress: { stage: 'awaiting_approval', draft: 'A draft for review.',
      required_approvals: 2, approved_by: [] as string[], expires_at: '2099-10-09T12:00:00Z', approver_role: 'approver' } });
    await page.route('**/v1/workflow-runs?*', route => route.fulfill({ json: { runs: new URL(route.request().url()).searchParams.get('project') === 'default' ? [current] : [], next_cursor: null } }));
    await page.route(`**/v1/workflow-runs/${id}/approve`, route => {
      expect(route.request().postDataJSON()).toEqual({ approved: true });
      current.progress!.approved_by!.push('admin');
      return route.fulfill({ json: { approved: true, reviewer: 'admin' } });
    });
    await login(page, 'admin', '/console/#approvals');
    await expect(page.getByText(/0 of 2 approvals received/)).toBeVisible();
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await expect(page.getByText(/1 of 2 approvals received/)).toBeVisible();
    await expect(page.getByText(/You have already approved/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Reject', exact: true })).toBeDisabled();
    await expect(page.getByText(/Expires.*2099/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('approval deadline and admin-only policy disable unavailable decisions', async ({ page }) => {
  const current: Run = run({ progress: { stage: 'awaiting_approval', draft: 'Review', required_approvals: 2,
    approved_by: [], expires_at: '2020-01-01T00:00:00Z', approver_role: 'admin' } });
  await page.route('**/v1/workflow-runs?*', route => route.fulfill({ json: { runs: new URL(route.request().url()).searchParams.get('project') === 'default' ? [current] : [], next_cursor: null } }));
  await login(page, 'approver', '/console/#approvals');
  await expect(page.getByText(/An admin must review/)).toBeVisible();
  await expect(page.getByText(/deadline has passed/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
});

test('team settings saves quorum and expiry together at the current revision', async ({ page }) => {
  await login(page, 'admin', '/console/#team');
  const count = page.getByLabel('Research · Required reviewers', { exact: true });
  const expiry = page.getByLabel('Research · Approval expiry (seconds)', { exact: true });
  await expect(count).toHaveValue('1');
  await expect(expiry).toHaveValue('604,800');
  await count.fill('2');
  await expiry.fill('3600');
  const saved = page.waitForRequest(request => request.url().endsWith('/v1/team/settings') && request.method() === 'PATCH');
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  const request = await saved;
  expect(request.headers()['if-match']).toBe('0');
  expect(request.postDataJSON()).toEqual({ fields: {
    'workflows.ResearchWorkflow.required_approvals': 2,
    'workflows.ResearchWorkflow.approval_timeout_seconds': 3600,
  } });
  await expect(page.getByText(/Settings saved/)).toBeVisible();
});
