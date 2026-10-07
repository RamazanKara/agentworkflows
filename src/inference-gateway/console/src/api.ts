import { useEffect, useState } from 'react';

export type Team = {
  team_id: string;
  role: 'admin' | 'builder' | 'approver' | 'viewer';
  projects: string[];
  providers: string[];
  cost_limit_usd: number | null;
  provider_configuration?: Record<string, { environment_variable: string; configured: boolean }>;
};
export type Session = { token: string; team: Team; id: number };
export type Policy = { allowedModels: string[]; allowedProviders: string[]; tokenLimit: number; costLimitUsd: number };
export type Step = {
  step_id: string; action: string; provider: string; model: string; tool: string;
  tokens: number; cost_usd: number; duration_ms: number; timestamp: number;
  status_code: number | null; receipt_id: string; chain_id: string;
  attempts: unknown[]; receipt: Record<string, unknown>;
};
export type Run = {
  run_id: string; workflow: string; project: string; created_at: number; status: string;
  progress?: { stage: string; draft?: string; message?: string };
  budget: { tokens: number; cost_usd: number; token_limit: number; cost_limit_usd: number };
  timeline?: Step[];
};
export type RunPage = { runs: Run[]; next_offset: number | null };
export type CostRow = { calls?: number; tokens?: number; cost_usd?: number };
export type Usage = {
  estimated_cost: number;
  spend: {
    project: string | null; window_start: number; window_seconds: number;
    reserved_and_spent_usd: number | null; cost_limit_usd: number | null;
    providers: Record<string, CostRow>; workflows: Record<string, CostRow>; accounting: string;
  };
};
export type Budget = { usage: { estimated_tokens: number }; limits: { estimated_tokens: number } };
export type Models = { data: { id: string; owned_by: string }[] };

export async function api<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init, cache: 'no-store', credentials: 'omit',
      headers: { Authorization: `Bearer ${token}`, ...(init.body ? { 'Content-Type': 'application/json' } : {}) },
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new Error('Cannot reach the gateway. Check your connection, then try again.');
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/v1/team') window.dispatchEvent(new Event('aw:expired'));
    const detail = body.error?.message || body.detail?.message || (typeof body.detail === 'string' ? body.detail : '');
    const advice: Record<number, string> = {
      401: 'Credential expired or invalid. Sign in with a valid team API key or JWT.',
      403: 'Your role cannot perform this action. Ask your team admin for the required access.',
      404: 'Not found in this team or project. The run may be outside Temporal retention.',
      409: 'This run changed. Refresh and review its current state before trying again.',
      422: 'Check the workflow input and selected project, then try again.',
      503: 'The service is unavailable. Check the gateway, Redis, and Temporal, then retry.',
    };
    throw new Error(`${detail || advice[response.status] || 'The request failed.'} (${response.status})`);
  }
  return body as T;
}

export function useData<T>(token: string, path: string, revision = 0) {
  const [state, setState] = useState<{ data?: T; error?: string }>({});
  useEffect(() => {
    const controller = new AbortController();
    setState(previous => ({ data: previous.data }));
    api<T>(token, path, { signal: controller.signal }).then(
      data => { if (!controller.signal.aborted) setState({ data }); },
      error => { if (!controller.signal.aborted) setState({ error: error.message }); },
    );
    return () => controller.abort();
  }, [token, path, revision]);
  return state;
}

export const money = (value: number | null | undefined) => value == null ? '—' : `$${value.toFixed(4)}`;
export const number = (value: number | undefined) => (value ?? 0).toLocaleString();
export const date = (value: number) => new Date(value * 1000).toLocaleString();
export const status = (run: Run) => run.progress?.stage === 'awaiting_approval' ? 'awaiting_approval' : run.status;
export const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
