import { MockActivityEnvironment } from '@temporalio/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentContext, GatewayActivities } from '../src/activities';
import type { AgentHandler } from '../src/activities';
import { runContainer, validateWorkspace } from '../src/containers';
import type { Kubectl } from '../src/containers';
import type { Call } from '../src/types';

const runId = 'ebf9363a-7912-4a62-89b4-b0a28c169731';
const fetchMock = vi.fn<typeof fetch>();
const ok = (body: unknown = {}) => new Response(JSON.stringify(body), { status: 200 });
const environment = () => new MockActivityEnvironment({
  workflowExecution: { workflowId: 'demo/default/frameworks', runId },
  workflowType: 'FrameworkWorkflow', activityId: '7',
});
const call = (kind: Call['kind'], tool: string, payload: Record<string, unknown>): Call => ({
  kind, tool, payload, budget: { token_limit: 300, cost_limit_usd: 0.5 }, data_classification: 'confidential',
});
const header = (index: number, name: string) => new Headers(fetchMock.mock.calls[index][1]?.headers).get(name);
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
afterEach(() => vi.unstubAllGlobals());

/** Calls fetch the way the OpenAI and Anthropic SDKs do, and wraps failures as their connection errors. */
async function sdkPost(options: { baseURL: string; apiKey: string; fetch: typeof fetch }, path: string, body: unknown) {
  try {
    const response = await options.fetch(`${options.baseURL}${path}`, {
      method: 'POST', body: JSON.stringify(body), signal: new AbortController().signal,
      headers: { 'content-type': 'application/json', authorization: `Bearer ${options.apiKey}`, 'x-stainless-retry-count': '0' },
    });
    return await response.json() as Record<string, unknown>;
  } catch (error) {
    throw new Error('Connection error.', { cause: error });
  }
}

const agents: Record<string, AgentHandler> = {
  async briefing(context, args) {
    const sources = await context.tool<string[]>('team.search', { query: args.topic });
    const chat = await sdkPost(context.openaiOptions(), '/chat/completions', { model: 'demo-openai', messages: [{ role: 'user', content: sources.join() }] });
    const message = await sdkPost(context.anthropicOptions(), '/v1/messages', { model: 'demo-anthropic', max_tokens: 16, messages: [] });
    return { chat: chat.id, message: message.id };
  },
};

describe('framework agents', () => {
  it('runs registered agents with governed, sequenced step IDs on every gateway request', async () => {
    fetchMock.mockImplementation(async (url) => {
      const path = new URL(String(url)).pathname;
      return ok(path.startsWith('/v1/tools') ? { result: ['source'] } : { id: path });
    });
    const activities = new GatewayActivities('http://gateway.test/', 'team-key', { agents });
    await expect(environment().run(activities.call.bind(activities), call('agent', 'briefing', { topic: 'agents' })))
      .resolves.toEqual({ result: { chat: '/v1/chat/completions', message: '/v1/messages' } });
    expect(fetchMock.mock.calls.map(([url, init]) => [init?.method, String(url)])).toEqual([
      ['PUT', `http://gateway.test/v1/workflow-runs/${runId}`],
      ['POST', 'http://gateway.test/v1/tools/team.search/call'],
      ['POST', 'http://gateway.test/v1/chat/completions'],
      ['POST', 'http://gateway.test/v1/messages'],
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ arguments: { query: 'agents' } });
    for (const index of [1, 2, 3]) {
      expect(header(index, 'X-Workflow-Run-ID')).toBe(runId);
      expect(header(index, 'X-Workflow-Step-ID')).toBe(`7/${index}`);
      expect(header(index, 'X-Data-Classification')).toBe('confidential');
      expect(header(index, 'Authorization')).toBe('Bearer team-key');
      expect(fetchMock.mock.calls[index][1]?.redirect).toBe('error');
    }
    expect(header(2, 'x-stainless-retry-count')).toBe('0');
  });

  it('fails a policy denial inside a framework without retrying it', async () => {
    fetchMock.mockResolvedValueOnce(ok()).mockResolvedValue(new Response(JSON.stringify({
      detail: { reason: 'workflow_model_denied', message: 'denied private-detail' },
    }), { status: 403, headers: { 'X-Request-ID': 'req-9' } }));
    const activities = new GatewayActivities('http://gateway.test', 'team-key', { agents });
    const error = await environment().run(activities.call.bind(activities), call('agent', 'briefing', { topic: 'x' }))
      .catch((error: unknown) => error);
    expect(error).toMatchObject({ type: 'workflow_model_denied', nonRetryable: true, message: expect.stringContaining('req-9') });
    expect(String(error)).not.toContain('private-detail');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('retries a lost gateway connection inside a framework as a transient failure', async () => {
    fetchMock.mockResolvedValueOnce(ok()).mockRejectedValue(new TypeError('fetch failed', { cause: { code: 'ECONNRESET' } }));
    const activities = new GatewayActivities('http://gateway.test', 'team-key', { agents });
    await expect(environment().run(activities.call.bind(activities), call('agent', 'briefing', { topic: 'x' })))
      .rejects.toMatchObject({ type: 'GatewayUnavailable', nonRetryable: false });
  });

  it('refuses unregistered agents before initializing the run', async () => {
    const activities = new GatewayActivities('http://gateway.test', 'team-key', { agents });
    for (const name of ['missing', 'toString', '__proto__']) {
      await expect(environment().run(activities.call.bind(activities), call('agent', name, {})))
        .rejects.toMatchObject({ type: 'AgentNotRegistered', nonRetryable: true });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps tool calls on the tool route even when an agent has the same name', async () => {
    fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({ result: 'tool' }));
    const activities = new GatewayActivities('http://gateway.test', 'team-key', { agents: { search: async () => 'agent' } });
    await expect(environment().run(activities.call.bind(activities), call('tool', 'search', { arguments: {} })))
      .resolves.toEqual({ result: 'tool' });
    expect(String(fetchMock.mock.calls[1][0])).toBe('http://gateway.test/v1/tools/search/call');
  });

  it('lets agents reach only the gateway, resolving relative paths under its prefix', async () => {
    fetchMock.mockImplementation(async () => ok({ data: [] }));
    const context = new AgentContext('http://gateway.test/prefix', 'team-key', { 'X-Workflow-Step-ID': '3' });
    expect(context.openaiOptions()).toMatchObject({ baseURL: 'http://gateway.test/prefix/v1', apiKey: 'team-key', maxRetries: 0 });
    expect(context.anthropicOptions().baseURL).toBe('http://gateway.test/prefix');
    await context.fetch('v1/models');
    await context.fetch(new Request('http://gateway.test/prefix/v1/models', { headers: { 'X-Trace': 'kept' } }));
    expect(String(fetchMock.mock.calls[0][0])).toBe('http://gateway.test/prefix/v1/models');
    expect(header(1, 'X-Trace')).toBe('kept');
    expect(header(1, 'X-Workflow-Step-ID')).toBe('3/2');
    for (const url of ['https://api.openai.com/v1/chat/completions', 'http://gateway.test/other', 'http://gateway.test:81/prefix/v1']) {
      await expect(context.fetch(url)).rejects.toMatchObject({ type: 'AgentEgressDenied', nonRetryable: true });
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(context.sequence).toBe(2);
  });

  it('fails an agent step that tries to leave the gateway, even through a framework', async () => {
    fetchMock.mockResolvedValue(ok());
    const activities = new GatewayActivities('http://gateway.test', 'team-key', {
      agents: { leak: context => sdkPost({ ...context.openaiOptions(), baseURL: 'https://api.openai.com/v1' }, '/chat/completions', {}) },
    });
    await expect(environment().run(activities.call.bind(activities), call('agent', 'leak', {})))
      .rejects.toMatchObject({ type: 'AgentEgressDenied', nonRetryable: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('cancels agent requests when Temporal cancels the activity', async () => {
    const env = environment();
    fetchMock.mockResolvedValueOnce(ok()).mockImplementation(async (_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
      env.cancel();
    }));
    const activities = new GatewayActivities('http://gateway.test', 'team-key', { agents });
    await expect(env.run(activities.call.bind(activities), call('agent', 'briefing', { topic: 'x' })))
      .rejects.toMatchObject({ name: 'CancelledFailure' });
  });
});

function workspace() {
  const spec = () => ({
    automountServiceAccountToken: false,
    securityContext: { runAsNonRoot: true, runAsUser: 10001, runAsGroup: 10001, seccompProfile: { type: 'RuntimeDefault' } },
    containers: [{
      name: 'workspace', image: 'approved-image',
      securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ['ALL'] } },
    }] as Record<string, unknown>[],
    volumes: [{ name: 'workspace', persistentVolumeClaim: { claimName: 'workspace' } }] as Record<string, unknown>[],
  } as Record<string, unknown> & { securityContext: Record<string, unknown>; containers: Record<string, unknown>[]; volumes: Record<string, unknown>[] });
  const sandbox = { apiVersion: 'agents.x-k8s.io/v1beta1', metadata: { uid: 'owner' }, spec: { podTemplate: { spec: spec() } } };
  const pod = { metadata: { ownerReferences: [{ uid: 'owner', kind: 'Sandbox' }] as unknown[] }, spec: spec(), status: { phase: 'Running' } };
  const policies = {
    items: [
      { metadata: { name: 'agent-workspace-default-deny' }, spec: { podSelector: {}, policyTypes: ['Ingress', 'Egress'] } },
      {
        metadata: { name: 'agent-workspace-approved-egress' },
        spec: {
          podSelector: {}, policyTypes: ['Egress'],
          egress: [
            { to: [{ namespaceSelector: { matchLabels: { 'kubernetes.io/metadata.name': 'inference' } } }], ports: [{ protocol: 'TCP', port: 8080 }] },
            { ports: [{ protocol: 'UDP', port: 53 }, { protocol: 'TCP', port: 53 }], to: [{ namespaceSelector: { matchLabels: { 'kubernetes.io/metadata.name': 'kube-system' } } }] },
          ] as unknown[],
        },
      },
    ] as { metadata: { name: string }; spec: Record<string, unknown> }[],
  };
  return { sandbox, pod, policies };
}

const gatewayUrl = 'http://gateway.inference.svc.cluster.local:8080';

describe('container workspaces', () => {
  it.each([
    'token', 'network', 'owner', 'root', 'override', 'sidecar', 'hostpath', 'extra-policy', 'env', 'image', 'pending',
    'privileged', 'host-network', 'empty-egress', 'wrong-port',
  ])('refuses the isolation bypass %s', (violation) => {
    const { sandbox, pod, policies } = workspace();
    validateWorkspace(sandbox, pod, policies, gatewayUrl);
    const container = pod.spec.containers[0];
    const egress = policies.items[1].spec.egress as Record<string, unknown>[];
    switch (violation) {
      case 'token': pod.spec.volumes.push({ name: 'token', projected: {} }); break;
      case 'network': egress.push({}); break;
      case 'owner': pod.metadata.ownerReferences = []; break;
      case 'root': pod.spec.securityContext.runAsUser = 0; break;
      case 'override': (container.securityContext as Record<string, unknown>).runAsUser = 0; break;
      case 'sidecar': pod.spec.containers.push(container); break;
      case 'hostpath': pod.spec.volumes.push({ name: 'host', hostPath: { path: '/' } }); break;
      case 'extra-policy': policies.items.push({ metadata: { name: 'allow-all' }, spec: { egress: [{}] } }); break;
      case 'env': container.env = [{ name: 'TOKEN', value: 'x' }]; break;
      case 'image': container.image = 'other-image'; break;
      case 'pending': pod.status.phase = 'Pending'; break;
      case 'privileged': (container.securityContext as Record<string, unknown>).privileged = true; break;
      case 'host-network': pod.spec.hostNetwork = true; break;
      case 'empty-egress': egress.splice(0, 1); break;
      case 'wrong-port': (egress[0].ports as Record<string, unknown>[])[0].port = 8443; break;
    }
    expect(() => validateWorkspace(sandbox, pod, policies, gatewayUrl)).toThrow(expect.objectContaining({
      type: 'WorkspaceNotIsolated', nonRetryable: true, message: expect.stringContaining('not isolated'),
    }));
  });

  it('accepts empty optional fields and requires the gateway service DNS name', () => {
    const { sandbox, pod, policies } = workspace();
    Object.assign(pod.spec, { hostNetwork: false, initContainers: [], ephemeralContainers: [] });
    Object.assign(pod.spec.containers[0], { env: [], envFrom: [] });
    validateWorkspace(sandbox, pod, policies, gatewayUrl);
    expect(() => validateWorkspace(sandbox, pod, policies, 'http://10.0.0.5:8080'))
      .toThrow("Container workers must use the gateway's Kubernetes service DNS URL.");
    expect(() => validateWorkspace(sandbox, pod, policies, 'https://gateway.inference.svc')).toThrow('not isolated');
  });

  const plan = {
    namespace: 'team-code', sandbox: 'coder', command: ['python', '/app/agent.py'], arguments: { task: 'test' },
    timeout_seconds: 120, api_key: 'awf_short-lived', credential_id: 'a'.repeat(64), expires_at: 1,
  };

  function cluster(exec: () => Promise<Buffer>) {
    const { sandbox, pod, policies } = workspace();
    const calls: { args: string[]; input?: string; timeoutMs: number }[] = [];
    const kubectl = vi.fn<Kubectl>(async (args, options) => {
      calls.push({ args, input: options.input, timeoutMs: options.timeoutMs });
      if (args.includes('exec')) return exec();
      return Buffer.from(JSON.stringify(args.includes('sandbox') ? sandbox : args.includes('pod') ? pod : policies));
    });
    return { calls, kubectl };
  }

  it('passes the grant on stdin only and finishes with the runner result', async () => {
    fetchMock.mockImplementation(async url => ok(String(url).endsWith('/start') ? plan : { result: 'safe output' }));
    const { calls, kubectl } = cluster(async () => Buffer.from(JSON.stringify({ exit_code: 0, output: 'raw output' })));
    const headers = { 'X-Workflow-Run-ID': runId, 'X-Workflow-Step-ID': '1', 'X-Data-Classification': 'internal' };
    await expect(runContainer({ baseUrl: gatewayUrl, apiKey: 'team-key', agent: 'coder/x', payload: { arguments: {} }, headers, kubectl }))
      .resolves.toEqual({ result: 'safe output' });
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `${gatewayUrl}/v1/agents/coder%2Fx/start`, `${gatewayUrl}/v1/agents/coder%2Fx/finish`,
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ credential_id: plan.credential_id, exit_code: 0, output: 'raw output' });
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ ...headers, Authorization: 'Bearer team-key' });
    expect(calls.map(({ args }) => args.slice(0, 4))).toEqual([
      ['-n', 'team-code', 'get', 'sandbox'], ['-n', 'team-code', 'get', 'pod'], ['-n', 'team-code', 'get', 'networkpolicies'],
      ['-n', 'team-code', 'exec', '-i'],
    ]);
    const exec = calls[3];
    expect(exec.args).toEqual(['-n', 'team-code', 'exec', '-i', 'coder', '-c', 'workspace', '--', 'python', '-m', 'agentworkflows.container_runner']);
    expect(JSON.stringify(exec.args)).not.toContain('awf_short-lived');
    expect(JSON.parse(exec.input!)).toEqual({ ...plan, gateway_url: gatewayUrl, headers });
    expect(exec.timeoutMs).toBe(130_000);
  });

  it.each([
    ['a failed command', { exec: async () => { throw new Error('fixture failed'); } }, 'fixture failed'],
    ['unreadable runner output', { exec: async () => Buffer.from('not json') }, 'invalid output'],
    ['an unsafe workspace name', { plan: { sandbox: '--kubeconfig=/tmp/x' } }, 'invalid workspace plan'],
    ['a namespace that is not a DNS label', { plan: { namespace: 'team.code' } }, 'invalid workspace plan'],
    ['an overlong namespace', { plan: { namespace: 'a'.repeat(64) } }, 'invalid workspace plan'],
    ['a missing timeout', { plan: { timeout_seconds: undefined } }, 'invalid workspace plan'],
  ] as const)('always revokes the grant after %s and reports the original failure', async (_name, failure, message) => {
    const start = { ...plan, ...('plan' in failure ? failure.plan : {}) };
    fetchMock.mockImplementation(async url => String(url).endsWith('/start') ? ok(start) : new Response('{}', { status: 503 }));
    const { calls, kubectl } = cluster('exec' in failure ? failure.exec : async () => Buffer.from('{}'));
    await expect(runContainer({ baseUrl: gatewayUrl, apiKey: 'team-key', agent: 'coder', payload: {}, headers: {}, kubectl }))
      .rejects.toThrow(message);
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toMatch(/\/finish$/);
    expect(JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body))).toEqual({ credential_id: plan.credential_id, exit_code: -1, output: '' });
    if ('plan' in failure) expect(calls).toEqual([]);
  });

  it('accepts a dotted Sandbox name, as Kubernetes subdomain names allow', async () => {
    fetchMock.mockImplementation(async url => ok(String(url).endsWith('/start') ? { ...plan, sandbox: 'coder.v2' } : { result: 'ok' }));
    const { calls, kubectl } = cluster(async () => Buffer.from(JSON.stringify({ exit_code: 0, output: '' })));
    await expect(runContainer({ baseUrl: gatewayUrl, apiKey: 'team-key', agent: 'coder', payload: {}, headers: {}, kubectl }))
      .resolves.toEqual({ result: 'ok' });
    expect(calls[3].args).toContain('coder.v2');
  });

  it('runs container calls from the activity after initializing the run budget', async () => {
    fetchMock.mockImplementation(async url => ok(String(url).endsWith('/start') ? plan : { result: 'done' }));
    const { kubectl } = cluster(async () => Buffer.from(JSON.stringify({ exit_code: 0, output: 'raw' })));
    const activities = new GatewayActivities(gatewayUrl, 'team-key', { kubectl });
    await expect(environment().run(activities.call.bind(activities), call('container', 'coder', { arguments: { task: 'x' } })))
      .resolves.toEqual({ result: 'done' });
    expect(fetchMock.mock.calls.map(([url, init]) => [init?.method, new URL(String(url)).pathname])).toEqual([
      ['PUT', `/v1/workflow-runs/${runId}`], ['POST', '/v1/agents/coder/start'], ['POST', '/v1/agents/coder/finish'],
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ arguments: { task: 'x' } });
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ 'X-Workflow-Step-ID': '7', 'X-Data-Classification': 'confidential' });
  });

  it('maps a refused container start to a non-retryable failure', async () => {
    fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(new Response('{"detail":{"reason":"agent_not_allowed"}}', { status: 403 }));
    const activities = new GatewayActivities(gatewayUrl, 'team-key', { kubectl: vi.fn<Kubectl>() });
    await expect(environment().run(activities.call.bind(activities), call('container', 'coder', {})))
      .rejects.toMatchObject({ type: 'agent_not_allowed', nonRetryable: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
