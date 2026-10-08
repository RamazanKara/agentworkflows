"""Command-line access to the governed gateway."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx

from agentworkflows import GatewayClient, GatewayError, __version__
from agentworkflows.scaffold import TEMPLATES, init_project


def workflow_input(value: str) -> object:
    try:
        text = Path(value[1:]).read_text(encoding="utf-8-sig") if value.startswith("@") else value
        parsed = json.loads(text)
        json.dumps(parsed, allow_nan=False)
        return parsed
    except (OSError, UnicodeError):
        raise argparse.ArgumentTypeError("Cannot read input file. Use --input @path/to/input.json (UTF-8).") from None
    except ValueError:
        raise argparse.ArgumentTypeError(
            'Input must be valid JSON. Use --input @input.json or --input \'{"topic":"Evaluate agents"}\'.'
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentworkflows",
        description="Run governed workflows, review approvals, and inspect team usage through AgentWorkflows.",
        epilog="Set AGENTWORKFLOWS_API_KEY and AGENTWORKFLOWS_URL (default: http://127.0.0.1:8080).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create an editable workflow project; no key or server required.")
    init.add_argument("directory", nargs="?", default=".", type=Path, help="New or empty directory (default: current).")
    init.add_argument(
        "--template", choices=TEMPLATES, default="research", help="Workflow template (default: research)."
    )
    commands.add_parser("models", help="List the models your team can use.")
    chat = commands.add_parser("chat", help="Send a prompt through the governed gateway.")
    chat.add_argument("prompt")
    chat.add_argument(
        "--model", help="Approved model ID from 'agentworkflows models'; defaults to the gateway's model."
    )
    commands.add_parser("usage", help="Show your team's token usage, estimated cost, and provider breakdown.")
    commands.add_parser("team", help="Show your team, role, projects, and configured providers.")
    keys = commands.add_parser("keys", help="Manage your team's API keys (admin only).")
    key_operations = keys.add_subparsers(dest="operation", required=True)
    key_operations.add_parser("list", help="List managed keys, including revoked and expired keys.")
    for operation in ("create", "update", "revoke"):
        sub = key_operations.add_parser(operation, help=f"{operation.title()} a team API key.")
        if operation != "create":
            sub.add_argument("key_id")
        if operation != "revoke":
            sub.add_argument("--name", required=operation == "create")
            sub.add_argument("--role", choices=("admin", "builder", "approver", "viewer"))
            sub.add_argument("--project", help="Project binding; use an empty string to clear it.")
            sub.add_argument("--expires-at", help="ISO-8601 expiry with timezone; use an empty string to clear it.")
    triggers = commands.add_parser("triggers", help="Inspect, pause or resume configured workflow triggers.")
    trigger_operations = triggers.add_subparsers(dest="operation", required=True)
    trigger_operations.add_parser("list", help="Show schedules (UTC), webhook endpoints and pause state.")
    for operation in ("pause", "resume"):
        sub = trigger_operations.add_parser(operation, help=f"{operation.title()} a configured workflow trigger.")
        sub.add_argument("workflow")
        sub.add_argument("name")
    runs = commands.add_parser("runs", help="Start, list, inspect, cancel, retry, or approve workflow runs.")
    operations = runs.add_subparsers(dest="operation", required=True)
    start = operations.add_parser("start", help="Start an approved workflow; defaults to ResearchWorkflow.")
    start.add_argument("workflow", nargs="?", default="ResearchWorkflow")
    start.add_argument(
        "--input", required=True, type=workflow_input, help="JSON or @file, e.g. @input.json from agentworkflows init."
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
    if args.command == "init":
        try:
            init_project(args.directory, args.template)
        except ValueError as exc:
            parser.error(str(exc))
        except OSError:
            parser.error("Cannot write the project. Choose a writable, empty directory and retry.")
        name = TEMPLATES[args.template][1]
        print(f"Created {args.template} project in {args.directory.resolve()}")
        print(f'Next: cd "{args.directory}"')
        print(
            "Start the stack and set your gateway key: https://ramazankara.github.io/agentworkflows/latest/quickstart/"
        )
        print(f"Then: agentworkflows runs start {name} --input '@input.json'")
        print("Edit workflow.py; README.md explains how to run your edited worker.")
        return 0
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
            elif args.command == "keys":
                path = "/v1/team/keys"
                if args.operation in {"update", "revoke"}:
                    path += "/" + quote(args.key_id, safe="")
                method = {"list": "GET", "create": "POST", "update": "PATCH", "revoke": "DELETE"}[args.operation]
                body = {
                    field: (getattr(args, field) or None)
                    for field in ("name", "role", "project", "expires_at")
                    if getattr(args, field, None) is not None
                }
                response = gateway._request(
                    method, path, creates_state=args.operation == "create",
                    **({"json": body} if args.operation in {"create", "update"} else {}),
                )
                print(json.dumps(response.json(), indent=2))
            elif args.command == "triggers":
                result = (
                    gateway.triggers()
                    if args.operation == "list"
                    else gateway.pause_trigger(args.workflow, args.name, paused=args.operation == "pause")
                )
                print(json.dumps(result, indent=2))
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
            404: "Check AGENTWORKFLOWS_URL points to AgentWorkflows, not another service on the same port. "
            "For a missing run, use 'agentworkflows runs list'.",
            429: "Check 'agentworkflows usage' and the team's budget or rate limit before retrying.",
        }
        hint = hints.get(exc.status_code, "Check the gateway logs using the request ID.")
        if exc.reason == "model_not_allowed":
            hint = "Choose an approved model from 'agentworkflows models'."
        elif exc.reason == "workflow_run_missing":
            hint = "Use 'agentworkflows runs list' with the correct team's gateway key and project."
        print(
            f"agentworkflows: {exc}. {hint} Request ID: {exc.request_id or 'unavailable'}.{start_hint}", file=sys.stderr
        )
        return 1
    except httpx.HTTPError:
        print(
            "agentworkflows: cannot reach the gateway. Check AGENTWORKFLOWS_URL "
            "(default http://127.0.0.1:8080) and start the stack using the Quickstart." + start_hint,
            file=sys.stderr,
        )
        return 1
    except httpx.InvalidURL:
        print(
            "agentworkflows: invalid AGENTWORKFLOWS_URL. Use a full URL such as http://127.0.0.1:8080.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
