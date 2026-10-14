"""scripts/rag-ingest.py stays a working entry point for the RAG service's ingestion CLI."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/rag-ingest.py"
# Flags that runbooks, tenant-offboard.py and earlier releases' instructions pass.
DOCUMENTED_FLAGS = (
    "--source", "--source-id", "--backend", "--check", "--write", "--delete", "--purge",
    "--older-than-days", "--qdrant-url", "--collection", "--collection-version", "--dimensions",
    "--timeout-seconds", "--embedding-provider", "--embedding-base-url", "--embedding-model",
    "--chunk-chars", "--overlap-chars", "--status-file",
)  # fmt: skip
RAG_RUNTIME = all(importlib.util.find_spec(name) for name in ("httpx", "yaml", "pydantic", "fastapi"))


@unittest.skipUnless(RAG_RUNTIME, "RAG runtime dependencies required (run with a service environment)")
class RagIngestWrapperTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        # From another directory, so the wrapper cannot rely on the caller's working directory.
        with tempfile.TemporaryDirectory() as cwd:
            return subprocess.run(
                [sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, check=False
            )

    def test_keeps_every_documented_flag(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in DOCUMENTED_FLAGS:
            self.assertIn(flag, result.stdout)

    def test_checks_the_platform_manifest_with_the_service_implementation(self):
        with tempfile.TemporaryDirectory() as output:
            status = Path(output) / "status.json"
            result = self.run_script(
                "--check",
                "--source", str(ROOT / "platform/rag/sources/platform-knowledge.yaml"),
                "--status-file", str(status),
            )  # fmt: skip
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(status.read_text())
        self.assertEqual(summary["status"], "checked")
        self.assertGreater(summary["chunks"], 0)
        self.assertEqual(json.loads(result.stdout), summary)

    def test_reports_operator_errors_without_a_traceback(self):
        result = self.run_script("--delete")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--source-id is required", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
