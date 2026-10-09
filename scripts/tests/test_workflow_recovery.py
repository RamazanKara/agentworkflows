import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("workflow_recovery", ROOT / "scripts/workflow-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RestoreVerificationTests(unittest.TestCase):
    def test_waiting_for_worker_readiness_preserves_http_status(self):
        error = HTTPError("http://localhost", 503, "not ready", {}, io.BytesIO(b'{"reason":"worker_starting"}'))
        with (
            patch.dict(recovery.os.environ, {"AGENTWORKFLOWS_GATEWAY_PORT": "8080"}),
            patch.object(recovery.urllib.request, "urlopen", side_effect=error),
        ):
            with self.assertRaises(HTTPError) as caught:
                recovery.request("/v1/workflow-runs/probe")
            self.assertEqual(caught.exception.code, 503)
            self.assertIn("worker_starting", str(caught.exception))
        ready = {"status": "running", "progress": {"stage": "awaiting_approval"}}
        with patch.object(recovery, "request", side_effect=[error, ready]), patch.object(recovery.time, "sleep"):
            self.assertEqual(recovery.wait_run("probe", "awaiting_approval"), ready)

    def fixture(self, directory):
        for name in recovery.FILES:
            (directory / name).write_bytes(b"database fixture")
        previous = hashlib.sha256(b"genesis").hexdigest()
        events = []
        for event in (
            {"event": "chain_start", "previous_chain_id": None},
            {"event": "inference_request", "status_code": 200},
        ):
            event.update(chain_id="restore-fixture", ts=1)
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256((previous + canonical).encode()).hexdigest()
            event.update(prev_hash=previous, record_hash=digest)
            events.append(json.dumps(event))
            previous = digest
        (directory / "receipts.jsonl").write_text("\n".join(events) + "\n")
        recovery.run(
            recovery.sys.executable,
            str(ROOT / "scripts/audit-anchor.py"),
            str(directory / "receipts.jsonl"),
            "--output",
            str(directory / "anchor.json"),
        )
        self.manifest(directory)

    def manifest(self, directory):
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sha256": {
                        name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in recovery.FILES
                    },
                }
            )
        )

    def test_restore_verifies_receipts_and_detects_rewritten_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            recovery.verify_backup(directory)
            receipts = directory / "receipts.jsonl"
            receipts.write_text(receipts.read_text().replace('"status_code": 200', '"status_code": 503'))
            self.manifest(directory)
            with self.assertRaises(RuntimeError):
                recovery.verify_backup(directory)

    def test_corrupt_database_and_empty_receipts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            (directory / "temporal.dump").write_bytes(b"truncated")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                recovery.verify_backup(directory)
            (directory / "anchor.json").write_text('{"chains": {}}')
            self.manifest(directory)
            with self.assertRaisesRegex(ValueError, "no receipt chains"):
                recovery.verify_backup(directory)

    def test_missing_and_empty_database_rejected_even_with_matching_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            database = directory / "temporal.dump"
            database.write_bytes(b"")
            self.manifest(directory)
            with self.assertRaisesRegex(ValueError, "empty"):
                recovery.verify_backup(directory)
            database.unlink()
            with self.assertRaises(FileNotFoundError):
                recovery.verify_backup(directory)


    def test_dependency_fault_is_reversed_when_verification_fails(self):
        commands = []
        with patch.object(recovery, "run", side_effect=lambda *args: commands.append(args)), patch.object(
            recovery, "request", side_effect=AssertionError("probe failure")
        ), self.assertRaisesRegex(AssertionError, "probe failure"):
            recovery.check_dependency_outages(["docker", "compose", "-p", "isolated-test"])
        self.assertIn(("docker", "compose", "-p", "isolated-test", "start", "budget-redis"), commands)
