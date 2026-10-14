#!/usr/bin/env python3
"""Bump every release version reference, or check that they all agree.

    scripts/release-prep.py 0.10.0            Move every reference from the current version to 0.10.0
    scripts/release-prep.py --check 0.10.0    Fail unless every reference already says 0.10.0
    scripts/release-prep.py --check           Fail unless every reference agrees with the SDK version

The current version is the Python SDK's (sdk/python/pyproject.toml). Two kinds of
reference move together:

- Anchors: package manifests and their lock roots, chart versions, first-party
  appVersions and image tags, umbrella dependency pins, Compose and GitOps revisions,
  the Makefile's CUSTOMER_REVISION, service and API contract versions. --check reads
  exactly these; the release workflow runs it on the tag and CI runs it on every push.
- Prose: README.md, docs/*.md and a few other guides that quote the release. Each
  standalone mention of the current version, with or without a leading "v", moves to
  the new one. Historical mentions of older releases are left alone; review the diff.

After a bump the umbrella chart's Chart.lock is refreshed with Helm so its digest
matches, and CHANGELOG.md must have a "## vX.Y.Z - YYYY-MM-DD" section as its newest
release heading. Workflow template versions are independent and never change here.
Uses only the standard library so it runs on a bare runner before any setup.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"\d+\.\d+\.\d+"
VERSION_PATTERN = re.compile(rf"^{VERSION}$")
CHANGELOG_HEADING = re.compile(rf"^## v({VERSION}) - \d{{4}}-\d{{2}}-\d{{2}}$", re.MULTILINE)
UMBRELLA = "deploy/charts/agentworkflows"
CHARTS = (
    "agent-workspace",
    "agentworkflows",
    "budget-redis",
    "inference-gateway",
    "ollama",
    "qdrant-vector-store",
    "rag-service",
    "vllm",
    "workflows",
)
# The other charts package upstream software and keep its version as appVersion.
FIRST_PARTY_CHARTS = ("agent-workspace", "agentworkflows", "inference-gateway", "rag-service", "workflows")
WORKER_IMAGE = "ghcr.io/ramazankara/agentworkflows/workflow-worker"
NPM_ROOT = rf'\A\{{\s*"name": "[^"]+",\s*(?:"private": true,\s*)?"version": "({VERSION})"'
NPM_LOCK_PACKAGE = rf'"packages": \{{\s*"": \{{\s*"name": "[^"]+",\s*"version": "({VERSION})"'
CUSTOMER_REVISION = rf"CUSTOMER_REVISION=v({VERSION})\b"
PROSE = (
    "README.md",
    "docs/*.md",
    "sdk/README.md",
    "deploy/clusters/customer/README.md",
    "sdk/python/tests/test_cli.py",
)


class Anchor(NamedTuple):
    """One kind of version reference: every match of pattern's single group is the release."""

    path: str
    pattern: str
    count: int | None = 1  # exact number of matches; None means at least one

    def regex(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.MULTILINE)


SDK_ANCHOR = Anchor("sdk/python/pyproject.toml", rf'^version = "({VERSION})"$')


def anchors() -> list[Anchor]:
    items = [
        SDK_ANCHOR,
        Anchor("sdk/python/agentworkflows/scaffold.py", rf'^SDK_VERSION = "({VERSION})"$'),
        Anchor("sdk/typescript/package.json", NPM_ROOT),
        Anchor("sdk/typescript/package-lock.json", NPM_ROOT),
        Anchor("sdk/typescript/package-lock.json", NPM_LOCK_PACKAGE),
        Anchor("src/inference-gateway/console/package.json", NPM_ROOT),
        Anchor("src/inference-gateway/console/package-lock.json", NPM_ROOT),
        Anchor("src/inference-gateway/console/package-lock.json", NPM_LOCK_PACKAGE),
        Anchor("src/inference-gateway/app/main.py", rf'^SERVICE_VERSION = "({VERSION})"$'),
        Anchor("src/rag-service/app/main.py", rf'^SERVICE_VERSION = "({VERSION})"$'),
        Anchor("scripts/api-contract.py", rf'^\s+version="({VERSION})",$', count=2),
        Anchor("platform/api-contracts/inference-gateway.openapi.json", rf'^    "version": "({VERSION})"$'),
        Anchor("platform/api-contracts/rag-service.openapi.json", rf'^    "version": "({VERSION})"$'),
        # Every umbrella dependency is a first-party chart released in lockstep.
        Anchor(f"{UMBRELLA}/Chart.yaml", rf'^    version: "({VERSION})"$', count=None),
        Anchor(f"{UMBRELLA}/Chart.lock", rf"^  version: ({VERSION})$", count=None),
        Anchor("deploy/charts/inference-gateway/values.yaml", rf'^  tag: "v({VERSION})"$'),
        Anchor("deploy/charts/rag-service/values.yaml", rf'^  tag: "v({VERSION})"$'),
        Anchor("deploy/charts/workflows/values.yaml", rf"^  image: {re.escape(WORKER_IMAGE)}:v({VERSION})$"),
        Anchor("deploy/charts/inference-gateway/README.md", rf"^\| `image\.tag` \| `v({VERSION})` \|$"),
        Anchor("deploy/charts/rag-service/README.md", rf"^\| `image\.tag` \| `v({VERSION})` \|$"),
        Anchor(
            "deploy/charts/workflows/README.md", rf"^\| `worker\.image` \| `{re.escape(WORKER_IMAGE)}:v({VERSION})` \|$"
        ),
        Anchor("deploy/compose/compose.yaml", rf"\$\{{AGENTWORKFLOWS_VERSION:-v({VERSION})\}}", count=3),
        Anchor("deploy/gitops/argocd/root-app.yaml", rf"^    targetRevision: v({VERSION})$"),
        Anchor("deploy/gitops/argocd/root-app-customer.yaml", rf"^    targetRevision: v({VERSION})$"),
        Anchor("deploy/clusters/local/apps.yaml", rf"^    targetRevision: v({VERSION})$", count=None),
        Anchor("deploy/clusters/customer/apps.yaml", rf"^    targetRevision: v({VERSION})$", count=None),
        Anchor("Makefile", rf"^CUSTOMER_REVISION \?= v({VERSION})$"),
        Anchor("README.md", CUSTOMER_REVISION, count=None),
        Anchor("docs/getting-started.md", CUSTOMER_REVISION, count=None),
        Anchor("deploy/clusters/customer/README.md", CUSTOMER_REVISION, count=None),
    ]
    for chart in CHARTS:
        items.append(Anchor(f"deploy/charts/{chart}/Chart.yaml", rf"^version: ({VERSION})$"))
    for chart in FIRST_PARTY_CHARTS:
        items.append(Anchor(f"deploy/charts/{chart}/Chart.yaml", rf'^appVersion: "({VERSION})"$'))
    return items


def mention(version: str) -> re.Pattern[str]:
    """A standalone mention of version, optionally v-prefixed, not part of a longer version."""
    return re.compile(rf"(?<![\w.])(v?){re.escape(version)}(?!\d|\.\d)")


def read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def anchor_versions(root: Path, anchor: Anchor) -> tuple[list[tuple[int, str]], str | None]:
    """Return (line, version) for each match, or a problem when the file lost the expected shape."""
    path = root / anchor.path
    if not path.is_file():
        return [], f"{anchor.path}: file is missing"
    text = read(root, anchor.path)
    found = [(text.count("\n", 0, match.start(1)) + 1, match.group(1)) for match in anchor.regex().finditer(text)]
    if (anchor.count is None and not found) or (anchor.count is not None and len(found) != anchor.count):
        expected = "at least 1" if anchor.count is None else str(anchor.count)
        return found, (
            f"{anchor.path}: expected {expected} version reference(s) matching {anchor.pattern!r}, "
            f"found {len(found)}; update scripts/release-prep.py if the file changed shape"
        )
    return found, None


def current_version(root: Path) -> str:
    found, problem = anchor_versions(root, SDK_ANCHOR)
    if problem or not found:
        raise SystemExit(f"release-prep: cannot read the SDK version: {problem}")
    return found[0][1]


def changelog_problem(root: Path, version: str) -> str | None:
    text = read(root, "CHANGELOG.md")
    match = CHANGELOG_HEADING.search(text)
    if match is None:
        return f"CHANGELOG.md has no '## v{version} - YYYY-MM-DD' heading"
    if match.group(1) != version:
        return (
            f"CHANGELOG.md's newest release heading is v{match.group(1)}; "
            f"add a '## v{version} - YYYY-MM-DD' section above it"
        )
    # The release workflow publishes the lines up to the next second-level heading.
    following = re.compile(r"^## ", re.MULTILINE).search(text, match.end())
    if not text[match.end() : following.start() if following else len(text)].strip():
        return f"CHANGELOG.md's v{version} section is empty; the GitHub release notes are taken from it"
    return None


def check(root: Path, version: str) -> list[str]:
    problems: list[str] = []
    for anchor in anchors():
        found, problem = anchor_versions(root, anchor)
        if problem:
            problems.append(problem)
        problems.extend(
            f"{anchor.path}:{line}: says {found_version}, expected {version}"
            for line, found_version in found
            if found_version != version
        )
    if problem := changelog_problem(root, version):
        problems.append(problem)
    return problems


def prose_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in PROSE:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files)


def replace_group(match: re.Match[str], version: str) -> str:
    start, end = match.span(1)
    offset = match.start()
    text = match.group(0)
    return text[: start - offset] + version + text[end - offset :]


def bump(root: Path, old: str, new: str) -> list[str]:
    """Set every anchor to new and move prose mentions of old to new; return the changed files.

    Anchors are release slots by definition, so one that drifted from old is corrected too.
    """
    problems = [problem for anchor in anchors() if (problem := anchor_versions(root, anchor)[1])]
    if problems:
        raise SystemExit("release-prep: refusing to bump:\n" + "\n".join(f"- {item}" for item in problems))
    updated: dict[Path, str] = {}

    def text_of(path: Path) -> str:
        return updated[path] if path in updated else path.read_text(encoding="utf-8")

    for anchor in anchors():
        path = root / anchor.path
        updated[path] = anchor.regex().sub(lambda match: replace_group(match, new), text_of(path))
    old_mention = mention(old)
    for path in prose_files(root):
        updated[path] = old_mention.sub(lambda match: match.group(1) + new, text_of(path))

    changed = []
    for path, text in updated.items():
        if text != path.read_text(encoding="utf-8"):
            path.write_text(text, encoding="utf-8")
            changed.append(path.relative_to(root).as_posix())
    return sorted(changed)


def refresh_umbrella_lock(root: Path) -> None:
    """Chart.lock carries a digest of the dependency list; let Helm recompute it."""
    helm = shutil.which("helm")
    if helm is None:
        raise SystemExit(
            f"release-prep: helm is not on PATH; run 'helm dependency update {UMBRELLA}' to refresh its Chart.lock digest"
        )
    subprocess.run(
        [helm, "dependency", "update", "--skip-refresh", str(root / UMBRELLA)], check=True, capture_output=True
    )


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", help="release version X.Y.Z (a leading v is accepted)")
    parser.add_argument("--check", action="store_true", help="only verify; change nothing")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--no-helm", action="store_true", help="skip refreshing the umbrella Chart.lock digest")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    version = (args.version or "").removeprefix("v")
    if version and not VERSION_PATTERN.match(version):
        parser.error(f"version must look like 1.2.3, not {args.version!r}")

    if args.check:
        target = version or current_version(root)
        problems = check(root, target)
        if problems:
            print(f"release-prep: version references disagree with {target}:")
            for problem in problems:
                print(f"- {problem}")
            return 1
        print(f"release-prep: every version reference is {target}")
        return 0

    if not version:
        parser.error("give the new version, or --check")
    old = current_version(root)
    if version == old:
        parser.error(f"the repository is already at {old}; use --check to verify it")
    if version_key(version) < version_key(old):
        parser.error(f"{version} is older than the current {old}")
    changed = bump(root, old, version)
    if not args.no_helm:
        refresh_umbrella_lock(root)
    print(f"release-prep: {old} -> {version} in {len(changed)} files:")
    for path in changed:
        print(f"  {path}")
    problems = check(root, version)
    if problems:
        print(f"release-prep: the files above now say {version}, but the release is not ready:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"release-prep: ready; review 'git diff', commit, then tag v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
