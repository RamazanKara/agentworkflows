"""Workspace-side runner; the worker supplies a short-lived grant through stdin."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import time


async def main() -> None:
    if sys.platform != "linux":
        raise RuntimeError("The container runner requires Linux; invoke it through WorkflowGateway.container.")
    plan = json.load(sys.stdin)
    remaining = plan["expires_at"] - time.time()
    if remaining <= 0:
        print(json.dumps({"exit_code": -1, "output": ""}))
        return
    env = {
        "PATH": os.defpath + ":/usr/local/bin",
        "HOME": "/workspace",
        "TMPDIR": "/tmp",
        "AGENTWORKFLOWS_URL": plan["gateway_url"],
        "AGENTWORKFLOWS_API_KEY": plan["api_key"],
        "OPENAI_BASE_URL": plan["gateway_url"].rstrip("/") + "/v1",
        "OPENAI_API_KEY": plan["api_key"],
        "ANTHROPIC_BASE_URL": plan["gateway_url"],
        "ANTHROPIC_API_KEY": plan["api_key"],
        "AGENTWORKFLOWS_RUN_ID": plan["headers"]["X-Workflow-Run-ID"],
        "AGENTWORKFLOWS_STEP_ID": plan["headers"]["X-Workflow-Step-ID"],
    }
    process = await asyncio.create_subprocess_exec(
        *plan["command"],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        cwd="/workspace",
        start_new_session=True,
    )
    exit_code = -1
    output = bytearray()
    try:
        async with asyncio.timeout(remaining):
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(plan["arguments"]).encode())
            await process.stdin.drain()
            process.stdin.close()
            while chunk := await process.stdout.read(65536):
                if len(output) + len(chunk) > 65536:
                    raise ValueError("agent output exceeds 64 KiB")
                output.extend(chunk)
            exit_code = await process.wait()
    except (TimeoutError, ValueError, BrokenPipeError):
        output.clear()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
    print(json.dumps({"exit_code": exit_code, "output": output.decode("utf-8", errors="replace")}))


if __name__ == "__main__":
    asyncio.run(main())
