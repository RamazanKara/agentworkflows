# Why AgentWorkflows

AgentWorkflows turns a team’s agent scripts into durable jobs with a shared review,
spend and receipt trail. Use it when people need to see what ran, approve a draft,
and account for model and tool calls from one console.

- **Recover work:** Python and TypeScript workers run on Temporal. Model and tool
  calls are activities; durable workflow history preserves progress and approval waits.
- **Review before proceeding:** a run snapshots its approval rules. One to ten
  distinct reviewers can be required, with an expiry and an explicit rejection path.
- **Share controlled access:** the gateway binds credentials to teams and projects,
  enforces model/tool allowlists and budgets, and supports API keys and company OIDC.
- **Inspect the evidence:** runs link step receipts, estimated usage and optional
  retained content. Trigger history, CSV usage and JSON Lines exports support review.

You still operate Temporal, workers, storage, identity configuration and integrations.
Receipts record gateway-observed activity; they do not prove that a model’s answer is
correct or that an external tool acted safely. Prices are configured estimates, and
retries cannot undo side effects. Start with idempotent tools and reviewed policies.

Choose a smaller gateway if you only need model routing. Keep an existing orchestration
stack if its review and governance already meet your needs. This project is intended
for teams that want these pieces integrated and can own their operation; it is not a
hosted service, a general chat product or a compliance certification.

Try the [quickstart](quickstart.md), browse the [templates](templates.md), and
check the [feature inventory](feature-inventory.md) and [architecture](architecture.md)
before committing to a deployment. See the
[release verification checks](release-verification.md).
