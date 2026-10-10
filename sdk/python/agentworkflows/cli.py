"""Command-line access to the governed gateway."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from agentworkflows import GatewayClient, GatewayError, __version__
from agentworkflows.scaffold import TEMPLATES, init_project
from agentworkflows.types import TeamSettingValue


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


def settings_fields(value: str) -> dict[str, TeamSettingValue]:
    try:
        parsed = workflow_input(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError(str(exc).replace("--input", "--fields")) from None
    if not isinstance(parsed, dict) or not parsed:
        raise argparse.ArgumentTypeError("--fields must be a non-empty JSON object mapping setting names to values.")
    return parsed


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
    usage = commands.add_parser("usage", help="Show your team's token usage, estimated cost, and provider breakdown.")
    usage.add_argument("--output", help="Export current UTC month usage as CSV; '-' writes to stdout.")
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
    workflows = commands.add_parser(
        "workflows", help="Register your own workflow types without a gateway redeploy (unrestricted admin only)."
    )
    workflow_operations = workflows.add_subparsers(dest="operation", required=True)
    workflow_operations.add_parser("list", help="Show registered workflows, reserved names and what you may use.")
    register = workflow_operations.add_parser(
        "register", help="Register or replace a workflow within the models, tools and limits your team already has."
    )
    register.add_argument("name", help="Temporal workflow type name, e.g. CodeReviewWorkflow.")
    register.add_argument("--model", dest="models", action="append", required=True, help="Approved model; repeatable.")
    register.add_argument("--tool", dest="tools", action="append", help="Approved team tool; repeatable.")
    register.add_argument("--token-limit", type=int, help="Tokens per run (default 10000).")
    register.add_argument("--cost-limit", type=float, help="USD per run (default 5).")
    register.add_argument("--reviewers", type=int, help="Distinct approvals required (1-10; default 1).")
    register.add_argument("--no-approval", action="store_true", help="Skip the human approval gate.")
    register.add_argument("--input-schema", type=workflow_input, help="Flat JSON Schema or @file for the run form.")
    register.add_argument("--revision", type=int, help="Reviewed revision from 'workflows list' (default: current).")
    remove = workflow_operations.add_parser("remove", help="Remove a registered workflow; new calls are denied.")
    remove.add_argument("name")
    remove.add_argument("--revision", type=int, help="Reviewed revision from 'workflows list' (default: current).")
    settings = commands.add_parser("settings", help="Inspect or change team settings (unrestricted admin only).")
    setting_operations = settings.add_subparsers(dest="operation", required=True)
    setting_operations.add_parser("show", help="Show effective values, policy defaults and the current revision.")
    for operation in ("set", "reset"):
        sub = setting_operations.add_parser(operation, help=f"{operation.title()} settings at a reviewed revision.")
        sub.add_argument("--revision", required=True, type=int, help="Revision from 'agentworkflows settings show'.")
        if operation == "set":
            sub.add_argument("--fields", required=True, type=settings_fields, help="Field/value JSON object or @file.")
        else:
            sub.add_argument("field", help="Field name from settings show to reset to its policy default.")
    audit = commands.add_parser("audit", help="Read, verify or export retained team receipts (admin only).")
    audit_operations = audit.add_subparsers(dest="operation", required=True)
    for operation in ("list", "verify", "export"):
        sub = audit_operations.add_parser(operation, help=f"{operation.title()} the team's retained audit events.")
        sub.add_argument("--from", dest="from_time", type=float, help="Inclusive Unix timestamp in seconds.")
        sub.add_argument("--to", type=float, help="Inclusive Unix timestamp in seconds.")
        if operation != "verify":
            for field in ("event-type", "actor", "project", "run-id"):
                sub.add_argument(f"--{field}", help="Exact-match filter.")
            sub.add_argument("--cursor", help="next_cursor from an earlier page; read older events.")
            sub.add_argument("--limit", type=int, default=50, help="Events per page (1-200; default: 50).")
        if operation == "export":
            sub.add_argument("--output", default="-", help="JSON Lines file; '-' or omitted writes to stdout.")
    triggers = commands.add_parser("triggers", help="Inspect, pause or resume configured workflow triggers.")
    trigger_operations = triggers.add_subparsers(dest="operation", required=True)
    trigger_operations.add_parser("list", help="Show schedules (UTC), webhook endpoints and pause state.")
    for operation in ("pause", "resume"):
        sub = trigger_operations.add_parser(operation, help=f"{operation.title()} a configured workflow trigger.")
        sub.add_argument("workflow")
        sub.add_argument("name")
    runs = commands.add_parser("runs", help="Start, list, export, inspect, cancel, retry, or approve workflow runs.")
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
    for operation in ("list", "export"):
        sub = operations.add_parser(operation, help=f"{operation.title()} retained runs in your project.")
        sub.add_argument("--project")
        sub.add_argument("--workflow", help="Exact workflow name.")
        sub.add_argument("--trigger", help="Exact trigger name; requires --workflow. History starts with v0.7.0.")
        sub.add_argument("--status", choices=(
            "running", "awaiting_approval", "completed", "failed", "canceled", "terminated", "timed_out",
            "continued_as_new",
        ))
        sub.add_argument("--limit", type=int, default=20, help="Records scanned per page (1-100; default: 20).")
        paging = sub.add_mutually_exclusive_group()
        paging.add_argument("--cursor", help="next_cursor from the previous page; keep the same filters.")
        paging.add_argument("--offset", type=int, help="Legacy next_offset; prefer --cursor while runs change.")
        if operation == "export":
            sub.add_argument("--output", default="-", help="JSON Lines file; '-' or omitted writes to stdout.")
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
                body: dict[str, Any] = {
                    field: (getattr(args, field) or None)
                    for field in ("name", "role", "project", "expires_at")
                    if getattr(args, field, None) is not None
                }
                if args.operation == "list":
                    key_result: object = gateway.list_keys()
                elif args.operation == "create":
                    key_result = gateway.create_key(**body)
                elif args.operation == "update":
                    key_result = gateway.update_key(args.key_id, **body)
                else:
                    key_result = gateway.revoke_key(args.key_id)
                print(json.dumps(key_result, indent=2))
            elif args.command == "workflows":
                if args.operation == "list":
                    registry_result: object = gateway.team_workflows()
                elif args.operation == "register":
                    registration: dict[str, Any] = {
                        field: value
                        for field, value in {
                            "allowed_tools": args.tools,
                            "token_limit": args.token_limit,
                            "cost_limit_usd": args.cost_limit,
                            "required_approvals": args.reviewers,
                            "approval_required": False if args.no_approval else None,
                            "input_schema": args.input_schema,
                        }.items()
                        if value is not None
                    }
                    registry_result = gateway.register_workflow(
                        args.name, models=args.models, revision=args.revision, **registration
                    )
                else:
                    registry_result = gateway.remove_workflow(args.name, revision=args.revision)
                print(json.dumps(registry_result, indent=2))
            elif args.command == "settings":
                if args.operation == "show":
                    settings_result = gateway.team_settings()
                elif args.operation == "set":
                    settings_result = gateway.update_team_settings(args.fields, revision=args.revision)
                else:
                    settings_result = gateway.reset_team_setting(args.field, revision=args.revision)
                print(json.dumps(settings_result, indent=2))
            elif args.command == "audit":
                filters: dict[str, Any] = {
                    field: getattr(args, field)
                    for field in ("from_time", "to", "event_type", "actor", "project", "run_id", "cursor", "limit")
                    if getattr(args, field, None) is not None
                }
                if args.operation == "list":
                    print(json.dumps(gateway.audit(**filters), indent=2))
                elif args.operation == "verify":
                    verification = gateway.verify_audit(**filters)
                    print(json.dumps(verification, indent=2))
                    if verification["ok"] is not True:
                        print(verification.get("message") or "Audit verification failed; inspect first_break.",
                              file=sys.stderr)
                        return 1
                else:
                    try:
                        with (
                            nullcontext(sys.stdout) if args.output == "-"
                            else Path(args.output).open("w", encoding="utf-8", newline="\n")
                        ) as output:
                            output.writelines(gateway.export_audit(**filters))
                    except (OSError, RuntimeError) as exc:
                        print(f"agentworkflows: audit export failed: {exc}", file=sys.stderr)
                        return 1
            elif args.command == "triggers":
                result: object = (
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
                elif args.operation in {"list", "export"}:
                    run_filters = {
                        field: getattr(args, field)
                        for field in ("project", "workflow", "trigger", "status", "limit", "cursor", "offset")
                        if getattr(args, field) is not None
                    }
                    if args.operation == "export":
                        try:
                            with (
                                nullcontext(sys.stdout) if args.output == "-"
                                else Path(args.output).open("w", encoding="utf-8", newline="\n")
                            ) as output:
                                output.writelines(gateway.export_runs(**run_filters))
                        except (OSError, RuntimeError) as exc:
                            print(f"agentworkflows: run export failed: {exc}", file=sys.stderr)
                            return 1
                        return 0
                    result = gateway.runs(**run_filters)
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
                if args.output is None:
                    print(json.dumps(gateway.usage(), indent=2))
                else:
                    try:
                        content = gateway.export_usage()
                        if args.output == "-":
                            sys.stdout.buffer.write(content.encode("utf-8"))
                        else:
                            Path(args.output).write_text(content, encoding="utf-8", newline="")
                    except OSError as exc:
                        print(f"agentworkflows: usage export failed: {exc}", file=sys.stderr)
                        return 1
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
        elif exc.reason == "team_settings_conflict":
            hint = "Run 'agentworkflows settings show', review the changes, then retry with its --revision."
        elif exc.reason == "team_workflows_conflict":
            hint = "Run 'agentworkflows workflows list', review the changes, then retry."
        elif exc.reason == "team_workflows_disabled":
            hint = "Your operator keeps workflow types in reviewed policy; ask them to add this one."
        elif args.command == "workflows" and exc.status_code == 422:
            hint = "Use only models and tools shown by 'agentworkflows workflows list', within its limits."
        elif args.command == "settings" and exc.status_code == 422:
            hint = "Correct the named fields using 'agentworkflows settings show' and 'settings set --help'."
        elif args.command == "audit" and exc.status_code == 422:
            hint = "Check the time range, cursor and filters using 'agentworkflows audit --help'."
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
