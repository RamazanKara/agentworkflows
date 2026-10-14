"""Release chart packaging: digests are bound into defaults without losing values comments."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_release_charts", ROOT / "scripts/package-release-charts.py")
assert SPEC is not None and SPEC.loader is not None
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)
HELM = shutil.which("helm")
GATEWAY = "sha256:" + "a" * 64
RAG = "sha256:" + "b" * 64
WORKER = "sha256:" + "c" * 64


class ValuesEditTests(unittest.TestCase):
    def test_pin_image_sets_tag_and_digest_and_keeps_comments(self):
        values = (ROOT / "deploy/charts/inference-gateway/values.yaml").read_text(encoding="utf-8")
        pinned, repository = packager.pin_image(values, "v9.9.9", GATEWAY)
        self.assertEqual(repository, "ghcr.io/ramazankara/agentworkflows/inference-gateway")
        self.assertIn(f'  tag: "v9.9.9"\n  digest: "{GATEWAY}"\n', pinned)
        self.assertIn("# First-party image: release CI pins the published digest", pinned)
        self.assertEqual(pinned.count("\n"), values.count("\n") + 1)
        # Third-party images further down keep their own pinned digests.
        self.assertEqual(pinned.count("digest:"), values.count("digest:") + 1)
        repinned, _ = packager.pin_image(pinned, "v9.9.10", RAG)
        self.assertIn(f'  tag: "v9.9.10"\n  digest: "{RAG}"\n', repinned)
        self.assertNotIn(GATEWAY, repinned)

    def test_pin_worker_replaces_tag_with_digest(self):
        values = (ROOT / "deploy/charts/workflows/values.yaml").read_text(encoding="utf-8")
        pinned, repository = packager.pin_worker(values, WORKER)
        self.assertEqual(repository, "ghcr.io/ramazankara/agentworkflows/workflow-worker")
        changed = [(old, new) for old, new in zip(values.splitlines(), pinned.splitlines(), strict=True) if old != new]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][1], f"  image: {repository}@{WORKER}")
        self.assertEqual(packager.pin_worker(pinned, GATEWAY)[0].count(GATEWAY), 1)

    def test_rejects_malformed_digests(self):
        for value in ("sha256:abc", "sha512:" + "a" * 64, "a" * 71, "sha256:" + "g" * 64):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                packager._digest(value)
        self.assertEqual(packager._digest("sha256:" + "A" * 64), "sha256:" + "a" * 64)


@unittest.skipUnless(
    HELM and list((ROOT / "deploy/charts/workflows/charts").glob("temporal-*.tgz")),
    "Helm and built chart dependencies required (helm dependency update deploy/charts/workflows)",
)
class PackageTests(unittest.TestCase):
    def test_packages_every_chart_bound_to_the_release_digests(self):
        version = packager.chart_metadata(ROOT / "deploy/charts/inference-gateway")[1]
        with tempfile.TemporaryDirectory(prefix="release-charts-test-") as output:
            args = argparse.Namespace(
                version=version,
                gateway_digest=GATEWAY,
                rag_digest=RAG,
                worker_digest=WORKER,
                source_revision="0" * 40,
                output_dir=output,
            )
            manifest = packager.package_charts(args)
            self.assertEqual(
                sorted(item["chart"] for item in manifest["charts"]),
                sorted(path.name for path in (ROOT / "deploy/charts").iterdir() if (path / "Chart.yaml").exists()),
            )
            self.assertEqual(json.loads((Path(output) / "chart-release-manifest.json").read_text()), manifest)
            with tarfile.open(Path(output) / f"inference-gateway-{version}.tgz") as archive:
                values = archive.extractfile("inference-gateway/values.yaml").read().decode()
            self.assertIn(f'digest: "{GATEWAY}"', values)
            self.assertIn("# First-party image: release CI pins the published digest", values)


if __name__ == "__main__":
    unittest.main()
