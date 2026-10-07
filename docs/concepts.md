# Workflow concepts

A workflow is a Python function run durably by Temporal. A worker executes its steps;
the AgentWorkflows gateway decides whether each model or tool call is allowed, accounts
for its cost, and writes a receipt. The console, CLI, and SDK share that same gateway API.

| Concept | What it means for your team |
| --- | --- |
| Team | An authenticated sandbox ID with members, allowed providers, and a shared budget |
| Project | A group of runs inside a team; a credential can be restricted to one project |
| Workflow | A named, reviewed implementation with allowed models, tools, destinations, and per-run limits |
| Run | One execution, identified by an exact UUID; retries create a new run and budget |
| Activity | A model, tool, agent, or container step; completed activities are replayed from history after worker failure |
| Approval | A durable pause with a reviewable draft; an admin or approver submits one authenticated decision |
| Receipt | A redacted, hash-chained gateway event with run/step IDs, policy decision, provider, usage, and cost |

## Follow one run

1. A **builder** starts an approved workflow in a project through the gateway.
2. Temporal schedules it on the team's queue (`TEAM-workflows`). Its **worker** executes
   activities using a team-bound credential with `workflows:execute`.
3. Every activity goes through the gateway's allowlists, data classification, secret checks,
   and team/run budgets. Provider credentials stay on the gateway.
4. `ApprovalWorkflow.approval(draft)` exposes the draft in the console and CLI and waits up
   to seven days. An **approver** or **admin** approves or rejects it as their verified identity.
5. Inspect the run's **result**, **budget**, and **timeline**. Export and verify the retained
   audit log when you need evidence beyond receipt IDs in the UI.

The local trial's admin key can perform all human roles for convenience. Separate member
and worker credentials before team use; [team setup](workflows.md#teams-projects-and-roles)
describes the existing policy files. Do not give end users direct Temporal or worker access.

## Durability and limits

Temporal saves completed activity results. A worker restart resumes waiting approval and
completed steps without repeating those results. An activity interrupted during an external
action may run again: approved tools must honor the supplied idempotency key. Container
steps do not automatically retry. Cancellation cannot undo a tool action already sent.

The tighter of the workflow's requested budget and the team's workflow policy applies.
All runs also consume the shared team budget. Accounting uses configured token prices and
conservative reservations, not provider invoices. Failed attempts can still cost money.

Receipts detect changes and internal gaps in retained chains. They do not prove an
unreported action, and external anchors are needed to detect complete rewrites or truncation.
The fake produces synthetic answers, tool results, and prices. It proves the governed path,
not research quality or live provider access.

Continue with [templates](templates.md), [architecture](architecture.md),
[security boundaries](security-overview.md), and the [workflow guide](workflows.md).
