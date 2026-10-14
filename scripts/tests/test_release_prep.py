"""Release version bumps: every reference moves together, and --check catches drift.

The bump tests run against a temporary copy of the files the script touches, so the
checkout itself is never modified.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_prep", ROOT / "scripts/release-prep.py")
assert SPEC is not None and SPEC.loader is not None
release_prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_prep)
HELM = shutil.which("helm")


def next_minor(version: str) -> str:
    major, minor, _ = version.split(".")
    return f"{major}.{int(minor) + 1}.0"


class RepositoryVersionTests(unittest.TestCase):
    def test_checkout_references_one_release(self):
        version = release_prep.current_version(ROOT)
        self.assertEqual(release_prep.check(ROOT, version), [])

    def test_check_names_each_stale_reference(self):
        version = release_prep.current_version(ROOT)
        problems = release_prep.check(ROOT, next_minor(version))
        self.assertTrue(any(problem.startswith("sdk/python/pyproject.toml:") for problem in problems))
        self.assertTrue(any(problem.startswith("deploy/charts/agentworkflows/Chart.lock:") for problem in problems))
        self.assertTrue(any(problem.startswith("CHANGELOG.md") for problem in problems))


class BumpTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="release-prep-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        paths = {anchor.path for anchor in release_prep.anchors()}
        paths.update(path.relative_to(ROOT).as_posix() for path in release_prep.prose_files(ROOT))
        paths.add("CHANGELOG.md")
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        # Chart directories without vendored dependencies, so Helm can rebuild the umbrella lock.
        for chart in (ROOT / "deploy/charts").iterdir():
            if chart.is_dir():
                shutil.copytree(
                    chart,
                    self.root / "deploy/charts" / chart.name,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("charts", "*.tgz"),
                )
        self.old = release_prep.current_version(self.root)
        self.new = next_minor(self.old)

    def run_main(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = release_prep.main([*args, "--root", str(self.root)])
        return code, output.getvalue()

    def add_changelog_section(self, body: str = "Release notes.\n") -> None:
        changelog = self.root / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8")
        changelog.write_text(text.replace("\n## ", f"\n## v{self.new} - 2026-11-01\n\n{body}\n## ", 1), "utf-8")

    def read(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def other_versions(self) -> dict[str, list[str]]:
        """Every version-like string in prose files except the release itself, per file."""
        pattern = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?![\d.]\d)")
        release = {self.old, f"v{self.old}", self.new, f"v{self.new}"}
        return {
            path.relative_to(self.root).as_posix(): sorted(
                item for item in pattern.findall(path.read_text(encoding="utf-8")) if item not in release
            )
            for path in release_prep.prose_files(self.root)
        }

    def test_bump_moves_every_reference_and_leaves_other_versions(self):
        self.add_changelog_section()
        upstream_app_version = re.search(r"^appVersion: .*$", self.read("deploy/charts/ollama/Chart.yaml"), re.M)[0]
        other_versions = self.other_versions()

        code, output = self.run_main(self.new, "--no-helm")

        self.assertEqual(code, 0, output)
        self.assertEqual(release_prep.check(self.root, self.new), [])
        self.assertNotEqual(release_prep.check(self.root, self.old), [])
        self.assertIn(upstream_app_version, self.read("deploy/charts/ollama/Chart.yaml"))
        self.assertIn('    version: "1.7.0"', self.read("deploy/charts/workflows/Chart.yaml"))
        self.assertEqual(self.other_versions(), other_versions)
        stale = release_prep.mention(self.old)
        for path in release_prep.prose_files(self.root):
            self.assertIsNone(stale.search(path.read_text(encoding="utf-8")), path)
        self.assertIn(f"CUSTOMER_REVISION=v{self.new}", self.read("README.md"))
        self.assertIn(
            f"/v{self.new}/agentworkflows-{self.new}-py3-none-any.whl", self.read("sdk/python/tests/test_cli.py")
        )
        self.assertIn(f"agentworkflows-sdk-{self.new}.tgz", self.read("docs/quickstart-typescript.md"))

    def test_bump_reports_a_missing_changelog_section(self):
        code, output = self.run_main(self.new, "--no-helm")
        self.assertEqual(code, 1)
        self.assertIn(f"## v{self.new} - YYYY-MM-DD", output)
        self.assertEqual(release_prep.current_version(self.root), self.new)

    def test_check_rejects_an_empty_changelog_section(self):
        self.add_changelog_section(body="")
        self.run_main(self.new, "--no-helm")
        self.assertIn(f"v{self.new} section is empty", " ".join(release_prep.check(self.root, self.new)))

    def test_check_points_at_a_reference_left_behind(self):
        self.add_changelog_section()
        self.run_main(self.new, "--no-helm")
        compose = self.root / "deploy/compose/compose.yaml"
        compose.write_text(compose.read_text(encoding="utf-8").replace(f"v{self.new}", f"v{self.old}", 1), "utf-8")
        code, output = self.run_main("--check", self.new)
        self.assertEqual(code, 1)
        self.assertRegex(
            output, rf"deploy/compose/compose\.yaml:\d+: says {re.escape(self.old)}, expected {re.escape(self.new)}"
        )

    def test_bump_corrects_a_drifted_anchor(self):
        self.add_changelog_section()
        lock = self.root / "deploy/charts/agentworkflows/Chart.lock"
        lock.write_text(lock.read_text(encoding="utf-8").replace(f"version: {self.old}", "version: 0.0.1", 1), "utf-8")
        code, output = self.run_main(self.new, "--no-helm")
        self.assertEqual(code, 0, output)

    def test_rejects_same_older_and_malformed_versions(self):
        for version in (self.old, "0.0.1", "1.2", "v1.2.3-rc.1"):
            with (
                self.subTest(version=version),
                self.assertRaises(SystemExit) as caught,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.run_main(version, "--no-helm")
            self.assertEqual(caught.exception.code, 2)
        self.assertEqual(release_prep.current_version(self.root), self.old)

    def test_refuses_to_bump_a_file_that_changed_shape(self):
        makefile = self.root / "Makefile"
        makefile.write_text(
            makefile.read_text(encoding="utf-8").replace("CUSTOMER_REVISION ?=", "CUSTOMER_REV ?="), "utf-8"
        )
        with self.assertRaises(SystemExit) as caught:
            self.run_main(self.new, "--no-helm")
        self.assertIn("Makefile", str(caught.exception))
        self.assertEqual(release_prep.current_version(self.root), self.old)

    @unittest.skipUnless(HELM, "helm required")
    def test_umbrella_lock_digest_matches_after_bump(self):
        self.add_changelog_section()
        code, output = self.run_main(self.new)
        self.assertEqual(code, 0, output)
        shutil.rmtree(self.root / "deploy/charts/agentworkflows/charts")
        # `helm dependency build` refuses a Chart.lock whose digest is out of sync with Chart.yaml.
        result = subprocess.run(
            [HELM, "dependency", "build", "--skip-refresh", str(self.root / "deploy/charts/agentworkflows")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
