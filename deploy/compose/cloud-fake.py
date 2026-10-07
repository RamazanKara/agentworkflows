"""Local wire-protocol fixtures for the Compose walkthrough. No external requests."""

import json
import struct
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar


def bedrock_event(kind, data):
    headers = b""
    for name, value in ((":message-type", "event"), (":event-type", kind), (":content-type", "application/json")):
        key, val = name.encode(), value.encode()
        headers += bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(val)) + val
    body = json.dumps(data).encode()
    prelude = struct.pack(">II", len(headers) + len(body) + 16, len(headers))
    frame = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return frame + struct.pack(">I", zlib.crc32(frame))


class Handler(BaseHTTPRequestHandler):
    counts: ClassVar[dict] = {}
    publications: ClassVar[dict] = {}

    def log_message(self, *_args):
        pass

    def send(self, status, content, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.send(200, json.dumps(self.counts if self.path == "/stats" else {"status": "ok"}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        provider = self.path.split("/")[1]
        self.counts[provider] = self.counts.get(provider, 0) + 1
        if "hardening slow step" in json.dumps(body):
            time.sleep(5)
        if provider == "mcp":
            method = body.get("method")
            if method == "notifications/initialized":
                self.send(202, b"")
                return
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "team-fixture", "version": "1"},
                }
            elif method == "tools/call" and body["params"]["name"] == "search":
                result = {"content": [{"type": "text", "text": "Synthetic MCP source: evaluate reliability and cost."}]}
            else:
                self.send(400, b"{}")
                return
            self.send(200, json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode())
            return
        if provider == "tools":
            tool = self.path.split("/")[-1]
            if tool == "research":
                result = [
                    {
                        "title": "Team research notes",
                        "url": "https://example.org/research",
                        "text": "Synthetic source: measure reliability and cost before rolling out agents.",
                    }
                ]
            elif tool == "publish":
                key = self.headers.get("Idempotency-Key")
                if not key:
                    self.send(400, b'{"error":"Idempotency-Key required"}')
                    return
                result = self.publications.setdefault(
                    key, {"url": f"https://example.org/briefings/{key}", "published": True}
                )
            elif tool == "report_source":
                snapshots = {
                    "changes": "Two changes shipped: session reset fix and audit export. Owner: engineering.",
                    "support": (
                        "12 tickets opened, 9 closed; 5 concern sign-in. Owner: support. Backlog baseline missing."
                    ),
                    "incidents": (
                        "INC-1042: elevated sign-in errors for 15 minutes. Rollback mitigated it; cause unconfirmed."
                    ),
                }
                source = body["source"]
                result = {
                    "id": source,
                    "url": f"https://example.test/weekly/{source}",
                    "period": body["period"],
                    "text": "Synthetic snapshot: " + snapshots[source],
                }
            elif tool == "incident_logs":
                result = {
                    "incident_id": body["incident_id"],
                    "source": "https://example.test/incidents/INC-1042/logs",
                    "synthetic": True,
                    "lines": [
                        "L1 2026-10-01T09:00:00Z auth deployment v42 started",
                        "L2 2026-10-01T09:05:00Z auth sign_in 503 rate=18%",
                        "L3 2026-10-01T09:12:00Z auth rollback to v41 started",
                        "L4 2026-10-01T09:20:00Z auth sign_in 503 rate=0.2% baseline restored",
                    ],
                }
            elif tool == "search_documents":
                query = body["query"].lower()
                result = []
                if "approv" in query:
                    result.append(
                        {
                            "id": "S1",
                            "title": "Approval handbook (fixture)",
                            "url": "https://example.test/handbook/approvals",
                            "text": (
                                "Admins and approvers can decide a waiting draft. Approval expires after seven days."
                            ),
                        }
                    )
                if "receipt" in query:
                    result.append(
                        {
                            "id": "S2",
                            "title": "Receipt handbook (fixture)",
                            "url": "https://example.test/handbook/receipts",
                            "text": (
                                "Receipts detect changes within retained hash chains. "
                                "External anchors detect truncation."
                            ),
                        }
                    )
            else:
                self.send(404, b"{}")
                return
            self.send(200, json.dumps(result).encode())
            return
        # The example's second model call exercises fallback after research has completed.
        if provider == "openai" and "Write a concise team briefing" in json.dumps(body):
            self.send(503, b'{"error":"fixture drafting outage"}')
            return
        if provider == "local-overload":
            self.send(503, b'{"error":"fixture overload"}')
            return
        header = "x-api-key" if provider == "anthropic" else "Authorization"
        expected = "compose-fake-only" if provider == "anthropic" else "Bearer compose-fake-only"
        if self.headers.get(header) != expected:
            self.send(401, b'{"error":"fixture authentication failed"}')
            return
        text = f"Local fake for {provider}; no cloud request was made."
        prompt = body.get("messages", [{}])[-1].get("content", "")
        if isinstance(prompt, str):
            if prompt.startswith("Review this PR diff"):
                text = (
                    "Synthetic PR review: HIGH auth.py:10 returns True for every user, bypassing the admin check. "
                    "Restore user.is_admin and test admin/non-admin access. Confirm caller context before approval."
                )
            elif prompt.startswith("Triage this support ticket"):
                text = (
                    "Synthetic triage: category=access; priority=high; owner=identity-support. "
                    "Draft reply: We are investigating sign-in after password resets. Please share the error "
                    "message and time, without passwords or reset links. No customer reply has been sent."
                )
            elif prompt.startswith("Write a weekly team report from these source snapshots"):
                text = (
                    "Synthetic weekly report: session reset fix and audit export shipped [changes] "
                    "(https://example.test/weekly/changes). Support opened 12 and closed 9 tickets, including "
                    "5 sign-in reports [support] (https://example.test/weekly/support). INC-1042 was mitigated "
                    "by rollback [incidents] (https://example.test/weekly/incidents). Engineering should verify "
                    "the cause; support should establish the backlog baseline."
                )
            elif prompt.startswith("Summarize this incident log export"):
                text = (
                    "Synthetic incident summary: deployment at 09:00 UTC [L1]; 18% sign-in 503s at 09:05 [L2]; "
                    "rollback at 09:12 [L3]; baseline restored at 09:20 [L4]. The deployment is a hypothesis, "
                    "not a confirmed root cause. Follow up on affected-user count and the v42 failure mechanism."
                )
            elif prompt.startswith("Answer the question using only the supplied document excerpts"):
                sources = json.loads(prompt.split("\n", 1)[1])["sources"]
                text = json.dumps(
                    {
                        "answer": "Synthetic answer: " + " ".join(f"{s['text']} [{s['id']}]" for s in sources),
                        "citation_ids": [s["id"] for s in sources],
                    }
                )
        streaming = body.get("stream") or self.path.endswith("converse-stream")
        usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
        if provider == "anthropic":
            result = {
                "id": "fixture",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
                {"type": "message_stop"},
            ]
        elif provider == "bedrock":
            result = {
                "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 5, "outputTokens": 2},
            }
            if streaming:
                wire = b"".join(
                    bedrock_event(kind, data)
                    for kind, data in [
                        ("messageStart", {"role": "assistant"}),
                        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": text}}),
                        ("messageStop", {"stopReason": "end_turn"}),
                        ("metadata", {"usage": result["usage"]}),
                    ]
                )
                self.send(200, wire, "application/vnd.amazon.eventstream")
                return
            events = []
        else:
            result = {
                "id": "fixture",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": usage,
            }
            events = [
                {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]},
                {"choices": [], "usage": usage},
            ]
        if streaming:
            wire = b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)
            if provider != "anthropic":
                wire += b"data: [DONE]\n\n"
            self.send(200, wire, "text/event-stream")
        else:
            self.send(200, json.dumps(result).encode())


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
