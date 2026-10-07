# Changelog

## Unreleased — Milestone 2

- Temporal-backed Python workflows with governed model/tool activities, retries, timeouts,
  per-run token/cost budgets, provider fallback, approval signals, and run-linked receipts.
- Self-hosted Temporal, dedicated PostgreSQL, persistent budget Redis, and a worker in
  Compose and Helm/GitOps using the official Temporal chart.
- Research → draft → approval → publish example; Compose smoke kills its worker and verifies
  recovery without repeating completed model calls. Added workflow SDK and gateway tests.
- Ten-minute workflow guide covering tool policies, deployment, and recovery boundaries.

## v0.1.0 - 2026-10-07

First AgentWorkflows product version, forked from private-ai-platform-kit with full Git history.

- Governed OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini routes, with
  classification-aware fallback, server-side credentials, usage accounting, and receipts.
- AgentWorkflows naming across the Python distribution and import package, CLI, images,
  Compose project, Helm chart, documentation, and CI.
- A cloud-first local walkthrough using protocol fixtures, with self-hosted models optional.
- Retained gateway, retrieval, sandbox, budget, and audit controls; removed the kit's research
  publication and inherited release evidence. Sample reports remain format examples only.
- Durable workflows and human approvals remain planned; they are not part of this version.

The upstream kit's releases and research remain in its separate repository and this fork's history.
