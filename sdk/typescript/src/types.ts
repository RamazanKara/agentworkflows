export interface ChatCompletion {
  choices: { message: { content?: string | null; [key: string]: unknown } }[];
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  [key: string]: unknown;
}

export interface RunBudget {
  token_limit: number;
  cost_limit_usd: number;
}

export interface Call {
  kind: 'model' | 'tool' | 'approval_waiting';
  payload: Record<string, unknown>;
  budget: RunBudget;
  tool: string;
  data_classification: 'public' | 'internal' | 'confidential' | 'restricted';
}

export interface StartedRun {
  run_id: string;
  workflow_id: string;
  project: string;
  request_id: string;
}

export interface Receipt {
  step_id: string;
  action: string;
  receipt_id: string;
  chain_id: string;
  tokens: number;
  cost_usd: number;
  status_code: number | null;
  receipt: Record<string, unknown>;
}

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  workflow: string;
  project: string;
  status: 'running' | 'completed' | 'failed' | 'canceled' | 'terminated' | 'continued_as_new' | 'timed_out';
  progress?: { stage: string; draft?: string; reviewer?: string; run_id?: string };
  result?: unknown;
  budget: RunBudget & { tokens: number; cost_usd: number };
  timeline?: Receipt[];
}

export interface TriggerRequest {
  workflow: string;
  trigger: string;
  firing_id?: string;
  run_id?: string;
}

export interface TriggerResult {
  paused?: boolean;
  run_id?: string;
  status?: WorkflowRun['status'];
}

export interface GovernedActivities {
  'agentworkflows.call'(call: Call): Promise<Record<string, unknown>>;
  'agentworkflows.trigger'(request: TriggerRequest): Promise<TriggerResult>;
}
