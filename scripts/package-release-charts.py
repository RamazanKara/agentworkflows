#!/usr/bin/env python3
"""Package release charts with first-party image digests bound into defaults.

Values files are edited in place, line by line, so the packaged charts keep the comments
that `helm show values` displays. Every packaged chart is rendered afterwards to prove it
references the released digests. Needs only Helm and the standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST_PARTY = {
    "inference-gateway": "gateway_digest",
    "rag-service": "rag_digest",
}
# The block-level `image:` mapping of a first-party chart, up to the next top-level key.
IMAGE_BLOCK = re.compile(r"^image:\n(?:(?:  .*)?\n)+?(?=\S)", re.MULTILINE)
IMAGE_REPOSITORY = re.compile(r"^  repository: (\S+)$", re.MULTILINE)
WORKER_IMAGE = re.compile(r"^(  image: )(\S+?)(?::[^\s:@/]+|@sha256:[0-9a-f]{64})$", re.MULTILINE)
CHART_VERSION = re.compile(r"^version: (\S+)$", re.MULTILINE)
CHART_NAME = re.compile(r"^name: (\S+)$", re.MULTILINE)


def _digest(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value):
        raise argparse.ArgumentTypeError("digest must be sha256:<64 lowercase hex characters>")
    return value.lower()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin_image(values: str, tag: str, digest: str) -> tuple[str, str]:
    """Set image.tag and image.digest in a chart's values text; return it and the repository."""
    block = IMAGE_BLOCK.search(values)
    if block is None:
        raise ValueError("values.yaml has no top-level image: block")
    text = block.group(0)
    repository = IMAGE_REPOSITORY.search(text)
    if repository is None or not re.search(r"^  tag: .*$", text, re.MULTILINE):
        raise ValueError("values.yaml image: block needs repository and tag")
    text = re.sub(r"^  digest: .*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^  tag: .*$", f'  tag: "{tag}"\n  digest: "{digest}"', text, count=1, flags=re.MULTILINE)
    return values[: block.start()] + text + values[block.end() :], repository.group(1)


def pin_worker(values: str, digest: str) -> tuple[str, str]:
    """Point worker.image at the digest; return the values text and the repository."""
    match = WORKER_IMAGE.search(values, values.index("\nworker:\n"))
    if match is None:
        raise ValueError("workflows values.yaml has no worker image")
    prefix, repository = match.groups()
    return values[: match.start()] + f"{prefix}{repository}@{digest}" + values[match.end() :], repository


def chart_metadata(chart: Path) -> tuple[str, str]:
    text = (chart / "Chart.yaml").read_text(encoding="utf-8")
    name, version = CHART_NAME.search(text), CHART_VERSION.search(text)
    if name is None or version is None:
        raise ValueError(f"{chart / 'Chart.yaml'} needs name and version")
    return name.group(1), version.group(1).strip("\"'")


def verify_rendered(archive: Path, references: list[str]) -> None:
    """Fail unless the packaged chart's default render uses every expected image reference."""
    rendered = subprocess.run(
        ["helm", "template", "release-check", str(archive)], check=True, text=True, capture_output=True
    ).stdout
    missing = [
        reference
        for reference in references
        if not re.search(rf'^\s*image: "?{re.escape(reference)}"?$', rendered, re.MULTILINE)
    ]
    if missing:
        raise ValueError(f"{archive.name} does not render the released images: {', '.join(missing)}")


def package_charts(args: argparse.Namespace) -> dict:
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("*.tgz"):
        old.unlink()

    with tempfile.TemporaryDirectory(prefix="agentworkflows-charts-") as temp:
        charts = Path(temp) / "charts"
        shutil.copytree(ROOT / "deploy/charts", charts, ignore=shutil.ignore_patterns("charts"))

        references: dict[str, str] = {}
        for chart_name, digest_arg in FIRST_PARTY.items():
            values_path = charts / chart_name / "values.yaml"
            text, repository = pin_image(
                values_path.read_text(encoding="utf-8"), f"v{args.version}", getattr(args, digest_arg)
            )
            values_path.write_text(text, encoding="utf-8")
            references[chart_name] = f"{repository}@{getattr(args, digest_arg)}"

        subprocess.run(["helm", "dependency", "update", str(charts / "workflows")], check=True)
        worker_values = charts / "workflows" / "values.yaml"
        text, repository = pin_worker(worker_values.read_text(encoding="utf-8"), args.worker_digest)
        worker_values.write_text(text, encoding="utf-8")
        references["workflows"] = f"{repository}@{args.worker_digest}"
        # Gateway and worker are on by default in the umbrella; retrieval is opt-in.
        references["agentworkflows"] = references["inference-gateway"]

        # Vendor Temporal and the digest-bound subcharts before packaging the umbrella.
        subprocess.run(["helm", "dependency", "update", str(charts / "agentworkflows")], check=True)

        packages: list[dict[str, str]] = []
        for chart in sorted(charts.iterdir(), key=lambda path: (path.name == "agentworkflows", path.name)):
            if not (chart / "Chart.yaml").exists():
                continue
            name, version = chart_metadata(chart)
            if version != args.version:
                raise ValueError(f"{chart.name} chart version {version} does not match release {args.version}")
            subprocess.run(["helm", "package", str(chart), "--destination", str(output)], check=True)
            archive = output / f"{name}-{version}.tgz"
            expected = [references[chart.name]] if chart.name in references else []
            if chart.name == "agentworkflows":
                expected.append(references["workflows"])
            verify_rendered(archive, expected)
            packages.append({"chart": name, "archive": archive.name, "sha256": _sha256(archive)})

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_revision": args.source_revision,
        "version": args.version,
        "images": {
            "inference-gateway": args.gateway_digest,
            "rag-service": args.rag_digest,
            "workflow-worker": args.worker_digest,
        },
        "charts": packages,
    }
    manifest_path = output / "chart-release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--gateway-digest", required=True, type=_digest)
    parser.add_argument("--rag-digest", required=True, type=_digest)
    parser.add_argument("--worker-digest", required=True, type=_digest)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output-dir", default="chart-packages")
    args = parser.parse_args()
    package_charts(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
