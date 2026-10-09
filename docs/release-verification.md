# Release Verification

Use this checklist before trusting a public release in a customer-owned cluster.

For the source candidate (package/chart version 0.9.0), run the local gate:

```bash
make lint test-gateway test-scripts api-contract config-contract chart-docs
make test-typescript test-console
```

Build Helm dependencies, then run `helm lint` and `helm template` for the charts
being shipped. After a console build, repeat the screenshot captures with
`npm test -- --grep 'release console layouts'` in `src/inference-gateway/console`.
Screenshots use synthetic UI fixtures; the separate Compose smoke test exercises
the live gateway.

On native Windows without WSL, run the console's `npm run build` and `npm test`,
the TypeScript SDK's `npm run build`, `npm run lint` and `npm test`, and Python
Ruff, SDK tests and contract scripts from their installed environments. The Bash
gates still need a Linux caller: `make lint test-gateway test-scripts`. Screenshot
tests do not verify live providers, Redis, Temporal or PostgreSQL integration.

## rc.4 adoption pass

2026-10-09, native Windows. All five plans were recorded in
[PRODUCT-GAPS.md](https://github.com/RamazanKara/agentworkflows/blob/main/docs/PRODUCT-GAPS.md)
before implementation. No dependencies were added and no commit, push, PR or publication was made.

| Native check | Result |
| --- | --- |
| Gateway full suite | **1,101 passed, 27 skipped**. Infrastructure/optional skips remain; no live PostgreSQL or Temporal deployment is implied. |
| Python SDK | **215 passed, 3 skipped** (optional frameworks). |
| TypeScript SDK | Build and lint passed; **86 tests passed**. |
| Console | Production build passed; **80 Playwright tests passed**. Final capture-only rerun: **3 passed**. |
| Contracts | OpenAPI snapshot generation/check and configuration contract check passed. |
| Helm | Dependencies resolved; **20 tests and 8 schema subtests passed**. Single-tenant profile passed strict Helm lint. |
| Ruff | Every changed Python file passes lint. Repository-wide lint still fails on the untouched `tests/test_approval_policies.py:67` E501. Repository formatter check reports 32 files; no unrelated formatting cleanup was made. |
| Docs | Strict MkDocs build still fails on two existing `../runbooks/observability.md` links in this file and `team-lifecycle.md`. New landing/reference links and screenshots resolve. |
| Compose quickstart | Attempted the README command; native `docker` is unavailable, including the usual Windows installation paths. WSL was not used. **No end-to-end run or 60-second timing claim was verified here.** |

Python checks used installed native environments with `PYTHONPATH` pointing at this
checkout for SDK tests and bytecode writes disabled. Browser tests used the production
Vite preview on port 4175 and the existing Playwright suite through a temporary config
that omitted its web-server launcher; the launcher hung during Windows teardown.
The preview process was stopped after verification.

The capture test now writes the README's [1440 px screenshot](assets/console-1440.png).
Reviewed [360 px](assets/console-360.png) and [393 px](assets/console-393.png) run captures
are retained as well. The complete capture set is generated under
`.out/console-v1.0.0-rc.4/`. Review covered first-run provider setup, templates,
membership/SSO, approval/timeline, alert rules and deployment checks. Layout assertions
check viewport overflow, mid-word wraps, placeholder fixture text, totals and identities.
All screenshots use the same seeded Insights team; they are UI evidence, not live service evidence.

### Caller acceptance still required

Run these from this checkout on a Docker-capable Linux host or in the caller's WSL
environment, with Python 3.12+, Docker/Compose, Helm, kind and kubectl installed:

```bash
docker compose -f deploy/compose/compose.yaml up --build -d --wait workflow-worker
python3 scripts/first-approved-run.py compose
python3 scripts/template-smoke.py
python3 scripts/trigger-smoke.py
```

The first-run script has a 300-second deadline after image preparation. Time the manual
sign-in → sample → approval walkthrough separately before publishing a measured
60-second result. Test all nine template inputs and the provider-key wizard against
the running gateway, then verify delivery in the local notification fakes.

For the isolated kind path:

```bash
docker build -t agentworkflows-gateway:quickstart src/inference-gateway
docker build -f sdk/python/Dockerfile -t agentworkflows-worker:quickstart .
helm dependency build deploy/charts/workflows
helm dependency build deploy/charts/agentworkflows
python3 scripts/first-approved-run.py kind
```

Run the existing recovery and upgrade drills against their disposable environments:

```bash
python3 scripts/workflow-recovery.py drill
python3 scripts/workflow-recovery.py upgrade
python3 scripts/helm-upgrade-test.py
```

These drills create and remove their own stacks/data. The upgrade drill's historical
baseline is pinned in its script; also stage the actual rc.3 → rc.4 customer upgrade.
Follow the [single-tenant reference](single-tenant.md) to verify managed-store TLS,
network isolation, backup restore, real company sign-in and approved notification
destinations. Use test accounts and a capped provider budget. None of that live
infrastructure acceptance was replaced by mocks or static chart rendering.

## rc.2 lifecycle pass

2026-10-09, native Windows, Python 3.12.14, Node 24.19.0, portable Helm 3.18.6.
This is unreleased source work: no commit, push, PR or publication, and no new dependencies.

| Native check | Result |
| --- | --- |
| Gateway full suite | 1,082 passed, 26 skipped; final lifecycle/stream-erasure/concurrency/run-audit/quickstart/telemetry subset: 59 passed. Six optional PostgreSQL tests require the caller database. |
| Python SDK | 202 passed, 3 optional-framework skips. |
| TypeScript SDK build, lint, tests | Passed; 79 tests. |
| Console build, Playwright | Passed; 76 tests with the same Windows preview workaround described below. Final capture-only rerun: 3 passed. |
| OpenAPI/config contracts, chart docs, dashboards | Passed; generated snapshots updated. |
| Helm dependencies, strict kind-profile lint/render | Passed; 19 umbrella chart tests, including encryption Secret references and OTLP flags. |
| Ruff | Changed Python files pass. Repository lint still reports the untouched E501 at `test_approval_policies.py:67`; formatter also finds existing repository drift. |
| Gateway mypy | 26 existing errors in 13 files, identical to a clean HEAD comparison; no new findings. |
| Scripted quickstarts | Native CLI and mocked approval/audit checks pass; live Compose/kind runs are blocked as described below. |

The **107 PNGs** in `.out/console-v1.0.0-rc.2/` cover 360/393/1440, including the gallery,
installation, secret rotation, retention/export/telemetry, cancellation confirmation, retry
and operation receipts. Layout assertions check overflow and mid-word wrapping; visual review
checks readable controls and spacing. Screens use one Insights/Maya Chen fixture. The canceled
run retains 4,200 tokens/$0.06 and gains two operation receipts; its retry starts with zero
tokens, spend and receipts. Template descriptions match the actual catalog. Downloads and
screenshots are synthetic UI evidence, not live backend proof.

The native sandbox blocks Playwright-managed Vite teardown. A temporary ignored config at
`.out/playwright-rc2.config.ts` disables only `webServer`, uses the same tests/locale/timezone,
and runs against a separately started Vite preview. No permanent test configuration changed.

### Caller checks in WSL

Native Docker is absent. `kind get clusters` cannot find Docker; `wsl.exe -d Ubuntu -e docker version`
returns `Wsl/Service/E_ACCESSDENIED`, and `kubectl config current-context` reports no context.
No live first-run timing, cluster mutation, real PostgreSQL deletion, or collector delivery
is claimed. From a Linux/WSL checkout with Docker, Python 3.12+, Helm, kind and kubectl:

```sh
docker compose -f deploy/compose/compose.yaml build
docker compose -f deploy/compose/compose.yaml pull --ignore-buildable
python3 scripts/first-approved-run.py compose

docker build -t agentworkflows-gateway:quickstart src/inference-gateway
docker build -f sdk/python/Dockerfile -t agentworkflows-worker:quickstart .
helm dependency build deploy/charts/workflows
helm dependency build deploy/charts/agentworkflows
python3 scripts/first-approved-run.py kind

make test-gateway test-scripts test-typescript test-console
```

The scripts fail if the prepared trial exceeds 300 seconds. Image preparation/downloads are
outside that clock. Record their output and inspect the approved run and verified audit chain.
For gateway SQL erasure/retention integration, point `TEST_POSTGRES_DSN` at a **disposable**
PostgreSQL database where the test user can create schemas, then run:

```sh
PYTHONPATH=src/inference-gateway src/inference-gateway/.venv/bin/python -m pytest \
  src/inference-gateway/tests/test_postgres_integration.py -q
```

Also exercise [OTLP collector/Grafana delivery](../runbooks/observability.md), and the
[team lifecycle](team-lifecycle.md) export/erasure flow with real Redis/Temporal and a second
team retained as the isolation control. Verify secret creation/rotation from an authorized
worker activity and rejection for other teams/projects. These live checks remain required
before treating the candidate as release-ready.

## Candidate readiness pass

2026-10-09, native Windows 11, Python 3.12.14, Node 24.19.0, Helm 3.18.6.
This pass prepares **1.0.0-rc.1** without changing package versions, publishing
artifacts or committing. It is **not a completed release gate**: the lint finding
and caller checks below remain open.

### Native checks

| Check | Result |
| --- | --- |
| `ruff check .` | One existing E501 at `src/inference-gateway/tests/test_approval_policies.py:67`; left unchanged. All changed Python files pass. |
| `python scripts/api-contract.py --check` / `python scripts/config-contract.py --check` | Pass. |
| Python SDK tests | 200 passed, 3 skipped. |
| TypeScript SDK build, lint, tests | Pass; 77 tests. |
| Console build and Playwright suite | Pass; 73 tests. |
| Gateway suite excluding live integrations | Final run: 1,069 passed, 5 skipped. An earlier Git Bash subprocess timeout in `test_sync_script.py::test_local_direct_apply_centrally_owns_namespaces` did not recur. |
| Security/body-limit regression subset after the final fix | 130 passed. |
| Umbrella Helm tests | 17 passed. |
| Documentation | `mkdocs build --strict` passes with the runbook mirror used by `scripts/docs-build.sh`. |
| Local gateway load | 4,000 requests, all HTTP 200; [endpoint timings and limitations](benchmarks-and-evals.md#10-candidate-native-gateway-sanity-2026-10-09). |

The Windows sandbox prevented Playwright's managed server teardown. The successful
native run used the same test configuration with `webServer` disabled in a temporary
config and an explicitly started/stopped Vite preview process. No permanent test
configuration was changed. Linux/WSL should use the ordinary `npm test` command.

### Console review

The capture suite writes **80 PNGs** to `.out/console-v1.0.0-rc.1/`: every route and
the six starter forms at **360, 393 and 1440 CSS pixels**, plus both phone menus.
It includes sign-in/SSO policy, approvals, spend limits, trigger history, CSV export,
key editing, retained step content, and setup states. Three downloaded CSVs are
saved beside the images. The images were visually reviewed, with these checks:

- No debug overlays, raw errors, filler records, overlaps or mid-word prose wraps.
- Text and controls fit 360px; long commands/JSON scroll inside their own panels.
- The Insights team and Maya Chen identity remain consistent across pages.
- One waiting Research run has three calls/receipts, 4,200 tokens and $0.06:
  $0.01 tool + $0.02 OpenAI + $0.03 Anthropic. Costs, run details, audit and CSV agree.
- Setup shows zero usage before providers are connected. The review gate has zero
  of two votes, a matching one-hour deadline, and consistent trigger history.
- Fixed missing Spend alerts padding and touching key/trigger action controls;
  layout assertions cover the fixes. Download assertions verify CSV contents.

These are realistic, deterministic seeded states, not proof of a live IdP or
workflow deployment. Screenshot artifacts and raw local results are git-ignored.

### Quickstart command coverage

All 14 fenced shell blocks were parsed with Bash or PowerShell as appropriate.
Runtime checks created a native venv, installed `./sdk/python`, exercised module
and subcommand help, scaffolded all six templates, and verified the checked-in
sample receipt log. The fresh clone attempt failed in Windows Schannel with
`SEC_E_NO_CREDENTIALS`; the existing checkout was used. Bash venv activation was
syntax-checked; the PowerShell environment path was exercised.

The following still require a caller with Docker/WSL: Compose `up --build` and
health waits; `runs start`, `inspect`, `approve`/reject, `usage`, JSONL and CSV
exports against that stack; gateway/worker logs and verification of a **fresh**
receipt export; two-reviewer/expiry walkthrough; `stop` or `down -v`; the real
OpenAI override (with the caller's funded key); and alternate-port `.env`/Compose
commands. The older `--no-build` release path was not exercised. `docker info`
could not run: no native Docker and `wsl.exe -d Ubuntu` returned `E_ACCESSDENIED`.

The TypeScript quickstart's 12 shell/JavaScript/TypeScript blocks also passed
syntax parsing. Its `npm ci`, build, lint and tests passed; a separate local project
installed the built source SDK and loaded `GatewayClient`. Results of snippet
parsing are in `.out/ts-doc-command-checks.json`. The Compose `aw-typescript` stack,
`npm run worker`, REPL start/inspect/approve/triage calls, admin/export calls,
`npm run smoke`, quorum walkthrough and Compose cleanup still need the caller's
Temporal/gateway stack. No snippet containing a live mutation was executed.

### Kubernetes command coverage

All 18 fenced Bash blocks passed syntax checks. Both `helm dependency update`
commands ran. All seven here-document values files were extracted, then strict
lint and template rendering passed for nine variants: defaults, candidate images,
TLS, HA, NetworkPolicy, external Redis, external PostgreSQL, TLS+OIDC, and all
options together. The guide's umbrella test command passed natively. Raw command
results are in `.out/docs-command-checks.json`; rendered YAML is in `.out/docs-check/`.

`kubectl config current-context` found no context, so **no cluster commands ran**.
The remaining commands/walkthroughs, in guide order, are:

1. Docker image builds, kind creation/image loading, initial `helm install`,
   bootstrap Secret read, port-forward and `/readyz` request.
2. Provider Secret creation, provider `helm upgrade`, rollout after rotation,
   API-key environment setup, and the paid Research start/inspect/approve flow.
3. TLS Secret creation, TLS/cert-manager upgrades and HTTPS probe; OIDC Secret
   creation/upgrade, auth-config probe, real sign-in/refresh/logout and Secure cookies.
4. HA upgrade, pod/PDB inspection and pod-loss/session recovery; NetworkPolicy
   upgrade/inspection, all three allowed/denied probe pods and unlisted-egress denial.
5. External Redis/PostgreSQL Secret creation and upgrades, backup/data migration,
   persistence after restarts, and combined installation in `aw-production-smoke`.
6. Upgrade with operator-owned `my-values.yaml`, rollback/restore rehearsal,
   `helm uninstall`, namespace deletion and kind cleanup; approval-policy rollout.

Those checks need test infrastructure, provider/IdP credentials, TLS/DNS, a
NetworkPolicy-capable CNI, and reachable external stores. Offline renders cannot
verify those conditions or existing-Secret reuse. Preserve the candidate image
overrides in every installation/upgrade values set.

### Caller checks in WSL

Use a Linux venv in a WSL checkout with Docker available; do not reuse the native
Windows venv. Re-run the documented quickstart and cluster checks above, then:

```bash
make validate-full
make compose-up
make compose-smoke
make workflow-loadtest
make workflow-upgrade-test
make workflow-helm-upgrade-test
make workflow-restore-drill
make repo-security-scan
make dependency-lock-check
make image-scan
make supply-chain-check
make loadtest-local
make evidence
make release-gate-strict
```

The workflow recovery/Helm checks require their documented test stacks and backups;
see [local evaluation](local-evaluation.md) and [production readiness](production-readiness.md).
Live-provider tests additionally need the caller's credentials. Do not substitute
fixture screenshots, offline chart renders or this fake-runtime load test for live
release evidence. No commit, push, tag, publication or `gh` command was run in this pass.

## Published artifact verification

The commands below target the older published release; change `RELEASE` only after
the matching artifacts exist. Set the release and repository once:

```bash
export RELEASE=v0.5.1
export REPOSITORY=RamazanKara/agentworkflows
export IMAGE_REPO=ghcr.io/ramazankara/agentworkflows
export RELEASE_IDENTITY="https://github.com/$REPOSITORY/.github/workflows/release.yml@refs/tags/$RELEASE"
```

## Helm OCI Charts

Tag builds publish each chart to `oci://$IMAGE_REPO/charts`.

```bash
helm pull "oci://$IMAGE_REPO/charts/inference-gateway" --version "${RELEASE#v}"
helm pull "oci://$IMAGE_REPO/charts/rag-service" --version "${RELEASE#v}"
helm pull "oci://$IMAGE_REPO/charts/agent-workspace" --version "${RELEASE#v}"
```

Render the downloaded chart before installing:

```bash
helm template verify-inference "inference-gateway-${RELEASE#v}.tgz" \
  --values deploy/clusters/customer/values/inference-gateway.yaml >/tmp/inference.yaml
```

## Image Signatures

Release images are signed by digest with Cosign in GitHub Actions.

```bash
cosign verify "$IMAGE_REPO/inference-gateway:$RELEASE" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify "$IMAGE_REPO/rag-service:$RELEASE" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Release images are also multi-arch (`linux/amd64` and `linux/arm64`); the signature covers the manifest list, so the same `cosign verify` works on Apple Silicon and arm64 (Graviton/Ampere) clusters.

## Chart Signatures

Helm chart OCI artifacts are cosign-signed by digest in the same release workflow as the images.

```bash
cosign verify "$IMAGE_REPO/charts/inference-gateway:${RELEASE#v}" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify "$IMAGE_REPO/charts/rag-service:${RELEASE#v}" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify "$IMAGE_REPO/charts/agent-workspace:${RELEASE#v}" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Chart OCI tags drop the leading `v` (`${RELEASE#v}`) to match the chart `version`, while runtime image tags keep it.

## Release Files

Download the release files into a new directory and check them:

```bash
mkdir -p "release-files/$RELEASE"
gh release download "$RELEASE" --repo "$REPOSITORY" --dir "release-files/$RELEASE"
(
  cd "release-files/$RELEASE"
  sha256sum --check sdk-checksums.txt
  cosign verify-blob chart-release-manifest.json \
    --bundle chart-release-manifest.sigstore.json \
    --certificate-identity "$RELEASE_IDENTITY" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
)
```

`sdk-checksums.txt` covers the Python wheel, source archive and TypeScript package. The
signed chart manifest records each chart package and the image digests it embeds.
Images are built from the tagged commit on GitHub-hosted runners; the release workflow does
not publish SBOM or provenance attestations. Run `make image-scan` locally if you need a
vulnerability report before deploying.

## Strict Evidence

Strict release evidence must be generated from current artifacts, not sample evidence:

```bash
make validate-full
make image-scan
make supply-chain-check
make loadtest-local
make evidence
make release-gate-strict
```

For a live customer-style validation path, run the local cluster checks and generate live evidence:

```bash
QUICKSTART_DIRECT_APPLY=1 make quickstart
make trace-smoke
make tenant-smoke
make agent-smoke
make evidence LIVE=1
```

Record the command output, generated evidence paths under `results/`, image digests, chart versions, and GitHub Actions run URL in the release notes.
