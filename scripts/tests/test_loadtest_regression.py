import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "regression", Path(__file__).resolve().parents[2] / "loadtest/check-regression.py"
)
regression = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regression)


class LoadRegressionTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {
            "platform": "test",
            "python": "3.12",
            "cpu_count": 2,
            "runtime": "fake",
            "results": [
                {
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "requests": 100,
                    "concurrency": 10,
                    "rps": 100,
                    "p95_ms": 20,
                    "statuses": {"200": 100},
                }
            ],
        }
        self.current = copy.deepcopy(self.baseline)

    def test_pass_boundary_and_latency_or_throughput_regression(self):
        self.current["results"][0].update(p95_ms=25, rps=80)
        self.assertEqual(regression.compare(self.baseline, self.current), [])
        self.current["results"][0].update(p95_ms=25.01, rps=79.99)
        self.assertEqual(len(regression.compare(self.baseline, self.current)), 2)

    def test_incomplete_failed_nonfinite_or_changed_workload_is_not_a_pass(self):
        for change in (
            {"statuses": {"200": 99, "503": 1}},
            {"statuses": {"200": 99}},
            {"p95_ms": float("nan")},
            {"rps": float("inf")},
            {"concurrency": 20},
        ):
            with self.subTest(change=change):
                current = copy.deepcopy(self.baseline)
                current["results"][0].update(change)
                with self.assertRaises(ValueError):
                    regression.compare(self.baseline, current)
        for result in ([], self.baseline["results"] * 2):
            self.current["results"] = result
            with self.assertRaises(ValueError):
                regression.compare(self.baseline, self.current)

    def test_environment_change_requires_its_own_baseline(self):
        self.current["cpu_count"] = 8
        with self.assertRaisesRegex(ValueError, "Incomparable"):
            regression.compare(self.baseline, self.current)
