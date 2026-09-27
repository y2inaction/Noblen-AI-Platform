"""Email over SMTP (any provider: Google Workspace, Microsoft 365, Zoho, Mailgun...)."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.config import settings
from app.integrations.net import IntegrationError, check_host


class SmtpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    from_email: EmailStr
    from_name: str | None = Field(default=None, max_length=120)


class SmtpSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=1024)


def _send(config: SmtpConfig, secret: SmtpSecret, message: EmailMessage) -> None:
    timeout = settings.INTEGRATIONS_HTTP_TIMEOUT_SECONDS
    context = ssl.create_default_context()
    server: smtplib.SMTP
    if config.security == "ssl":
        server = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=timeout)
    with server:
        if config.security == "starttls":
            server.starttls(context=context)
        if secret.username:
            server.login(secret.username, secret.password or "")
        server.send_message(message)


async def send(
    config: dict[str, Any],
    secret: dict[str, Any],
    *,
    to: list[str],
    subject: str,
    body: str,
    reply_to: str | None = None,
) -> dict[str, Any]:
    cfg, sec = SmtpConfig.model_validate(config), SmtpSecret.model_validate(secret)
    if cfg.security == "none" and not settings.INTEGRATIONS_ALLOW_PRIVATE_NETWORKS:
        raise IntegrationError("Unencrypted SMTP is only allowed in local development.")
    await check_host(cfg.host, cfg.port)
    message = EmailMessage()
    message["From"] = formataddr((cfg.from_name or "", str(cfg.from_email)))
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=str(cfg.from_email).rsplit("@", 1)[-1])
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    try:
        await asyncio.to_thread(_send, cfg, sec, message)
    except smtplib.SMTPAuthenticationError as exc:
        raise IntegrationError("The mail server rejected the credentials.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise IntegrationError("The mail server refused the recipients.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise IntegrationError(f"Could not send the email ({type(exc).__name__}).") from exc
    return {"sent": True, "to": to, "message_id": message["Message-ID"]}


async def test(config: dict[str, Any], secret: dict[str, Any]) -> None:
    """Connect (and log in) without sending anything."""
    cfg, sec = SmtpConfig.model_validate(config), SmtpSecret.model_validate(secret)
    await check_host(cfg.host, cfg.port)

    def _probe() -> None:
        timeout = settings.INTEGRATIONS_HTTP_TIMEOUT_SECONDS
        context = ssl.create_default_context()
        server = (
            smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout, context=context)
            if cfg.security == "ssl"
            else smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
        )
        with server:
            if cfg.security == "starttls":
                server.starttls(context=context)
            if sec.username:
                server.login(sec.username, sec.password or "")
            server.noop()

    try:
        await asyncio.to_thread(_probe)
    except smtplib.SMTPAuthenticationError as exc:
        raise IntegrationError("The mail server rejected the credentials.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise IntegrationError(
            f"Could not connect to the mail server ({type(exc).__name__})."
        ) from exc
