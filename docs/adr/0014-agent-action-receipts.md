# 0014. Agent-action receipts on the audit chain

- Status: Accepted
- Date: 2026-08-04
- Deciders: Platform maintainer

## Context

AgentWorkflows' promise is that you can prove what a coding agent did. The audit chain records
every model call (`action_type: "model_call"`) as a complete, tamper-evident record of what an
agent asked a model. The next step is to record what the agent then did with the answer.

The project's own demo shows why. The most persuasive moment in `make agent-sandbox-demo` is the
blocked exfiltration attempt, enforced by a NetworkPolicy packet drop. An auditor asking "show me
every time this agent tried to reach the internet" wants an answer `make audit-verify` can check
offline. The same holds for the actions that matter most in a workspace: commands executed, files
written, credentials requested.

The enforcement for these lives outside the gateway. Default-deny networking governs egress, the
opt-in Falco/Tetragon application watches workload behavior, and the workspace is a hardened
sandbox. This decision adds the evidence trail that ties those controls to the same verifiable
chain as the model calls.

## Decision

Add an authenticated receipt intake to the gateway: `POST /v1/receipts`, off by default
(`AGENT_RECEIPTS_ENABLED`). It accepts a typed agent action and links it into the same
per-process hash chain as model calls, with the same redaction discipline and the same operator
verifier.

The action taxonomy is closed and small: `egress_denied`, `egress_allowed`, `tool_exec`,
`file_write`, `credential_request`, `workspace_lifecycle`. The intake chains only recognized
action types, so the taxonomy stays a reviewed vocabulary that every report can aggregate.

The **boundary is the important part of this decision**: the gateway accepts and chains
receipts as audit records. A submitted receipt is a claim by the sandbox about something that
already happened, recorded so it can be audited. The NetworkPolicy, the Kyverno policy, or the
runtime-security agent decides whether an action is blocked, exactly as before, and the gateway
grants permissions only through its own authorization.

Because a receipt is an attributable claim, the intake is bound to the caller's identity the
same way every other endpoint is: the sandbox on the receipt comes from the caller's bound
sandbox (the audience-bound workspace token or an API-key record), and a receipt claiming a
different sandbox is rejected. Each workspace writes only to its own history.

Producers are whatever the operator already runs. Falco and Tetragon alerts, CNI denied-flow
logs, and an agent's own tool hooks are all callers of this endpoint; the platform ships the
intake, the taxonomy, the chaining, and the verification, and the operator's existing tooling
does the collection.

## Consequences

- "Prove what it did" becomes checkable offline for more than model calls: `make audit-verify`
  verifies agent actions and model calls in one chain, and a deleted action receipt breaks the
  chain like any other record.
- The operator chooses which producers feed the receipt stream, and that choice defines its
  coverage. AgentWorkflows provides integrity; the operator's producer set provides completeness.
- Receipts are claims reported by the sandbox. Out-of-band producers (Falco, CNI) report actions
  independently of the agent, which is why the boundary above matters. The chain makes tampering
  with reported history detectable.
- The intake is an opt-in endpoint with a bounded body, its own rate limit path, and a closed
  vocabulary, so an enabled intake stays a bounded audit channel.

## Alternatives considered

- **A separate receipts service.** Cleaner separation. The chain is per-process and the operator
  verifier groups by `chain_id`, so keeping receipts in the gateway chain gives one chain to
  anchor and correlate in the single-cluster topology AgentWorkflows targets. (The RAG service runs
  its own chain as an independently deployed service with its own lifecycle.)
- **Deriving receipts from Falco alerts inside the gateway.** An intake anything can post to was
  chosen so the gateway stays a governance service, independent of any one runtime-security stack.
- **Free-form `action_type` strings.** Simpler to accept. A closed taxonomy was chosen because the
  crosswalk and evidence pack aggregate by action type and the vocabulary stays reviewable.
- **Trusting a submitted `sandbox_id`.** Binding the sandbox to the caller's identity was chosen
  because tenant-scoped history is the property that gives the chain its value.
- **Forwarding logs to a SIEM only.** The receipt intake was chosen because the differentiator is
  verifiable evidence an auditor can check offline.
