"""Outbound webhooks: POST JSON to a fixed HTTPS endpoint, optionally signed.

The URL is part of the connection (set by an administrator), never a tool
argument, so agents and workflows can only reach endpoints someone approved.
With a signing secret, requests carry `X-Noblen-Timestamp` and
`X-Noblen-Signature: sha256=HMAC(secret, "<timestamp>.<body>")`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.integrations import net


class WebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2048)


class WebhookSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signing_secret: str | None = Field(default=None, max_length=512)
    bearer_token: str | None = Field(default=None, max_length=4096)


def signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


async def post(config: dict[str, Any], secret: dict[str, Any], payload: Any) -> dict[str, Any]:
    cfg, sec = WebhookConfig.model_validate(config), WebhookSecret.model_validate(secret)
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json"}
    if sec.signing_secret:
        timestamp = str(int(time.time()))
        headers["X-Noblen-Timestamp"] = timestamp
        headers["X-Noblen-Signature"] = signature(sec.signing_secret, timestamp, body)
    if sec.bearer_token:
        headers["Authorization"] = f"Bearer {sec.bearer_token}"
    response = await net.request("POST", cfg.url, content=body, headers=headers)
    result: dict[str, Any] = {"status_code": response.status_code, "ok": response.is_success}
    text = response.text[:4000]
    if text:
        result["body"] = text
    if not response.is_success:
        raise net.IntegrationError(f"The webhook returned HTTP {response.status_code}.")
    return result


async def test(config: dict[str, Any], secret: dict[str, Any]) -> None:
    await net.check_url(WebhookConfig.model_validate(config).url)
