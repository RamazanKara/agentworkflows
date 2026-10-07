#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f deploy/compose/compose.yaml)
OUT="${OUTPUT_DIR:-.out/compose}"
mkdir -p "$OUT"

client() {
  "${COMPOSE[@]}" exec -T -e AGENTWORKFLOWS_API_KEY="${CLIENT_KEY:-demo-builder}" workflow-worker python -m agentworkflows.cli "$@"
}

client team
client runs start --input '{"topic":"How should our team evaluate durable AI agents?"}' >"$OUT/workflow.json"
run_id="$(python3 -c "import json; print(json.load(open('$OUT/workflow.json'))['run_id'])")"
echo "[workflow] $run_id: authenticated start, research, draft, and approval"
for _ in $(seq 1 40); do
  client runs inspect "$run_id" >"$OUT/workflow-status.json"
  stage="$(python3 -c "import json; print(json.load(open('$OUT/workflow-status.json')).get('progress',{}).get('stage',''))")"
  [[ "$stage" == "awaiting_approval" ]] && break
  sleep 1
done
[[ "$stage" == "awaiting_approval" ]] || { echo "Draft did not reach approval; inspect the worker logs." >&2; exit 1; }
client runs list >"$OUT/workflow-list.json"
if client runs approve "$run_id"; then echo 'Builder must not approve' >&2; exit 1; fi
if CLIENT_KEY=demo-other-team client runs inspect "$run_id"; then echo 'Team isolation failed' >&2; exit 1; fi
if CLIENT_KEY=demo-viewer client runs start --input '{"topic":"denied"}'; then echo 'Viewer started a run' >&2; exit 1; fi

echo "[workflow] killing and replacing the worker while approval is waiting"
"${COMPOSE[@]}" kill -s SIGKILL workflow-worker
"${COMPOSE[@]}" up -d --no-deps workflow-worker
for _ in $(seq 1 30); do
  client runs inspect "$run_id" >"$OUT/workflow-status.json"
  stage="$(python3 -c "import json; print(json.load(open('$OUT/workflow-status.json')).get('progress',{}).get('stage',''))")"
  [[ "$stage" == "awaiting_approval" ]] && break
  sleep 1
done
CLIENT_KEY=demo-approver client runs approve "$run_id"
for _ in $(seq 1 40); do
  client runs inspect "$run_id" >"$OUT/workflow-result.json"
  status="$(python3 -c "import json; print(json.load(open('$OUT/workflow-result.json'))['status'])")"
  [[ "$status" == "completed" ]] && break
  sleep 1
done
[[ "$status" == "completed" ]] || { echo "Workflow did not complete" >&2; exit 1; }
client usage >"$OUT/team-usage.json"
"${COMPOSE[@]}" logs --no-color --no-log-prefix inference-gateway >"$OUT/workflow-audit.log" 2>&1
python3 - "$run_id" "$OUT" <<'PY'
import json
import pathlib
import sys

run_id, output = sys.argv[1], pathlib.Path(sys.argv[2])
result = json.loads((output / "workflow-result.json").read_text())
assert result["status"] == "completed", result
assert len(result["timeline"]) >= 5, result
assert all(row["receipt_id"] for row in result["timeline"])
usage = json.loads((output / "team-usage.json").read_text())
assert usage["spend"]["reserved_and_spent_usd"] > 0
assert {"openai", "anthropic", "tool"} <= usage["providers"].keys()
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
assert len({e["workflow_step_id"] for e in models + tools}) == 4
print("[workflow] published after SIGKILL: exactly two completed model calls, two tool calls, correlated receipts")
PY

client runs start --input '{"topic":"Cancellation and retry"}' >"$OUT/cancel-run.json"
cancel_id="$(python3 -c "import json; print(json.load(open('$OUT/cancel-run.json'))['run_id'])")"
for _ in $(seq 1 30); do
  client runs inspect "$cancel_id" >"$OUT/cancel-status.json"
  stage="$(python3 -c "import json; print(json.load(open('$OUT/cancel-status.json')).get('progress',{}).get('stage',''))")"
  [[ "$stage" == "awaiting_approval" ]] && break
  sleep 1
done
[[ "$stage" == "awaiting_approval" ]] || { echo 'Cancellation fixture did not reach its waiting step' >&2; exit 1; }
client runs cancel "$cancel_id"
for _ in $(seq 1 30); do
  client runs inspect "$cancel_id" >"$OUT/cancel-status.json"
  status="$(python3 -c "import json; print(json.load(open('$OUT/cancel-status.json'))['status'])")"
  [[ "$status" == "canceled" ]] && break
  sleep 1
done
[[ "$status" == "canceled" ]] || { echo 'Cancellation did not finish' >&2; exit 1; }
client runs retry "$cancel_id" >"$OUT/retry-run.json"
retry_id="$(python3 -c "import json; print(json.load(open('$OUT/retry-run.json'))['run_id'])")"
client runs cancel "$retry_id"
echo "[workflow] roles, tenant isolation, timeline, spend, cancellation, and retry verified"

client runs start SupportTriageWorkflow --input '{"ticket":"I cannot sign in after resetting my password."}' >"$OUT/support-run.json"
support_id="$(python3 -c "import json; print(json.load(open('$OUT/support-run.json'))['run_id'])")"
for _ in $(seq 1 30); do
  client runs inspect "$support_id" >"$OUT/support-result.json"
  status="$(python3 -c "import json; print(json.load(open('$OUT/support-result.json'))['status'])")"
  [[ "$status" == "completed" ]] && break
  sleep 1
done
[[ "$status" == "completed" ]] || { echo 'Support triage did not complete; inspect worker logs.' >&2; exit 1; }

client runs start CodeReviewWorkflow --input '{"diff":"- return user.is_admin\n+ return True"}' >"$OUT/review-run.json"
review_id="$(python3 -c "import json; print(json.load(open('$OUT/review-run.json'))['run_id'])")"
for _ in $(seq 1 30); do
  client runs inspect "$review_id" >"$OUT/review-result.json"
  stage="$(python3 -c "import json; print(json.load(open('$OUT/review-result.json')).get('progress',{}).get('stage',''))")"
  [[ "$stage" == "awaiting_approval" ]] && break
  sleep 1
done
[[ "$stage" == "awaiting_approval" ]] || { echo 'Code review did not reach approval; inspect worker logs.' >&2; exit 1; }
CLIENT_KEY=demo-approver client runs approve "$review_id" --reject
for _ in $(seq 1 30); do
  client runs inspect "$review_id" >"$OUT/review-result.json"
  status="$(python3 -c "import json; print(json.load(open('$OUT/review-result.json'))['status'])")"
  [[ "$status" == "completed" ]] && break
  sleep 1
done
[[ "$status" == "completed" ]] || { echo 'Code review did not finish after rejection.' >&2; exit 1; }
python3 - "$OUT" <<'PY'
import json
import pathlib
import sys

output = pathlib.Path(sys.argv[1])
support = json.loads((output / "support-result.json").read_text())
review = json.loads((output / "review-result.json").read_text())
assert support["result"] and len(support["timeline"]) == 1, support
assert review["result"]["approved"] is False and review["result"]["reviewer"] == "demo-approver", review
assert {step["action"] for step in review["timeline"]} == {"model_call", "approval"}, review
assert all(step["receipt_id"] for row in (support, review) for step in row["timeline"])
print("[workflow] support triage result and rejected code review are receipted; no external actions")
PY
