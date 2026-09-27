"""HubSpot CRM (REST API v3) with a private-app access token.

Scopes needed on the private app: crm.objects.contacts.read,
crm.objects.contacts.write (notes use the contacts association).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.integrations import net

CONTACT_PROPERTIES = ["email", "firstname", "lastname", "phone", "company"]
# HubSpot-defined association type: note → contact.
NOTE_TO_CONTACT = 202


class HubspotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HubspotSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=8, max_length=4096)


async def _call(secret: dict[str, Any], method: str, path: str, json: Any = None) -> dict[str, Any]:
    token = HubspotSecret.model_validate(secret).access_token
    response = await net.request(
        method,
        settings.HUBSPOT_API_BASE.rstrip("/") + path,
        json=json,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code == 401:
        raise net.IntegrationError("HubSpot rejected the access token.")
    if response.status_code == 403:
        raise net.IntegrationError("The HubSpot app lacks the required scopes.")
    if response.status_code == 429:
        raise net.IntegrationError("HubSpot rate limit reached; try again shortly.")
    if not response.is_success:
        raise net.IntegrationError(f"HubSpot returned HTTP {response.status_code}.")
    return dict(response.json()) if response.content else {}


def _contact(item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    return {"id": str(item.get("id")), **{p: props.get(p) for p in CONTACT_PROPERTIES}}


async def search_contacts(
    secret: dict[str, Any], query: str, limit: int = 10
) -> list[dict[str, Any]]:
    body = {"query": query, "limit": max(1, min(limit, 25)), "properties": CONTACT_PROPERTIES}
    data = await _call(secret, "POST", "/crm/v3/objects/contacts/search", body)
    return [_contact(item) for item in data.get("results", [])]


async def _find_by_email(secret: dict[str, Any], email: str) -> dict[str, Any] | None:
    body = {
        "filterGroups": [
            {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
        ],
        "properties": CONTACT_PROPERTIES,
        "limit": 1,
    }
    data = await _call(secret, "POST", "/crm/v3/objects/contacts/search", body)
    results = data.get("results", [])
    return _contact(results[0]) if results else None


async def upsert_contact(secret: dict[str, Any], properties: dict[str, str]) -> dict[str, Any]:
    email = properties["email"].strip().lower()
    fields = {k: v for k, v in {**properties, "email": email}.items() if v not in (None, "")}
    existing = await _find_by_email(secret, email)
    if existing is None:
        created = await _call(secret, "POST", "/crm/v3/objects/contacts", {"properties": fields})
        return {"created": True, "contact": _contact(created)}
    updated = await _call(
        secret, "PATCH", f"/crm/v3/objects/contacts/{existing['id']}", {"properties": fields}
    )
    return {"created": False, "contact": _contact(updated)}


async def add_note(secret: dict[str, Any], email: str, note: str, timestamp: str) -> dict[str, Any]:
    contact = await _find_by_email(secret, email.strip().lower())
    if contact is None:
        raise net.IntegrationError(f"No HubSpot contact has the email {email}.")
    body = {
        "properties": {"hs_note_body": note, "hs_timestamp": timestamp},
        "associations": [
            {
                "to": {"id": contact["id"]},
                "types": [
                    {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_CONTACT}
                ],
            }
        ],
    }
    created = await _call(secret, "POST", "/crm/v3/objects/notes", body)
    return {"note_id": str(created.get("id")), "contact_id": contact["id"]}


async def test(config: dict[str, Any], secret: dict[str, Any]) -> None:
    await _call(secret, "GET", "/crm/v3/objects/contacts?limit=1")
