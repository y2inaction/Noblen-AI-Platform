"""Calendars over CalDAV (RFC 4791): Google, iCloud, Fastmail, Nextcloud, Zoho...

The connection points at one calendar collection URL and authenticates with a
username and (app-specific) password. Reads use a `calendar-query` REPORT with
server-side expansion of recurring events; writes PUT a new iCalendar object
with `If-None-Match: *`, so an existing event is never overwritten.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.integrations import net

_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
MAX_RANGE = timedelta(days=62)


class CaldavConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_url: str = Field(min_length=8, max_length=2048)


class CaldavSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


def _auth(secret: dict[str, Any]) -> httpx.BasicAuth:
    sec = CaldavSecret.model_validate(secret)
    return httpx.BasicAuth(sec.username, sec.password)


def _url(config: dict[str, Any]) -> str:
    url = CaldavConfig.model_validate(config).calendar_url
    return url if url.endswith("/") else url + "/"


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _check(response: httpx.Response, expected: set[int]) -> None:
    if response.status_code == 401:
        raise net.IntegrationError("The calendar server rejected the credentials.")
    if response.status_code == 404:
        raise net.IntegrationError("The calendar URL was not found.")
    if response.status_code not in expected:
        raise net.IntegrationError(f"The calendar server returned HTTP {response.status_code}.")


# ---- iCalendar (RFC 5545) — the small subset calendars exchange for events -- #
def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold at 75 octets without splitting a UTF-8 character."""
    encoded, parts = line.encode(), list[str]()
    while len(encoded) > 75:
        cut = 75 if not parts else 74
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode())
        encoded = encoded[cut:]
    parts.append(encoded.decode())
    return "\r\n ".join(parts)


def _parse_when(params: dict[str, str], value: str) -> tuple[str, bool]:
    """(ISO 8601 string, all_day)."""
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat(), True
    utc = value.endswith("Z")
    moment = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if utc:
        return moment.replace(tzinfo=UTC).isoformat(), False
    tzid = params.get("TZID")
    if tzid:
        try:
            return moment.replace(tzinfo=ZoneInfo(tzid)).isoformat(), False
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return moment.isoformat(), False  # floating time


def parse_events(ical: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    depth = 0
    for line in _unfold(ical):
        name_part, _, value = line.partition(":")
        name, *raw_params = name_part.split(";")
        name = name.upper()
        params = {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in raw_params)}
        if name == "BEGIN":
            if value.upper() == "VEVENT" and depth == 0:
                current = {}
            elif current is not None:
                depth += 1  # e.g. VALARM inside the event
            continue
        if name == "END":
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.upper() == "VEVENT":
                events.append(current)
                current = None
            continue
        if current is None or depth:
            continue
        if name in ("DTSTART", "DTEND"):
            when, all_day = _parse_when(params, value)
            current["start" if name == "DTSTART" else "end"] = when
            current["all_day"] = all_day
        elif name == "SUMMARY":
            current["title"] = _unescape(value)
        elif name == "LOCATION":
            current["location"] = _unescape(value)
        elif name == "DESCRIPTION":
            current["description"] = _unescape(value)[:2000]
        elif name == "UID":
            current["uid"] = value
        elif name == "STATUS":
            current["status"] = value.upper()
    return events


def build_event(
    uid: str,
    title: str,
    start: datetime,
    end: datetime,
    description: str | None,
    location: str | None,
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Noblen AI//Integrations//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(datetime.now(UTC))}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_escape(title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# ---- operations --------------------------------------------------------- #
async def list_events(
    config: dict[str, Any], secret: dict[str, Any], start: datetime, end: datetime, limit: int = 50
) -> list[dict[str, Any]]:
    if end <= start:
        raise net.IntegrationError("The end must be after the start.")
    if end - start > MAX_RANGE:
        raise net.IntegrationError("Ask for at most 62 days of events at a time.")
    s, e = _stamp(start), _stamp(end)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        f'<d:prop><d:getetag/><c:calendar-data><c:expand start="{s}" end="{e}"/>'
        "</c:calendar-data></d:prop>"
        '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
        f'<c:time-range start="{s}" end="{e}"/>'
        "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
    )
    response = await net.request(
        "REPORT",
        _url(config),
        content=body.encode(),
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        auth=_auth(secret),
    )
    _check(response, {207})
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise net.IntegrationError("The calendar server sent an unreadable response.") from exc
    events: list[dict[str, Any]] = []
    for data in root.iterfind(".//c:calendar-data", _NS):
        events.extend(parse_events(data.text or ""))
    events.sort(key=lambda ev: str(ev.get("start", "")))
    return events[:limit]


async def create_event(
    config: dict[str, Any],
    secret: dict[str, Any],
    *,
    title: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    if end <= start:
        raise net.IntegrationError("The end must be after the start.")
    uid = f"{uuid.uuid4()}@noblen.ai"
    response = await net.request(
        "PUT",
        _url(config) + uid.split("@")[0] + ".ics",
        content=build_event(uid, title, start, end, description, location).encode(),
        headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
        auth=_auth(secret),
    )
    _check(response, {200, 201, 204})
    return {"uid": uid, "title": title, "start": start.isoformat(), "end": end.isoformat()}


async def test(config: dict[str, Any], secret: dict[str, Any]) -> None:
    response = await net.request(
        "PROPFIND",
        _url(config),
        content=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
        b"</d:prop></d:propfind>",
        headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
        auth=_auth(secret),
    )
    _check(response, {207})
