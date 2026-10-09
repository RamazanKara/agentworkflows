# Release Verification

Use this checklist before trusting a public release in a customer-owned cluster.

For the unreleased v0.6.0 checkout, run the local gate before tagging:

```bash
make lint test-gateway test-scripts api-contract config-contract chart-docs
make test-typescript test-console
```

Build Helm dependencies, then run `helm lint` and `helm template` for the charts
being shipped. The console suite captures fixture-based screens at 360, 393 and 1440
CSS pixels in `.out/console-v0.6.0/`; inspect them yourself after the tests pass.
To repeat only those captures after a console build, run
`npm test -- --grep 'release console layouts'` from `src/inference-gateway/console`.
These cover Get started, provider setup, costs, members and keys, step JSON,
settings, audit and the phone menu. Inspect chips, links, command wrapping and
horizontal step-JSON scrolling at both phone widths. Setup variants show zero
usage while keys are missing. The connected insights team has one Research run
awaiting approval: three calls and receipts, 4,200 tokens and $0.06, matching the
step sums and cost tables. These are synthetic UI fixtures; the separate Compose
smoke test exercises the live gateway.

On native Windows without WSL, run the console's `npm run build` and `npm test`,
the TypeScript SDK's `npm run build`, `npm run lint` and `npm test`, and Python
Ruff, SDK tests and contract scripts from their installed environments. The Bash
gates still need a Linux caller: `make lint test-gateway test-scripts`. Screenshot
tests do not verify live providers, Redis, Temporal or PostgreSQL integration.

Set the release and repository once:

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
