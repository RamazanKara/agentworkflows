"""Synthetic research and publication tools for the example; no outbound requests."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def send(self, status, result):
        content = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.send(200, {"status": "ok"})

    def do_POST(self):
        json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/research":
            self.send(
                200,
                [{
                    "title": "Example research notes",
                    "url": "https://example.org/research",
                    "text": "Synthetic source: evaluate reliability, approvals and cost before rolling out agents.",
                }],
            )
        elif self.path == "/publish":
            key = self.headers["Idempotency-Key"]
            self.send(200, {"url": f"https://example.org/briefings/{key}", "published": True, "synthetic": True})
        else:
            self.send(404, {"error": "Unknown example tool"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
