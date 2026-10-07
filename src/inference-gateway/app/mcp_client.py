"""Bounded MCP Streamable HTTP tool calls to administrator-registered servers."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx
from fastapi import HTTPException

from app.policy import ToolRoute

PROTOCOL_VERSION = "2025-03-26"


async def read_tool_response(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    limit: int,
    *,
    rpc_id: int | None = None,
) -> Any:
    async with client.stream("POST", url, json=body, headers=headers) as response:
        response.raise_for_status()
        session = response.headers.get("Mcp-Session-Id")
        if session:
            headers["Mcp-Session-Id"] = session
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > limit:
                raise ValueError("tool response exceeds the gateway body limit")
            if rpc_id is not None and "text/event-stream" in response.headers.get("content-type", ""):
                text = content.decode("utf-8", errors="replace").replace("\r\n", "\n")
                for event in text.split("\n\n")[:-1]:
                    data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
                    if data:
                        message = json.loads(data)
                        if message.get("id") == rpc_id:
                            return message
        if response.status_code == 202 and rpc_id is None:
            return None
        return json.loads(content)


async def call_mcp_tool(
    client: httpx.AsyncClient,
    target: ToolRoute,
    arguments: dict[str, Any],
    headers: dict[str, str],
    limit: int,
) -> dict[str, Any]:
    headers.update({"Accept": "application/json, text/event-stream"})
    url = str(target.url)

    async def rpc(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        message = await read_tool_response(
            client,
            url,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers,
            limit,
            rpc_id=request_id,
        )
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or message.get("id") != request_id:
            raise ValueError("invalid MCP response identity")
        if "error" in message:
            raise HTTPException(
                502,
                detail={
                    "reason": "mcp_server_error",
                    "message": "MCP server rejected the request; inspect its logs with the Idempotency-Key.",
                },
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise ValueError("invalid MCP result")
        return result

    try:
        initialized = await rpc(
            1,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agentworkflows", "version": "0.3.0"},
            },
        )
        if initialized.get("protocolVersion") != PROTOCOL_VERSION or "tools" not in initialized.get("capabilities", {}):
            raise HTTPException(
                502,
                detail={
                    "reason": "mcp_protocol_unsupported",
                    "message": f"Configure an MCP Streamable HTTP server supporting tools and {PROTOCOL_VERSION}.",
                },
            )
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        await read_tool_response(client, url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, limit)
        result = await rpc(2, "tools/call", {"name": target.mcp_tool, "arguments": arguments})
        if result.get("isError"):
            raise HTTPException(
                502,
                detail={
                    "reason": "mcp_tool_failed",
                    "message": "MCP tool reported failure; inspect the server logs before retrying side effects.",
                },
            )
        if not isinstance(result.get("content"), list):
            raise ValueError("MCP tool result requires content")
        return result
    finally:
        if "Mcp-Session-Id" in headers:
            with contextlib.suppress(httpx.HTTPError):
                async with client.stream("DELETE", url, headers=headers):
                    pass
