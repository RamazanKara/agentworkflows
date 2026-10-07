"""Minimal code-executing agent for the existing agent-workspace image."""

import json
import os
import sys
import urllib.request
from pathlib import Path

arguments = json.load(sys.stdin)
request = urllib.request.Request(
    os.environ["AGENTWORKFLOWS_URL"].rstrip("/") + "/v1/chat/completions",
    headers={"Authorization": "Bearer " + os.environ["AGENTWORKFLOWS_API_KEY"], "Content-Type": "application/json"},
    data=json.dumps(
        {"model": "demo-openai", "max_tokens": 128, "messages": [{"role": "user", "content": arguments["task"]}]}
    ).encode(),
)
with urllib.request.urlopen(request, timeout=90) as response:
    text = json.load(response)["choices"][0]["message"]["content"]
Path("review.txt").write_text(text, encoding="utf-8")
print(text)
