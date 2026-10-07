# Distribution and discovery

Release CI is configured to publish one tested source revision through four channels.
Version 0.1.0 starts this product; commands using release assets apply after its tag is published. Images are promoted
from the already-tested commit digest, Helm charts embed those immutable image digests, the
Python client is built separately and attached to the GitHub release, and versioned
documentation is retained by `mike`.

## Helm OCI

The umbrella chart is the recommended public entry point:

```bash
helm pull oci://ghcr.io/ramazankara/agentworkflows/charts/agentworkflows --version 0.1.0
helm install agentworkflows oci://ghcr.io/ramazankara/agentworkflows/charts/agentworkflows \
  --version 0.1.0 --namespace ai-platform --create-namespace
```

Release CI publishes `artifacthub-repo.yml` to the chart repository's special
`artifacthub.io` OCI tag. To finish discoverability, a maintainer must register
`oci://ghcr.io/ramazankara/agentworkflows/charts/agentworkflows` once in the Artifact Hub
control panel, copy the assigned `repositoryID` into `artifacthub-repo.yml`, and cut the next
release. This external registration cannot be completed from repository code.

## Python package

```bash
python -m pip install https://github.com/RamazanKara/agentworkflows/releases/download/v0.1.0/agentworkflows-0.1.0-py3-none-any.whl
```

The wheel, source archive, and `sdk-checksums.txt` are attached to each GitHub release.
Follow [release verification](release-verification.md) to verify the files before installing.
The default release channel is GitHub downloads; PyPI publishing requires the setup below.
Until a release is available, install from the checkout with `python -m pip install ./sdk/python`.

### Optional PyPI publishing

PyPI is disabled unless the repository Actions variable `PYPI_PUBLISH_ENABLED` is exactly
`true`. GitHub downloads complete independently of this setting and PyPI environment approval.

Before enabling it, register a pending PyPI Trusted Publisher with owner
`RamazanKara`, repository `agentworkflows`, workflow `ci.yml`, environment `pypi`,
and project `agentworkflows`. Protect the GitHub `pypi` environment with
required reviewer approval and tag-only deployment rules. CI keeps package building in an
unprivileged job; only the prebuilt artifact reaches the OIDC-enabled publish job. PyPI
attestations remain enabled. After completing this setup, set the repository variable to
`true` to publish subsequent release tags to PyPI as well.

## Versioned documentation

Main publishes the `development` documentation alias. A `v*` release tag publishes its exact
version, moves `latest`, and makes `latest` the site root. The tag build retains its generated
content on `gh-pages`, then dispatches a run on main to publish that tree. This avoids GitHub
Pages retaining the earlier main artifact when main and the tag share a commit. Older
generated versions remain on the `gh-pages` branch and in the version selector.

## Operator-owned one-time setup

The code and workflows are complete, but these account-level actions require repository-owner
authority:

1. Set GitHub Pages source to **GitHub Actions**.
2. For optional PyPI publishing, configure its protected environment and trusted publisher,
   then enable `PYPI_PUBLISH_ENABLED` at repository level.
3. Register the AgentWorkflows OCI chart in Artifact Hub and record its assigned repository ID.
4. Keep GHCR packages public so anonymous Helm pulls and Artifact Hub indexing work.
5. Render `docs/assets/social-preview.svg` at 1280x640 and upload the resulting PNG under
   **Settings > General > Social preview**. The upstream kit's old preview is not a product asset.
