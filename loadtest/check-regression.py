#!/usr/bin/env python3
"""Compare gateway-sanity reports: p95 +25%, throughput -20%, and zero failures."""

import argparse
import json
import math
from pathlib import Path


def rows(report):
    result = {}
    for row in report["results"]:
        key = (row["method"], row["path"])
        if key in result:
            raise ValueError(f"Duplicate route: {key}")
        if row["requests"] <= 0 or row["concurrency"] <= 0:
            raise ValueError(f"Empty workload: {key}")
        if row["statuses"] != {"200": row["requests"]}:
            raise ValueError(f"Failed or missing requests: {key}")
        for name in ("rps", "p95_ms"):
            value = row[name]
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid {name}: {key}")
        result[key] = row
    if not result:
        raise ValueError("Empty report")
    return result


def compare(baseline, current):
    for field in ("platform", "python", "cpu_count", "runtime"):
        if baseline[field] != current[field]:
            raise ValueError(f"Incomparable environment: {field}; record a baseline on this runner")
    previous, measured = rows(baseline), rows(current)
    if previous.keys() != measured.keys():
        raise ValueError("Workload routes differ")
    failures = []
    for key, row in measured.items():
        old = previous[key]
        if any(row[field] != old[field] for field in ("requests", "concurrency")):
            raise ValueError(f"Workload differs: {key}")
        if row["p95_ms"] > old["p95_ms"] * 1.25:
            failures.append(f"{key}: p95 {row['p95_ms']}ms exceeds {old['p95_ms'] * 1.25:.2f}ms")
        if row["rps"] < old["rps"] * 0.8:
            failures.append(f"{key}: throughput {row['rps']}rps below {old['rps'] * 0.8:.2f}rps")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    args = parser.parse_args()
    try:
        failures = compare(
            json.loads(args.baseline.read_text(encoding="utf-8")),
            json.loads(args.current.read_text(encoding="utf-8")),
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print("\n".join(failures) if failures else "PASS: p95, throughput and zero-error thresholds")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
