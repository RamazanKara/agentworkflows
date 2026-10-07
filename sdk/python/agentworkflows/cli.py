"""Command-line access to the governed gateway."""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from agentworkflows import GatewayClient, GatewayError, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentworkflows",
        description="Call models and inspect team usage through AgentWorkflows.",
        epilog="Set AGENTWORKFLOWS_API_KEY and AGENTWORKFLOWS_URL (default: http://127.0.0.1:8080).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="List the models your team can use.")
    chat = commands.add_parser("chat", help="Send a prompt through the governed gateway.")
    chat.add_argument("prompt")
    chat.add_argument(
        "--model", help="Approved model ID from 'agentworkflows models'; defaults to the gateway's model."
    )
    commands.add_parser("usage", help="Show your team's token usage, estimated cost, and provider breakdown.")
    args = parser.parse_args(argv)
    api_key = os.environ.get("AGENTWORKFLOWS_API_KEY")
    if not api_key:
        parser.error("set AGENTWORKFLOWS_API_KEY to your gateway key (local-development-only for the Compose demo)")
    base_url = os.environ.get("AGENTWORKFLOWS_URL", "http://127.0.0.1:8080")
    try:
        with GatewayClient(base_url, api_key=api_key) as gateway:
            if args.command == "chat":
                reply = gateway.chat([{"role": "user", "content": args.prompt}], model=args.model)
                print(reply["choices"][0]["message"].get("content") or "")
            elif args.command == "models":
                for model in gateway.models()["data"]:
                    print(model["id"])
            else:
                print(json.dumps(gateway.usage(), indent=2))
    except GatewayError as exc:
        hints = {
            401: "Check AGENTWORKFLOWS_API_KEY.",
            403: "Ask your team administrator to check the credential scope and routing policy.",
            429: "Check 'agentworkflows usage' and the team's budget or rate limit before retrying.",
        }
        hint = hints.get(exc.status_code, "Check the gateway logs using the request ID.")
        if exc.reason == "model_not_allowed":
            hint = "Choose an approved model from 'agentworkflows models'."
        print(f"agentworkflows: {exc}. {hint} Request ID: {exc.request_id or 'unavailable'}", file=sys.stderr)
        return 1
    except httpx.HTTPError:
        print("agentworkflows: cannot reach the gateway. Check AGENTWORKFLOWS_URL and gateway health.", file=sys.stderr)
        return 1
    return 0
