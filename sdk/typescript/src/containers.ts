import { spawn } from 'node:child_process';
import { ApplicationFailure } from '@temporalio/common';
import { requestJson } from './http';

/** Run `kubectl` with `input` on stdin and return its stdout. */
export type Kubectl = (args: string[], options: { input?: string; timeoutMs: number; signal?: AbortSignal }) => Promise<Buffer>;

const OUTPUT_LIMIT = 1_048_576;
const NAME = /^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$/;

export const kubectl: Kubectl = (args, { input, timeoutMs, signal }) => new Promise((resolve, reject) => {
  const child = spawn('kubectl', args, { stdio: ['pipe', 'pipe', 'ignore'] });
  const output: Buffer[] = [];
  let size = 0;
  let failure: Error | undefined;
  let settled = false;
  const stop = (error: Error) => {
    failure ??= error;
    child.kill('SIGKILL');
  };
  const timer = setTimeout(() => stop(ApplicationFailure.retryable(
    'Workspace command timed out; check Sandbox readiness and the agent timeout.', 'WorkspaceTimeout')), timeoutMs);
  const abort = () => stop(ApplicationFailure.retryable('Workspace command canceled.', 'WorkspaceCanceled'));
  signal?.addEventListener('abort', abort, { once: true });
  const finish = (error?: Error) => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    if (error) reject(error);
    else resolve(Buffer.concat(output));
  };
  child.on('error', (error: NodeJS.ErrnoException) => finish(error.code === 'ENOENT'
    ? ApplicationFailure.nonRetryable('Install kubectl on the worker and bind its identity to the approved workspace.', 'KubectlMissing')
    : failure ?? ApplicationFailure.nonRetryable('Could not run kubectl on the worker.', 'KubectlFailed')));
  child.stdout.on('data', (chunk: Buffer) => {
    size += chunk.length;
    if (size > OUTPUT_LIMIT) {
      stop(ApplicationFailure.nonRetryable('Workspace output exceeds 1 MiB; return a smaller result.', 'WorkspaceOutputTooLarge'));
    } else output.push(chunk);
  });
  // The command may exit before reading stdin; its exit status reports the failure.
  child.stdin.on('error', () => undefined);
  child.stdin.end(input ?? '');
  child.on('close', (code) => finish(failure ?? (code === 0 ? undefined : ApplicationFailure.nonRetryable(
    'Workspace command failed; check Sandbox readiness, worker RBAC, and agent image.', 'WorkspaceCommandFailed'))));
});

type Json = Record<string, unknown>;

/** Python truthiness for JSON values: empty strings, arrays and objects count as absent. */
function present(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (value && typeof value === 'object') return Object.keys(value).length > 0;
  return Boolean(value);
}

function same(left: unknown, right: unknown): boolean {
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
      left.every((item, index) => same(item, right[index]));
  }
  if (left && right && typeof left === 'object' && typeof right === 'object') {
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length &&
      keys.every(key => Object.hasOwn(right, key) && same((left as Json)[key], (right as Json)[key]));
  }
  return left === right;
}

function object(value: unknown): Json {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Json : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function isolated(sandbox: Json, pod: Json, policies: Json, gateway: string): boolean {
  const url = new URL(gateway);
  const host = url.hostname.split('.');
  const svc = host.indexOf('svc');
  if (svc < 2) {
    throw ApplicationFailure.nonRetryable("Container workers must use the gateway's Kubernetes service DNS URL.", 'WorkspaceNotIsolated');
  }
  const gatewayRule = {
    to: [{ namespaceSelector: { matchLabels: { 'kubernetes.io/metadata.name': host[svc - 1] } } }],
    ports: [{ protocol: 'TCP', port: Number(url.port) || (url.protocol === 'https:' ? 443 : 80) }],
  };
  const dnsRule = {
    to: [{ namespaceSelector: { matchLabels: { 'kubernetes.io/metadata.name': 'kube-system' } } }],
    ports: [{ protocol: 'UDP', port: 53 }, { protocol: 'TCP', port: 53 }],
  };
  const spec = object(pod.spec);
  const security = object(spec.securityContext);
  const containers = list(spec.containers).map(object);
  const network = Object.fromEntries(list(policies.items).map(object)
    .map(item => [String(object(item.metadata).name), object(item.spec)]));
  const deny = network['agent-workspace-default-deny'] ?? {};
  const approved = network['agent-workspace-approved-egress'] ?? {};
  const egress = list(approved.egress);
  const podValid = sandbox.apiVersion === 'agents.x-k8s.io/v1beta1' &&
    list(object(pod.metadata).ownerReferences).map(object)
      .some(owner => owner.uid === object(sandbox.metadata).uid && owner.uid !== undefined && owner.kind === 'Sandbox') &&
    object(pod.status).phase === 'Running' &&
    spec.automountServiceAccountToken === false &&
    !['hostNetwork', 'hostPID', 'hostIPC', 'initContainers', 'ephemeralContainers'].some(key => present(spec[key])) &&
    security.runAsNonRoot === true && security.runAsUser === 10001 && security.runAsGroup === 10001 &&
    same(security.seccompProfile, { type: 'RuntimeDefault' }) &&
    containers.length === 1 && containers[0].name === 'workspace' &&
    list(spec.volumes).map(object).every(volume => Object.keys(volume).every(key => ['name', 'emptyDir', 'persistentVolumeClaim'].includes(key))) &&
    same(Object.keys(network).sort(), ['agent-workspace-approved-egress', 'agent-workspace-default-deny']) &&
    same(deny.podSelector, {}) &&
    same([...new Set(list(deny.policyTypes))].sort(), ['Egress', 'Ingress']) &&
    !present(deny.ingress) && !present(deny.egress) &&
    same(approved.podSelector, {}) && same(approved.policyTypes, ['Egress']) && !present(approved.ingress) &&
    egress.some(rule => same(rule, gatewayRule)) && egress.every(rule => same(rule, gatewayRule) || same(rule, dnsRule));
  if (!podValid) return false;
  const container = containers[0];
  const context = object(container.securityContext);
  const template = list(object(object(object(sandbox.spec).podTemplate).spec).containers).map(object)[0];
  return context.allowPrivilegeEscalation === false && context.readOnlyRootFilesystem === true &&
    !present(context.privileged) && same(context.capabilities, { drop: ['ALL'] }) &&
    (context.runAsUser ?? 10001) === 10001 && (context.runAsGroup ?? 10001) === 10001 &&
    (context.runAsNonRoot ?? true) === true &&
    same(context.seccompProfile ?? { type: 'RuntimeDefault' }, { type: 'RuntimeDefault' }) &&
    (context.procMount ?? 'Default') === 'Default' &&
    !present(container.env) && !present(container.envFrom) &&
    typeof container.image === 'string' && container.image === template?.image;
}

/**
 * Refuse a workspace that could escape its boundary: it must be the Sandbox's running pod with one hardened
 * `workspace` container, no service-account token, host access or extra volumes, and only the chart's default-deny
 * and gateway (plus DNS) egress policies. Mirrors the Python SDK's check.
 */
export function validateWorkspace(sandbox: Json, pod: Json, policies: Json, gateway: string): void {
  if (!isolated(sandbox, pod, policies, gateway)) {
    throw ApplicationFailure.nonRetryable(
      'Workspace is not isolated for workflow execution. Use the agent-workspace chart with gateway-only egress, ' +
      'no projected token, and one hardened workspace container; see docs/agent-sandbox-integration.md.',
      'WorkspaceNotIsolated');
  }
}

export interface ContainerStep {
  baseUrl: string;
  apiKey: string;
  agent: string;
  payload: Record<string, unknown>;
  /** Governed run, step and classification headers. */
  headers: Record<string, string>;
  signal?: AbortSignal;
  kubectl?: Kubectl;
}

/**
 * Run an approved command in its existing agent-sandbox workspace through the workspace's Python runner
 * (`python -m agentworkflows.container_runner`, shipped in the worker image). The short-lived grant reaches the
 * runner on stdin only, and the gateway always hears how the step finished, so the grant is revoked even when
 * validation, execution or the activity fails.
 */
export async function runContainer(step: ContainerStep): Promise<Record<string, unknown>> {
  const run = step.kubectl ?? kubectl;
  const path = `/v1/agents/${encodeURIComponent(step.agent)}`;
  const plan = await requestJson<Json>(step.baseUrl, step.apiKey, 'POST', `${path}/start`, step.payload, step.headers, step.signal);
  let result: Json = { exit_code: -1, output: '' };
  let failed = false;
  let failure: unknown;
  try {
    const { namespace, sandbox: name } = plan;
    const seconds = Number(plan.timeout_seconds);
    if (typeof namespace !== 'string' || typeof name !== 'string' || !NAME.test(namespace) || !NAME.test(name) ||
        !Number.isFinite(seconds) || seconds <= 0) {
      throw ApplicationFailure.nonRetryable('The gateway returned an invalid workspace plan; check the agent policy.', 'WorkspaceInvalid');
    }
    const read = async (...args: string[]): Promise<Json> => {
      const output = await run(['-n', namespace, 'get', ...args, '-o', 'json'], { timeoutMs: 20_000, signal: step.signal });
      try {
        return object(JSON.parse(String(output)));
      } catch {
        throw ApplicationFailure.nonRetryable(`kubectl returned unreadable ${args[0]} JSON; check the worker's kubectl.`, 'WorkspaceUnreadable');
      }
    };
    const sandbox = await read('sandbox', name);
    const pod = await read('pod', name);
    const policies = await read('networkpolicies');
    validateWorkspace(sandbox, pod, policies, step.baseUrl);
    const output = await run(
      ['-n', namespace, 'exec', '-i', name, '-c', 'workspace', '--', 'python', '-m', 'agentworkflows.container_runner'],
      {
        input: JSON.stringify({ ...plan, gateway_url: step.baseUrl, headers: step.headers }),
        timeoutMs: (seconds + 10) * 1000, signal: step.signal,
      },
    );
    try {
      result = object(JSON.parse(String(output)));
    } catch {
      throw ApplicationFailure.nonRetryable('The workspace runner returned invalid output; check the agent image.', 'WorkspaceRunnerFailed');
    }
  } catch (error) {
    failed = true;
    failure = error;
  }
  // Never write the grant or raw container output to Temporal history. Finishing ignores cancellation so the
  // grant is revoked; after a failure, that failure is what the step reports.
  const finish = requestJson<Record<string, unknown>>(step.baseUrl, step.apiKey, 'POST', `${path}/finish`,
    { credential_id: plan.credential_id, ...result }, step.headers);
  if (failed) {
    await finish.catch(() => undefined);
    throw failure;
  }
  return finish;
}
