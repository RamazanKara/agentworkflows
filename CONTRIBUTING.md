# Contributing

Changes should keep service behavior, local and customer deployments, documentation,
and release checks aligned. Use the [repository map](docs/repository-map.md) to find
the right component and the [developer workflow](docs/development.md) for setup,
focused tests, generated files, and troubleshooting.

## Start locally

Service and tooling development does not require a Kubernetes cluster. From the
repository root in Linux or WSL:

```bash
make help
make test
make quality
```

Use `make test-gateway`, `make test-rag`, or `make test-scripts` while iterating.
Run the gateway and RAG tests in separate interpreter processes because both
services use a package named `app`.

For documentation changes:

```bash
make docs-install
make repo-hygiene
make docs-build
```

CI runs one Ubuntu job on pushes to `main` or manual dispatch: Ruff lint, tooling,
gateway, RAG and SDK unit tests, the console build, and an SDK import check. With
Python 3.14 and Node.js 24, run the same checks locally:

```bash
RAYON_NUM_THREADS=2 make -j2 lint
make -j2 test-scripts test-gateway test-rag
PYTHONPATH=sdk/python src/inference-gateway/.venv/bin/python -c 'from agentworkflows import GatewayClient; print(GatewayClient.__name__)'
```

Full validation, browser/end-to-end tests, image builds, and security scans are
local opt-in checks. `make validate-full` requires the strict toolchain described
in the [developer workflow](docs/development.md). For a full check and the Docker
Compose smoke test:

```bash
make -j2 validate-full
make compose-up
make compose-smoke
make compose-down
```

`make compose-up` builds the gateway, RAG, and workflow-worker images sequentially
before starting the stack. To build just the images without starting services:

```bash
docker compose -f deploy/compose/compose.yaml build inference-gateway
docker compose -f deploy/compose/compose.yaml build rag-service
docker compose -f deploy/compose/compose.yaml build workflow-worker
```

`make test-console`, `make fuzz`, `make image-scan`, and `make repo-security-scan`
also remain available locally. Use the [quickstart](docs/quickstart.md) when your
change needs a running cluster.

Ruff and mypy use an isolated, hash-pinned `.venv-quality`; their configuration is
in [pyproject.toml](pyproject.toml). `make format` formats service code and applies
Ruff's safe lint fixes across the repository. Optional
[pre-commit hooks](.pre-commit-config.yaml) run the same tools.

## Change standards

User experience is part of every capability: use sensible defaults, one obvious path,
actionable errors, and copy-pasteable examples a team lead can follow in ten minutes.
Remove friction before adding options.

- Keep runtime and test dependencies separate. Regenerate the associated hashed
  locks whenever requirement pins change, including tooling locks.
- Keep Docker base images pinned by digest. Run `make image-scan` and
  `make repo-security-scan` for changes to images or runtime dependencies.
- Put HTTP regression tests beside the service and repository-tooling tests under
  `scripts/tests/`. Use fake backends and temporary data for local tests.
- Keep `make quality` passing. Test the behavior affected by the change, including
  failure paths for authentication, policy, budgets, and tenant isolation.
- Regenerate API contracts with `make api-contract-update` after changing public
  routes or schemas, and configuration contracts with `make config-contract-update`
  after changing settings, Helm environment variables, or chart defaults.
- Update chart value tables with `make chart-docs-update` when values change.
  Review generated diffs along with their source changes.
- Keep new commands discoverable through `make help` and the relevant guide or
  runbook. Add site pages to `mkdocs.yml` and the appropriate documentation index.
- Keep root-level `scripts/*.py` and `scripts/*.sh` executable in Git; use
  `git update-index --chmod=+x <path>` for a mode-only fix.
- Keep generated evidence under `results/` ignored unless it is an intentional
  `sample-*` artifact. Do not commit secrets, raw prompts, customer data, local
  kubeconfigs, or generated tenant output.
- Record significant architectural decisions in an [ADR](docs/adr/README.md).
  Add a validation check when introducing a new operational invariant.

## Prepare a review

Explain the problem, resulting behavior, and validation performed. Use the
[pull request template](.github/PULL_REQUEST_TEMPLATE.md), and record user-visible
changes under an unreleased entry in [CHANGELOG.md](CHANGELOG.md).

Reviewers should check that:

- API, chart, and GitOps behavior stay compatible across local and customer profiles,
  or include migration instructions.
- Security controls remain enforceable through tests, policy, or validation scripts.
- New runbooks identify the operator action, validation command, and rollback or
  escalation path.
- Customer-facing values avoid cloud-specific assumptions unless a named profile
  makes the dependency explicit.

## Release readiness

Before a release or a production-readiness handoff, use the strict path with a
running target environment:

```bash
make validate-full
make image-scan
make evidence LIVE=1
make release-gate-strict
make release-report-strict
```

The non-strict `make release-gate` target can use sample evidence for local
configuration checks. It does not establish readiness for a current deployment.
See [release gates](runbooks/release-gates.md) for required evidence.

## Community workflow

Use the issue templates for bugs, feature requests, and questions. Discuss larger
design changes in an issue before implementing them. Follow
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [GOVERNANCE.md](GOVERNANCE.md), and
[ROADMAP.md](ROADMAP.md). Report vulnerabilities through [SECURITY.md](SECURITY.md)
and keep public issues free of private data.
