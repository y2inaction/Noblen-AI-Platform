"""Remote MCP servers over the Streamable HTTP transport (MCP 2025-06-18).

Each operation opens a short session: `initialize`, `notifications/initialized`,
the request (`tools/list` or `tools/call`), then a best-effort session DELETE.
Responses may be plain JSON or an SSE stream; both are handled. The server URL
is checked by the SSRF guard, redirects are not followed, and results are
bounded and returned as untrusted data.
"""

from __future__ import annotations

import contextlib
import itertools
import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.integrations import net

PROTOCOL_VERSION = "2025-06-18"
MAX_TOOLS = 500
MAX_RESULT_CHARS = 20_000


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2048)


class McpSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bearer_token: str | None = Field(default=None, max_length=4096)


def _messages(response: httpx.Response) -> list[dict[str, Any]]:
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind == "text/event-stream":
        found: list[dict[str, Any]] = []
        for block in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in block.split("\n") if line.startswith("data:")
            )
            if data:
                try:
                    found.append(json.loads(data))
                except json.JSONDecodeError:
                    continue
        return found
    try:
        body = response.json()
    except ValueError as exc:
        raise net.IntegrationError("The MCP server sent an unreadable response.") from exc
    return body if isinstance(body, list) else [body]


class McpSession:
    def __init__(self, config: dict[str, Any], secret: dict[str, Any]) -> None:
        self._url = McpConfig.model_validate(config).url
        token = McpSecret.model_validate(secret).bearer_token
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._ids = itertools.count(1)
        self._http: httpx.AsyncClient | None = None
        self.server_info: dict[str, Any] = {}

    async def __aenter__(self) -> McpSession:
        await net.check_url(self._url)
        self._http = net.client()
        try:
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "noblen-ai", "version": "3.0"},
                },
            )
            self.server_info = dict(result.get("serverInfo") or {})
            self._headers["MCP-Protocol-Version"] = str(
                result.get("protocolVersion") or PROTOCOL_VERSION
            )
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            await self._http.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._http is not None
        if "Mcp-Session-Id" in self._headers:
            with contextlib.suppress(httpx.HTTPError):
                await self._http.delete(self._url, headers=self._headers)
        await self._http.aclose()

    async def _post(self, message: dict[str, Any]) -> httpx.Response:
        assert self._http is not None
        try:
            response = await self._http.post(self._url, json=message, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise net.IntegrationError("The MCP server did not respond in time.") from exc
        except httpx.HTTPError as exc:
            raise net.IntegrationError(
                f"Could not reach the MCP server ({type(exc).__name__})."
            ) from exc
        if response.status_code in (401, 403):
            raise net.IntegrationError("The MCP server rejected the credentials.")
        if response.status_code >= 400:
            raise net.IntegrationError(f"The MCP server returned HTTP {response.status_code}.")
        if len(response.content) > settings.INTEGRATIONS_MAX_RESPONSE_BYTES:
            raise net.IntegrationError("The MCP server's response was too large.")
        if session := response.headers.get("mcp-session-id"):
            self._headers["Mcp-Session-Id"] = session
        return response

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = next(self._ids)
        response = await self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        for message in _messages(response):
            if message.get("id") != request_id:
                continue  # notifications or server requests we do not serve
            if "error" in message:
                error = message["error"] or {}
                raise net.IntegrationError(
                    f"MCP error {error.get('code')}: {str(error.get('message', ''))[:200]}"
                )
            return dict(message.get("result") or {})
        raise net.IntegrationError(f"The MCP server did not answer '{method}'.")

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(tools) < MAX_TOOLS:
            result = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(
                t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")
            )
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools[:MAX_TOOLS]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        texts = [
            str(c.get("text", ""))
            for c in result.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        other = [
            c.get("type")
            for c in result.get("content", [])
            if isinstance(c, dict) and c.get("type") != "text"
        ]
        output: dict[str, Any] = {"is_error": bool(result.get("isError"))}
        text = "\n".join(texts)
        if text:
            output["text"] = text[:MAX_RESULT_CHARS]
            if len(text) > MAX_RESULT_CHARS:
                output["truncated"] = True
        if isinstance(result.get("structuredContent"), dict):
            structured = json.dumps(result["structuredContent"], default=str)
            if len(structured) <= MAX_RESULT_CHARS:
                output["structured"] = result["structuredContent"]
        if other:
            output["omitted_content_types"] = sorted({str(o) for o in other})
        return output


async def list_tools(config: dict[str, Any], secret: dict[str, Any]) -> list[dict[str, Any]]:
    async with McpSession(config, secret) as session:
        return await session.list_tools()


async def call_tool(
    config: dict[str, Any], secret: dict[str, Any], name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    async with McpSession(config, secret) as session:
        return await session.call_tool(name, arguments)


async def test(config: dict[str, Any], secret: dict[str, Any]) -> None:
    async with McpSession(config, secret):
        pass
