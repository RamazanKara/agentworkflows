"""Command-line access to the governed gateway."""

from __future__ import annotations

import argparse
import json
import os
import sys
from uuid import UUID, uuid4

import httpx

from agentworkflows import GatewayClient, GatewayError, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentworkflows",
        description="Run governed workflows, review approvals, and inspect team usage through AgentWorkflows.",
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
    commands.add_parser("team", help="Show your team, role, projects, and configured providers.")
    runs = commands.add_parser("runs", help="Start, list, inspect, cancel, retry, or approve workflow runs.")
    operations = runs.add_subparsers(dest="operation", required=True)
    start = operations.add_parser("start", help="Start an approved workflow; defaults to ResearchWorkflow.")
    start.add_argument("workflow", nargs="?", default="ResearchWorkflow")
    start.add_argument(
        "--input", required=True, type=json.loads, help='JSON input, e.g. \'{"topic":"Evaluate AI agents"}\'.'
    )
    start.add_argument("--project", help="Project from agentworkflows team; defaults to your credential's project.")
    start.add_argument(
        "--request-id", type=UUID, help="Reuse after an ambiguous start failure to avoid a duplicate run."
    )
    listing = operations.add_parser("list", help="List the latest runs in your project.")
    listing.add_argument("--project")
    listing.add_argument("--offset", type=int, default=0, help="next_offset from the previous page.")
    for operation in ("inspect", "cancel", "retry", "approve"):
        sub = operations.add_parser(
            operation,
            help={
                "inspect": "Show status, draft, budgets, and the step timeline with receipt IDs.",
                "cancel": "Request cancellation; already-sent tool actions cannot be undone.",
                "retry": "Start a fresh run after failure or cancellation; all steps may execute again.",
                "approve": "Approve the waiting draft using your authenticated identity.",
            }[operation],
        )
        sub.add_argument("run_id", type=UUID)
        if operation == "approve":
            sub.add_argument("--reject", action="store_true", help="Reject the draft without publishing.")
    args = parser.parse_args(argv)
    api_key = os.environ.get("AGENTWORKFLOWS_API_KEY")
    if not api_key:
        parser.error("set AGENTWORKFLOWS_API_KEY to your gateway key (local-development-only for the Compose demo)")
    base_url = os.environ.get("AGENTWORKFLOWS_URL", "http://127.0.0.1:8080")
    start_hint = ""
    if args.command == "runs" and args.operation == "start":
        args.request_id = args.request_id or uuid4()
        start_hint = f" Retry this start with --request-id {args.request_id} to avoid duplicates."
    try:
        with GatewayClient(base_url, api_key=api_key) as gateway:
            if args.command == "chat":
                reply = gateway.chat([{"role": "user", "content": args.prompt}], model=args.model)
                print(reply["choices"][0]["message"].get("content") or "")
            elif args.command == "models":
                for model in gateway.models()["data"]:
                    print(model["id"])
            elif args.command == "team":
                print(json.dumps(gateway.team(), indent=2))
            elif args.command == "runs":
                if args.operation == "start":
                    result = gateway.start_run(
                        args.workflow,
                        args.input,
                        project=args.project,
                        request_id=str(args.request_id) if args.request_id else None,
                    )
                elif args.operation == "list":
                    result = gateway.runs(project=args.project, offset=args.offset)
                elif args.operation == "inspect":
                    result = gateway.run(str(args.run_id))
                elif args.operation == "cancel":
                    result = gateway.cancel_run(str(args.run_id))
                elif args.operation == "retry":
                    result = gateway.retry_run(str(args.run_id))
                else:
                    result = gateway.approve_run(str(args.run_id), approved=not args.reject)
                print(json.dumps(result, indent=2))
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
        print(
            f"agentworkflows: {exc}. {hint} Request ID: {exc.request_id or 'unavailable'}.{start_hint}", file=sys.stderr
        )
        return 1
    except httpx.HTTPError:
        print(
            "agentworkflows: cannot reach the gateway. Check AGENTWORKFLOWS_URL and gateway health." + start_hint,
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
