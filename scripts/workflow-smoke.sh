#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f deploy/compose/compose.yaml)
OUT="${OUTPUT_DIR:-.out/compose}"
mkdir -p "$OUT"

client() {
  "${COMPOSE[@]}" run --rm --no-deps -T workflow-worker python -m agentworkflows.examples.research "$@"
}

client start "How should our team evaluate durable AI agents?" >"$OUT/workflow.json"
workflow_id="$(python3 -c "import json; print(json.load(open('$OUT/workflow.json'))['workflow_id'])")"
run_id="$(python3 -c "import json; print(json.load(open('$OUT/workflow.json'))['run_id'])")"
echo "[workflow] $workflow_id / $run_id: research, then draft with cloud fallback"
for _ in $(seq 1 30); do
  client status "$workflow_id" "$run_id" >"$OUT/workflow-status.json"
  stage="$(python3 -c "import json; print(json.load(open('$OUT/workflow-status.json'))['stage'])")"
  [[ "$stage" == "awaiting_approval" ]] && break
  sleep 1
done
[[ "$stage" == "awaiting_approval" ]] || { echo "Draft did not reach approval; inspect Temporal UI and worker logs." >&2; exit 1; }

echo "[workflow] killing the worker; sending an approval signal while it is offline"
"${COMPOSE[@]}" kill -s SIGKILL workflow-worker
trap '"${COMPOSE[@]}" up -d --no-deps workflow-worker >/dev/null' EXIT
"${COMPOSE[@]}" exec -T temporal temporal workflow signal --address temporal:7233 \
  --workflow-id "$workflow_id" --run-id "$run_id" --name approve --input true --input '"compose-reviewer"'
"${COMPOSE[@]}" up -d --no-deps workflow-worker
trap - EXIT
client result "$workflow_id" "$run_id" >"$OUT/workflow-result.json"
"${COMPOSE[@]}" logs --no-color --no-log-prefix inference-gateway >"$OUT/workflow-audit.log" 2>&1
python3 - "$run_id" "$OUT" <<'PY'
import json
import pathlib
import sys

run_id, output = sys.argv[1], pathlib.Path(sys.argv[2])
result = json.loads((output / "workflow-result.json").read_text())
assert result["status"] == "published", result
events = {}
for line in (output / "workflow-audit.log").read_text().splitlines():
    if '"record_hash"' not in line:
        continue
    event = json.loads(line[line.index("{"):])
    if event.get("workflow_run_id") == run_id:
        events[event["record_hash"]] = event
models = [e for e in events.values() if e["action_type"] == "model_call"]
tools = [e for e in events.values() if e["action_type"] == "tool_exec"]
assert len(models) == 2 and all(e["status_code"] == 200 for e in models), models
assert {e["tool"] for e in tools} == {"research", "publish"} and len(tools) == 2, tools
assert any([a["status"] for a in e["routing_attempts"]] == ["failed", "served"] for e in models), models
assert len({e["workflow_step_id"] for e in events.values()}) == 4
print("[workflow] published after SIGKILL: exactly two completed model calls, two tool calls, correlated receipts")
PY
