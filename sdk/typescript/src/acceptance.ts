import type { GatewayClient } from './client';
import { GatewayError } from './errors';

export const DEADLINE_SECONDS = 300;
const FAILED = new Set(['failed', 'canceled', 'terminated', 'timed_out']);

export interface AcceptanceStage { stage: string; seconds: number; detail: string }
export interface AcceptanceResult {
  ok: true; run_id: string; total_seconds: number; deadline_seconds: number; stages: AcceptanceStage[]; notes: string[];
}
export interface AcceptanceOptions {
  /** Reviewers who approve after `gateway`, for approval policies that need several distinct identities. */
  approvers?: GatewayClient[];
  deadlineSeconds?: number;
  /** Allow the sample to call a real provider that bills your account. */
  allowPaid?: boolean;
  pollSeconds?: number;
  now?: () => number;
  sleep?: (milliseconds: number) => Promise<void>;
  onStage?: (stage: AcceptanceStage) => void;
}

/** The install did not complete a governed run; `stage` says where and `stages` what passed. */
export class AcceptanceError extends Error {
  constructor(readonly stage: string, message: string, readonly stages: AcceptanceStage[]) {
    super(message);
    this.name = 'AcceptanceError';
  }
}

/**
 * Prove an installation completes a governed run, following the console wizard: read readiness, start the
 * team's sample, wait for its draft, approve it, then confirm completion, the approval receipt, the run summary
 * and (for admins) audit-chain verification. Throws AcceptanceError at the first failing stage, including
 * when the deadline passes. Works against any gateway.
 */
export async function firstApprovedRun(gateway: GatewayClient, options: AcceptanceOptions = {}): Promise<AcceptanceResult> {
  const now = options.now ?? (() => performance.now());
  const sleep = options.sleep ?? ((milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds)));
  const deadline = options.deadlineSeconds ?? DEADLINE_SECONDS;
  const poll = (options.pollSeconds ?? 1) * 1000;
  const started = now();
  const elapsed = () => (now() - started) / 1000;
  const stages: AcceptanceStage[] = [];
  const notes: string[] = [];
  const done = (stage: string, detail: string) => {
    const row = { stage, seconds: Math.round(elapsed() * 10) / 10, detail };
    stages.push(row);
    options.onStage?.(row);
  };
  const fail = (stage: string, message: string) => new AcceptanceError(stage, message, stages);
  const wait = async <T>(stage: string, what: string, observe: () => Promise<T | undefined>): Promise<T> => {
    for (;;) {
      const seen = await observe();
      if (seen !== undefined) return seen;
      if (elapsed() >= deadline) throw fail(stage, `Timed out after ${deadline} seconds waiting for ${what}.`);
      await sleep(poll);
    }
  };
  const attempt = async <T>(stage: string, message: string, call: () => Promise<T>): Promise<T> => {
    try { return await call(); } catch (error) {
      if (error instanceof GatewayError) throw fail(stage, `${message} (${error.message})`);
      throw error;
    }
  };

  const setup = await attempt('ready', 'Could not read readiness.', () => gateway.onboarding());
  const sample = setup.sample;
  if (setup.blockers.length || !sample || !sample.ready) {
    throw fail('ready', `Not ready for a first run. ${setup.blockers.join(' ') || 'The team has no sample workflow.'}`);
  }
  const model = typeof sample.input.model === 'string' ? sample.input.model : undefined;
  const routes = (await attempt('ready', 'Could not list models.', () => gateway.models())).data;
  const simulated = Boolean(routes.find(route => route.id === model)?.simulated);
  if (!simulated && !options.allowPaid) {
    throw fail('ready', `The sample would call ${model ?? 'a model'}, which is not simulated, so it can spend up to the workflow cost limit on your provider account. Re-run with allowPaid to accept that.`);
  }
  done('ready', `Sample ${sample.template_id} ${sample.version} on ${model ?? 'the default model'}${simulated ? ' (simulated)' : ' (real provider)'}`);

  if (sample.installed) done('installed', 'Already installed');
  else {
    await attempt('installed', 'The sample template is not installed and this credential cannot install it. Ask a team admin, or use an admin key.',
      () => gateway.installTemplate(sample.template_id, sample.version));
    done('installed', `Installed ${sample.template_id} ${sample.version}`);
  }

  const { run_id: runId } = await attempt('started', 'Could not start the sample.', () => gateway.startRun(sample.workflow, sample.input));
  done('started', `Run ${runId}`);

  const waiting = await wait('drafted', 'the draft to await approval', async () => {
    const detail = await gateway.run(runId);
    if (FAILED.has(detail.status)) {
      throw fail('drafted', `The run ended as ${detail.status} (${detail.error?.code ?? 'no code'}). Inspect its worker logs and step receipts.`);
    }
    if (detail.status === 'completed') throw fail('drafted', 'The run finished without pausing for approval; check its approval policy.');
    return detail.progress?.stage === 'awaiting_approval' ? detail : undefined;
  });
  const needed = waiting.progress?.required_approvals ?? 1;
  done('drafted', `Awaiting ${needed} ${needed === 1 ? 'approval' : 'approvals'}`);

  const reviewers = [gateway, ...(options.approvers ?? [])];
  if (reviewers.length < needed) {
    throw fail('approved', `This run needs ${needed} distinct reviewers but ${reviewers.length} credential(s) were given. Pass ${needed - reviewers.length} more reviewer key(s).`);
  }
  for (const reviewer of reviewers.slice(0, needed)) {
    await attempt('approved', 'A credential could not approve. Reviewers need the admin or approver role.', () => reviewer.approveRun(runId));
  }
  done('approved', `Approved by ${needed} ${needed === 1 ? 'reviewer' : 'reviewers'}`);

  const final = await wait('completed', 'the run to finish after approval', async () => {
    const detail = await gateway.run(runId);
    if (FAILED.has(detail.status)) {
      throw fail('completed', `The run ended as ${detail.status} (${detail.error?.code ?? 'no code'}) after approval.`);
    }
    return detail.status === 'completed' ? detail : undefined;
  });
  const outcome = (final.result as { status?: string } | undefined)?.status;
  if (outcome !== 'published') throw fail('completed', `The run completed with outcome ${outcome ?? 'unknown'}, not published.`);
  done('completed', 'Published');

  const timeline = final.timeline ?? [];
  if (!timeline.some(step => step.action === 'approval')) throw fail('evidence', 'The completed run has no approval receipt in its timeline.');
  if (final.summary === undefined) notes.push('The gateway did not return a run summary; it predates run insights.');
  done('evidence', `${timeline.length} receipts, including the approval`);

  try {
    const verification = await gateway.verifyAudit();
    if (verification.enabled === false) {
      notes.push('The audit chain is disabled on this gateway; enable it before production use.');
      done('audit', 'Disabled');
    } else if (verification.ok !== true || verification.checked < 1) {
      throw fail('audit', `The retained audit chain did not verify: ${JSON.stringify(verification.first_break)}`);
    } else done('audit', `Verified ${verification.checked} events`);
  } catch (error) {
    if (!(error instanceof GatewayError)) throw error;
    if (error.statusCode !== 403) throw fail('audit', `Audit verification failed to run: ${error.message}`);
    notes.push('Audit verification skipped: it needs a team admin credential.');
    done('audit', 'Skipped (admin credential required)');
  }

  const total = Math.round(elapsed() * 10) / 10;
  if (total > deadline) throw fail('completed', `The run succeeded but took ${total} seconds, over the ${deadline}-second budget.`);
  return { ok: true, run_id: runId, total_seconds: total, deadline_seconds: deadline, stages, notes };
}
