import {
  ApplicationFailure, condition, defineQuery, defineSignal, defineUpdate,
  patched, proxyActivities, setHandler, sleep, workflowInfo,
} from '@temporalio/workflow';
import type { ActivityOptions } from '@temporalio/workflow';
import type { ApprovalProgress, Call, ChatCompletion, GovernedActivities, TriggerRequest, TriggerResult } from './types';

export type InputProperty = {
  type: 'string' | 'number' | 'integer' | 'boolean' | 'array';
  description?: string;
  default?: string | number | boolean | string[];
  examples?: (string | number | boolean | string[])[];
  enum?: (string | number | boolean | string[])[];
  items?: { type: 'string' };
  minLength?: number; pattern?: string;
  minimum?: number; maximum?: number; exclusiveMinimum?: number;
};

export type InputSchema = {
  $schema?: 'https://json-schema.org/draft/2020-12/schema';
  type: 'object';
  properties: Record<string, InputProperty>;
  required?: string[];
  description?: string;
  additionalProperties?: boolean;
};

export function withInputSchema<T extends (...args: never[]) => Promise<unknown>>(schema: InputSchema, workflow: T): T & { inputSchema: InputSchema } {
  return Object.assign(workflow, { inputSchema: schema });
}

export class Budget {
  constructor(readonly tokenLimit = 10_000, readonly costLimitUsd = 5) {
    if (!Number.isInteger(tokenLimit) || tokenLimit < 1 || tokenLimit > 1_000_000_000 ||
        !Number.isFinite(costLimitUsd) || costLimitUsd <= 0 || costLimitUsd > 1_000_000) {
      throw ApplicationFailure.nonRetryable('Budget requires 1–1000000000 tokens and a finite USD limit > 0 and <= 1000000.', 'InvalidBudget');
    }
  }
}

export interface GatewayOptions {
  dataClassification?: Call['data_classification'];
  startToCloseTimeout?: ActivityOptions['startToCloseTimeout'];
  scheduleToCloseTimeout?: ActivityOptions['scheduleToCloseTimeout'];
  retry?: ActivityOptions['retry'];
}

export class WorkflowGateway {
  private readonly activities: GovernedActivities;

  constructor(readonly budget = new Budget(), private readonly options: GatewayOptions = {}) {
    this.activities = proxyActivities<GovernedActivities>({
      startToCloseTimeout: options.startToCloseTimeout ?? '3 minutes',
      scheduleToCloseTimeout: options.scheduleToCloseTimeout ?? '15 minutes',
      retry: options.retry ?? {
        initialInterval: '1 second', backoffCoefficient: 2, maximumInterval: '30 seconds', maximumAttempts: 5,
      },
    });
  }

  private call(kind: Call['kind'], payload: Call['payload'], tool = ''): Promise<Record<string, unknown>> {
    return this.activities['agentworkflows.call']({
      kind, payload, tool,
      budget: { token_limit: this.budget.tokenLimit, cost_limit_usd: this.budget.costLimitUsd },
      data_classification: this.options.dataClassification ?? 'internal',
    });
  }

  async model(messages: Record<string, unknown>[], options: { model?: string; maxTokens?: number } = {}): Promise<ChatCompletion> {
    return await this.call('model', {
      messages, max_tokens: options.maxTokens ?? 512, ...(options.model ? { model: options.model } : {}),
    }) as unknown as ChatCompletion;
  }

  async text(prompt: string, options: { model?: string; maxTokens?: number } = {}): Promise<string> {
    const reply = await this.model([{ role: 'user', content: prompt }], options);
    return reply.choices[0].message.content ?? '';
  }

  async tool<T = unknown>(name: string, args: Record<string, unknown>): Promise<T> {
    const reply = await this.call('tool', { arguments: args }, name);
    return reply.result as T;
  }
}

export const approveSignal = defineSignal<[boolean, string]>('approve');
export const reviewUpdate = defineUpdate<boolean, [boolean, string]>('review');
export const statusQuery = defineQuery<ApprovalProgress>('status');

export class ApprovalWorkflow {
  stage = 'running';
  draft = '';
  decision: boolean | undefined;
  reviewer = '';
  requiredApprovals = 1;
  approvedBy: string[] = [];
  expiresAt?: number;
  approverRole: 'admin' | 'approver' = 'approver';
  approvalPolicyVersion?: 1;

  constructor() {
    setHandler(approveSignal, (approved, reviewer) => this.approve(approved, reviewer));
    setHandler(reviewUpdate, (approved, reviewer) => this.review(approved, reviewer));
    setHandler(statusQuery, () => this.status());
  }

  async approval(draft: string): Promise<boolean> {
    if (this.stage !== 'running') {
      throw ApplicationFailure.nonRetryable('Call approval once per run.', 'ApprovalAlreadyRequested');
    }
    this.draft = draft;
    this.stage = 'awaiting_approval';
    const versioned = patched('approval-policy-v1');
    if (versioned) this.stage = 'configuring_approval';
    const activities = proxyActivities<GovernedActivities>({
      startToCloseTimeout: '3 minutes', scheduleToCloseTimeout: '15 minutes',
      retry: { initialInterval: '1 second', backoffCoefficient: 2, maximumInterval: '30 seconds', maximumAttempts: 5 },
    });
    const gate = await activities['agentworkflows.call']({
      kind: 'approval_waiting', payload: versioned ? { policy_version: 1 } : {}, tool: '',
      budget: { token_limit: 10_000, cost_limit_usd: 5 }, data_classification: 'internal',
    });
    let timeout = 604800;
    if (versioned) {
      if (gate.policy_version !== 1) throw ApplicationFailure.nonRetryable('Upgrade the gateway to 0.9.0 before the worker SDK.', 'ApprovalPolicyUnsupported');
      this.approvalPolicyVersion = 1;
      this.requiredApprovals = Number(gate.required_approvals ?? 1);
      this.approverRole = gate.approver_role === 'admin' ? 'admin' : 'approver';
      timeout = Number(gate.approval_timeout_seconds ?? 604800);
      this.expiresAt = Date.now() + timeout * 1000;
      this.stage = 'awaiting_approval';
      if (gate.approval_required === false) { this.decision = true; this.reviewer = 'team policy'; }
    }
    if (!await condition(() => this.decision !== undefined, versioned ? timeout * 1000 : '7 days')) {
      this.stage = 'expired';
      throw ApplicationFailure.nonRetryable('Approval expired; start a new review.', 'ApprovalExpired');
    }
    this.stage = this.decision ? 'approved' : 'rejected';
    return this.decision === true;
  }

  approve(approved: boolean, reviewer: string): void {
    if (this.stage === 'awaiting_approval' && this.decision === undefined &&
        typeof approved === 'boolean' && typeof reviewer === 'string' && reviewer.trim() &&
        !this.approvedBy.includes(reviewer.trim()) && (this.expiresAt === undefined || Date.now() < this.expiresAt)) {
      this.reviewer = reviewer.trim();
      if (approved) this.approvedBy.push(this.reviewer);
      if (!approved || this.approvedBy.length >= this.requiredApprovals) this.decision = approved;
    }
  }

  review(approved: boolean, reviewer: string): boolean {
    if (this.stage !== 'awaiting_approval' || this.decision !== undefined || this.approvedBy.includes(reviewer)) return false;
    const count = this.approvedBy.length;
    this.approve(approved, reviewer);
    return this.decision !== undefined || this.approvedBy.length > count;
  }

  status(): ApprovalProgress {
    return {
      stage: this.stage, draft: this.draft, reviewer: this.reviewer, run_id: workflowInfo().runId,
      ...(this.approvalPolicyVersion ? {
        approval_policy_version: this.approvalPolicyVersion, required_approvals: this.requiredApprovals,
        approved_by: [...this.approvedBy], expires_at: new Date(this.expiresAt!).toISOString(), approver_role: this.approverRole,
      } : {}),
    };
  }
}

export async function AgentWorkflowsTrigger(input: TriggerRequest): Promise<TriggerResult> {
  const request = { ...input, firing_id: workflowInfo().runId };
  const activities = proxyActivities<GovernedActivities>({
    startToCloseTimeout: '45 seconds', scheduleToCloseTimeout: '10 minutes',
    retry: { maximumInterval: '30 seconds' },
  });
  for (;;) {
    const result = await activities['agentworkflows.trigger'](request);
    if (result.paused || (result.status && ['completed', 'failed', 'canceled', 'terminated', 'timed_out'].includes(result.status))) {
      return result;
    }
    request.run_id = result.run_id;
    // Keeping the action open lets Temporal's SKIP overlap policy cover the target run.
    await sleep('30 seconds');
  }
}
