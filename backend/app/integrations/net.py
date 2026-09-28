"""Outbound network access for integrations, with SSRF protection.

Every outbound connection (HTTP or SMTP) is checked first: the URL scheme must be
allowed, and the host must resolve only to public addresses. Loopback, private,
link-local (including cloud metadata), multicast and reserved ranges are refused
unless INTEGRATIONS_ALLOW_PRIVATE_NETWORKS is on (local development only).

Known limit: a hostname is resolved at check time and again by the client, so a
DNS-rebinding attacker could race the two. Redirects are never followed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.exceptions import AppError


class IntegrationError(AppError):
    """A client-safe failure talking to an external service."""

    status_code = 502
    error_code = "integration_error"


# Tests replace this with an httpx.MockTransport; production uses the default.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_host(host: str, port: int) -> None:
    if not host:
        raise IntegrationError("The address has no host.")
    if settings.INTEGRATIONS_ALLOW_PRIVATE_NETWORKS:
        return
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await resolve_host(host, port)
        except OSError as exc:
            raise IntegrationError(f"Could not resolve '{host}'.") from exc
    if not addresses or not all(_public(a) for a in addresses):
        raise IntegrationError(f"'{host}' is not a public address; refusing to connect.")


async def check_url(url: str, *, allow_http: bool = False) -> None:
    parts = urlsplit(url)
    schemes = (
        {"https", "http"}
        if allow_http or settings.INTEGRATIONS_ALLOW_PRIVATE_NETWORKS
        else {"https"}
    )
    if parts.scheme not in schemes:
        raise IntegrationError("Only https:// URLs are allowed.")
    if parts.username or parts.password:
        raise IntegrationError("Put credentials in the connection's secret, not the URL.")
    await check_host(parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_transport,
        timeout=settings.INTEGRATIONS_HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": "Noblen-AI/3.0 (+integrations)"},
    )


async def request(
    method: str, url: str, *, allow_http: bool = False, **kwargs: object
) -> httpx.Response:
    """A checked, bounded request. Raises IntegrationError on transport failures."""
    await check_url(url, allow_http=allow_http)
    try:
        async with client() as http:
            response = await http.request(method, url, **kwargs)  # type: ignore[arg-type]
    except httpx.TimeoutException as exc:
        raise IntegrationError("The service did not respond in time.") from exc
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Could not reach the service ({type(exc).__name__}).") from exc
    if len(response.content) > settings.INTEGRATIONS_MAX_RESPONSE_BYTES:
        raise IntegrationError("The service's response was too large.")
    return response
