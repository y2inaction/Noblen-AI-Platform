"""Integrations (M6): secrets, SSRF guard, iCalendar, signatures, MCP responses."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.integrations import net, secrets
from app.integrations.providers import caldav, mcp, webhook


@pytest.fixture(autouse=True)
def _fresh_keys():
    secrets._box.cache_clear()
    yield
    secrets._box.cache_clear()


def test_secrets_round_trip_and_rotate(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEYS", [old])
    token = secrets.encrypt({"password": "s3cret-value"})
    assert "s3cret" not in token
    # Rotation: a new first key encrypts, the old one still decrypts.
    secrets._box.cache_clear()
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEYS", [new, old])
    assert secrets.decrypt(token) == {"password": "s3cret-value"}
    # Without the old key, stored credentials cannot be read (and say so).
    secrets._box.cache_clear()
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEYS", [new])
    with pytest.raises(secrets.EncryptionUnavailable):
        secrets.decrypt(token)


def test_production_requires_an_explicit_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEYS", [])
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(secrets.EncryptionUnavailable):
        secrets.encrypt({"a": "b"})


def test_mask_never_reveals_short_secrets():
    assert secrets.mask("abc") == "••••"
    assert secrets.mask("a-long-api-token-1234") == "••••1234"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "192.168.0.10", "169.254.169.254", "::1", "fd00::1", "0.0.0.0"],
)
async def test_private_addresses_are_refused(monkeypatch, address):
    monkeypatch.setattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_NETWORKS", False)
    with pytest.raises(net.IntegrationError):
        await net.check_host(address, 443)


async def test_hostnames_resolving_to_private_addresses_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_NETWORKS", False)

    async def fake_resolve(host, port):
        return ["93.184.216.34", "10.0.0.7"]  # one bad answer is enough to refuse

    monkeypatch.setattr(net, "resolve_host", fake_resolve)
    with pytest.raises(net.IntegrationError):
        await net.check_url("https://hooks.example.com/x")


async def test_url_rules(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_NETWORKS", False)

    async def public(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr(net, "resolve_host", public)
    await net.check_url("https://hooks.example.com/x")
    for bad in ("http://hooks.example.com/x", "ftp://example.com", "https://user:pw@example.com/"):
        with pytest.raises(net.IntegrationError):
            await net.check_url(bad)


async def test_redirects_are_not_followed(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_NETWORKS", True)
    net.set_transport(
        httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "http://10.0.0.1/"}))
    )
    try:
        response = await net.request("GET", "https://example.com/start")
    finally:
        net.set_transport(None)
    assert response.status_code == 302


def test_webhook_signature_is_verifiable():
    sig = webhook.signature("shh", "1700000000", b'{"a":1}')
    assert sig.startswith("sha256=") and len(sig) == 7 + 64
    assert sig != webhook.signature("shh", "1700000001", b'{"a":1}')


_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\nUID:a1\r\nDTSTART;TZID=Africa/Lagos:20260928T090000\r\n"
    "DTEND;TZID=Africa/Lagos:20260928T100000\r\nSUMMARY:Board review\\, Q3\r\n"
    "DESCRIPTION:Line one\\nLine two with a long tail that the server folded ac\r\n ross lines\r\n"
    "BEGIN:VALARM\r\nSUMMARY:Alarm (ignored)\r\nEND:VALARM\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:a2\r\nDTSTART;VALUE=DATE:20260929\r\nDTEND;VALUE=DATE:20260930\r\n"
    "SUMMARY:Public holiday\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:a3\r\nDTSTART:20260928T150000Z\r\nDTEND:20260928T153000Z\r\n"
    "SUMMARY:Call\r\nLOCATION:Room 4\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_icalendar_parsing():
    first, holiday, call = caldav.parse_events(_ICS)
    assert first["title"] == "Board review, Q3"
    assert first["start"] == "2026-09-28T09:00:00+01:00"
    assert (
        first["description"]
        == "Line one\nLine two with a long tail that the server folded across lines"
    )
    assert (holiday["start"], holiday["all_day"]) == ("2026-09-29", True)
    assert (call["start"], call["location"]) == ("2026-09-28T15:00:00+00:00", "Room 4")


def test_icalendar_building_escapes_and_folds():
    start = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    text = caldav.build_event(
        "u1",
        "Budget; review, ₦ & more " * 4,
        start,
        start.replace(hour=9),
        "Agenda:\n1. Costs",
        None,
    )
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert "DTSTART:20260928T080000Z" in text
    [event] = caldav.parse_events(text)
    assert event["title"] == ("Budget; review, ₦ & more " * 4)
    assert event["description"] == "Agenda:\n1. Costs"


def test_mcp_reads_json_and_sse_responses():
    as_json = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert mcp._messages(as_json) == [{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}]
    sse = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        'data: {"jsonrpc":"2.0","id":2,"result":{"x":1}}\n\n',
    )
    assert [m.get("id") for m in mcp._messages(sse)] == [None, 2]
