"""Knowledge access control within an organization (PostgreSQL + pgvector).

Noblen AI 3.0 M3: restricted knowledge bases and documents, user/role grants,
filtering *inside* the retrieval query, agents limited to their initiator's
access, and structured table queries under the same rules.
"""

from __future__ import annotations

import json
import uuid

from app.models.membership import OrganizationMember
from tests.conftest import auth_headers

_SALARIES = "Executive salary bands: CEO 120 million naira per year."
_HANDBOOK = "Office hours are 8am to 5pm, Monday to Friday."


async def _register(pg_client, email, org="Acme"):
    r = await pg_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "organization_name": org},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _join(pg_client, pg_sessions, admin, email, role) -> dict:
    """A new user who is `role` in the admin's organization (selected via header)."""
    auth = await _register(pg_client, email, f"Home {email}")
    async with pg_sessions() as s:
        s.add(
            OrganizationMember(
                user_id=uuid.UUID(auth["user"]["id"]),
                organization_id=uuid.UUID(admin["organization_id"]),
                role_name=role,
                status="ACTIVE",
            )
        )
        await s.commit()
    auth["_org"] = admin["organization_id"]
    return auth


def _h(auth) -> dict[str, str]:
    headers = auth_headers(auth)
    if auth.get("_org"):
        headers["X-Organization-Id"] = auth["_org"]
    return headers


async def _kb(pg_client, auth, name) -> str:
    r = await pg_client.post("/api/v1/knowledge-bases", headers=_h(auth), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _doc(pg_client, auth, kb_id, name, content) -> dict:
    r = await pg_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/text",
        headers=_h(auth),
        json={"name": name, "content": content},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _set_kb_access(pg_client, auth, kb_id, visibility, grants=()):
    return await pg_client.put(
        f"/api/v1/knowledge-bases/{kb_id}/access",
        headers=_h(auth),
        json={"visibility": visibility, "grants": list(grants)},
    )


async def _search(pg_client, auth, query="salary", **extra) -> list[str]:
    r = await pg_client.post(
        "/api/v1/knowledge/search", headers=_h(auth), json={"query": query, **extra}
    )
    assert r.status_code == 200, r.text
    return [x["document_name"] for x in r.json()["results"]]


async def test_restricted_knowledge_base_is_invisible_without_a_grant(pg_client, pg_sessions):
    admin = await _register(pg_client, "admin@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "m@acl.example.com", "MEMBER")
    hr = await _kb(pg_client, admin, "HR")
    await _doc(pg_client, admin, hr, "Salaries", _SALARIES)
    assert (await _set_kb_access(pg_client, admin, hr, "RESTRICTED")).status_code == 200

    # Not listed, not fetchable, not searchable — indistinguishable from absent.
    listing = (await pg_client.get("/api/v1/knowledge-bases", headers=_h(member))).json()
    assert listing["total"] == 0
    assert (
        await pg_client.get(f"/api/v1/knowledge-bases/{hr}", headers=_h(member))
    ).status_code == 404
    docs = await pg_client.get(f"/api/v1/knowledge-bases/{hr}/documents", headers=_h(member))
    assert docs.status_code == 404
    assert await _search(pg_client, member) == []
    # The creator (and org admins) still read it.
    assert await _search(pg_client, admin) == ["Salaries"]


async def test_user_and_role_grants_open_a_restricted_base(pg_client, pg_sessions):
    admin = await _register(pg_client, "admin2@acl.example.com")
    alice = await _join(pg_client, pg_sessions, admin, "alice@acl.example.com", "MEMBER")
    mgr = await _join(pg_client, pg_sessions, admin, "mgr@acl.example.com", "MANAGER")
    viewer = await _join(pg_client, pg_sessions, admin, "v@acl.example.com", "MEMBER")
    hr = await _kb(pg_client, admin, "HR")
    await _doc(pg_client, admin, hr, "Salaries", _SALARIES)
    r = await _set_kb_access(
        pg_client,
        admin,
        hr,
        "RESTRICTED",
        [
            {"principal_type": "USER", "principal": alice["user"]["id"]},
            {"principal_type": "ROLE", "principal": "MANAGER"},
        ],
    )
    assert r.status_code == 200, r.text
    assert {g["principal"] for g in r.json()["grants"]} == {alice["user"]["id"], "MANAGER"}

    assert await _search(pg_client, alice) == ["Salaries"]
    assert await _search(pg_client, mgr) == ["Salaries"]
    assert await _search(pg_client, viewer) == []

    # Revoking takes effect immediately.
    await _set_kb_access(pg_client, admin, hr, "RESTRICTED", [])
    assert await _search(pg_client, alice) == []
    # Opening it to the organization again restores Phase 4 behaviour.
    await _set_kb_access(pg_client, admin, hr, "ORGANIZATION")
    assert await _search(pg_client, viewer) == ["Salaries"]


async def test_restricted_document_inside_an_open_base(pg_client, pg_sessions):
    admin = await _register(pg_client, "admin3@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "m3@acl.example.com", "MEMBER")
    kb = await _kb(pg_client, admin, "Company")
    await _doc(pg_client, admin, kb, "Handbook", _HANDBOOK)
    secret = await _doc(pg_client, admin, kb, "Salaries", _SALARIES)
    r = await pg_client.put(
        f"/api/v1/documents/{secret['id']}/access",
        headers=_h(admin),
        json={"visibility": "RESTRICTED", "grants": []},
    )
    assert r.status_code == 200, r.text

    assert await _search(pg_client, member) == ["Handbook"]
    listed = (
        await pg_client.get(f"/api/v1/knowledge-bases/{kb}/documents", headers=_h(member))
    ).json()
    assert [d["name"] for d in listed["items"]] == ["Handbook"]
    missing = await pg_client.get(f"/api/v1/documents/{secret['id']}", headers=_h(member))
    assert missing.status_code == 404
    # Re-uploading identical bytes must not reveal the restricted document, even
    # to a manager who may ingest into the base but was not granted the document.
    manager = await _join(pg_client, pg_sessions, admin, "mg3@acl.example.com", "MANAGER")
    dup = await pg_client.post(
        f"/api/v1/knowledge-bases/{kb}/documents/text",
        headers=_h(manager),
        json={"name": "Probe", "content": _SALARIES},
    )
    assert dup.status_code == 409
    assert secret["id"] not in dup.text


async def test_access_filter_runs_before_ranking_and_limit(pg_client, pg_sessions):
    """Restricted chunks must never occupy the top-k slots of an unauthorized reader."""
    admin = await _register(pg_client, "admin4@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "m4@acl.example.com", "MEMBER")
    kb = await _kb(pg_client, admin, "Mixed")
    for i in range(6):
        doc = await _doc(pg_client, admin, kb, f"Secret {i}", f"{_SALARIES} Version {i}.")
        await pg_client.put(
            f"/api/v1/documents/{doc['id']}/access",
            headers=_h(admin),
            json={"visibility": "RESTRICTED", "grants": []},
        )
    await _doc(pg_client, admin, kb, "Handbook", _HANDBOOK)
    assert await _search(pg_client, member, top_k=1) == ["Handbook"]


async def test_managing_access_is_permissioned_and_validated(pg_client, pg_sessions):
    admin = await _register(pg_client, "admin5@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "m5@acl.example.com", "MEMBER")
    manager = await _join(pg_client, pg_sessions, admin, "mg5@acl.example.com", "MANAGER")
    outsider = await _register(pg_client, "x@elsewhere.example.com", "Elsewhere")
    kb = await _kb(pg_client, admin, "Board")

    assert (await _set_kb_access(pg_client, member, kb, "RESTRICTED")).status_code == 403
    bad_role = await _set_kb_access(
        pg_client, admin, kb, "RESTRICTED", [{"principal_type": "ROLE", "principal": "KING"}]
    )
    assert bad_role.status_code == 422
    stranger = await _set_kb_access(
        pg_client,
        admin,
        kb,
        "RESTRICTED",
        [{"principal_type": "USER", "principal": outsider["user"]["id"]}],
    )
    assert stranger.status_code == 422

    # A manager can manage access only for bases they can read.
    await _set_kb_access(pg_client, admin, kb, "RESTRICTED")
    assert (await _set_kb_access(pg_client, manager, kb, "ORGANIZATION")).status_code == 404
    # ...and cannot give an agent knowledge they cannot read.
    agent = await pg_client.post("/api/v1/agents", headers=_h(manager), json={"name": "Bot"})
    attach = await pg_client.post(
        f"/api/v1/agents/{agent.json()['id']}/knowledge-bases",
        headers=_h(manager),
        json={"knowledge_base_id": kb},
    )
    assert attach.status_code == 404


async def test_other_organizations_can_never_be_granted_or_see_anything(pg_client):
    a = await _register(pg_client, "a@orga.example.com", "Org A")
    b = await _register(pg_client, "b@orgb.example.com", "Org B")
    kb = await _kb(pg_client, a, "Org A docs")
    await _doc(pg_client, a, kb, "Salaries", _SALARIES)
    assert (await _set_kb_access(pg_client, b, kb, "ORGANIZATION")).status_code == 404
    assert await _search(pg_client, b) == []


async def test_archived_knowledge_bases_are_not_searchable(pg_client):
    admin = await _register(pg_client, "arch@acl.example.com")
    kb = await _kb(pg_client, admin, "Old")
    await _doc(pg_client, admin, kb, "Old policy", _HANDBOOK)
    assert await _search(pg_client, admin, "hours") == ["Old policy"]
    await pg_client.delete(f"/api/v1/knowledge-bases/{kb}", headers=_h(admin))
    assert await _search(pg_client, admin, "hours") == []


# --------------------------------------------------------------------------- #
# Agents read with their initiator's access
# --------------------------------------------------------------------------- #
async def _agent(pg_client, admin, kb_id) -> str:
    agent_id = (
        await pg_client.post("/api/v1/agents", headers=_h(admin), json={"name": "Researcher"})
    ).json()["id"]
    tools = (await pg_client.get("/api/v1/tools", headers=_h(admin))).json()["items"]
    for name in ("search_knowledge", "list_data_tables", "query_data_table"):
        tid = next(t["id"] for t in tools if t["name"] == name)
        await pg_client.post(
            f"/api/v1/agents/{agent_id}/tools", headers=_h(admin), json={"tool_id": tid}
        )
    r = await pg_client.post(
        f"/api/v1/agents/{agent_id}/knowledge-bases",
        headers=_h(admin),
        json={"knowledge_base_id": kb_id},
    )
    assert r.status_code == 201, r.text
    ver = await pg_client.post(f"/api/v1/agents/{agent_id}/versions", headers=_h(admin), json={})
    await pg_client.post(
        f"/api/v1/agents/{agent_id}/versions/{ver.json()['id']}/activate", headers=_h(admin)
    )
    return agent_id


async def _agent_tool_result(pg_client, auth, agent_id, tool, args) -> dict:
    directive = f"go [[tool:{tool}|{json.dumps(args)}]]"
    r = await pg_client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(auth), json={"message": directive}
    )
    assert r.status_code == 200, r.text
    msgs = (
        await pg_client.get(
            f"/api/v1/conversations/{r.json()['conversation_id']}/messages", headers=_h(auth)
        )
    ).json()
    return json.loads(next(m for m in msgs if m["role"] == "tool")["content"])


async def test_agent_search_is_limited_to_what_its_initiator_may_read(pg_client, pg_sessions):
    admin = await _register(pg_client, "admin6@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "m6@acl.example.com", "MEMBER")
    kb = await _kb(pg_client, admin, "Company")
    await _doc(pg_client, admin, kb, "Handbook", _HANDBOOK)
    secret = await _doc(pg_client, admin, kb, "Salaries", _SALARIES)
    await pg_client.put(
        f"/api/v1/documents/{secret['id']}/access",
        headers=_h(admin),
        json={"visibility": "RESTRICTED", "grants": []},
    )
    agent_id = await _agent(pg_client, admin, kb)

    as_admin = await _agent_tool_result(
        pg_client, admin, agent_id, "search_knowledge", {"query": "salary"}
    )
    as_member = await _agent_tool_result(
        pg_client, member, agent_id, "search_knowledge", {"query": "salary"}
    )
    assert {r["document_name"] for r in as_admin["results"]} == {"Handbook", "Salaries"}
    assert {r["document_name"] for r in as_member["results"]} == {"Handbook"}
    assert "120 million" not in json.dumps(as_member)


# --------------------------------------------------------------------------- #
# Structured retrieval on PostgreSQL (JSON path SQL + access rules)
# --------------------------------------------------------------------------- #
_ORDERS = (
    "order_id,state,amount,status\n"
    "1,Kano,15000,paid\n2,Lagos,40000,paid\n3,Kano,5000,refunded\n"
    "4,Abuja,22000,paid\n5,Kano,1,000,paid\n"
)


async def _upload_csv(pg_client, auth, kb_id, name, content) -> dict:
    r = await pg_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/upload",
        headers=_h(auth),
        files={"file": (name, content.encode(), "text/csv")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "READY", r.json()
    return r.json()


async def test_table_queries_run_in_postgres_under_access_rules(pg_client, pg_sessions):
    admin = await _register(pg_client, "tbl@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "tm@acl.example.com", "MEMBER")
    kb = await _kb(pg_client, admin, "Sales data")
    clean = _ORDERS.replace("5,Kano,1,000,paid", '5,Kano,"1,000",paid')
    doc = await _upload_csv(pg_client, admin, kb, "orders.csv", clean)
    tables = (
        await pg_client.get(f"/api/v1/knowledge/tables?document_id={doc['id']}", headers=_h(admin))
    ).json()["items"]
    assert len(tables) == 1
    table = tables[0]
    types = {c["name"]: c["type"] for c in table["columns"]}
    assert types == {"order_id": "number", "state": "text", "amount": "number", "status": "text"}
    url = f"/api/v1/knowledge/tables/{table['table_id']}/query"

    total = await pg_client.post(
        url,
        headers=_h(admin),
        json={
            "filters": [
                {"column": "state", "op": "eq", "value": "Kano"},
                {"column": "status", "op": "eq", "value": "paid"},
            ],
            "aggregate": {"op": "sum", "column": "amount"},
        },
    )
    assert total.status_code == 200, total.text
    assert total.json()["rows"] == [{"sum_amount": 16000}]

    grouped = await pg_client.post(
        url,
        headers=_h(admin),
        json={"aggregate": {"op": "count"}, "group_by": "state", "limit": 2},
    )
    assert grouped.json()["rows"][0] == {"state": "Kano", "count": 3}
    assert grouped.json()["truncated"] is True

    big = await pg_client.post(
        url,
        headers=_h(admin),
        json={
            "filters": [{"column": "amount", "op": "gte", "value": 20000}],
            "columns": ["order_id", "amount"],
            "order_by": {"column": "amount", "descending": True},
        },
    )
    assert big.json()["rows"] == [
        {"order_id": 2, "amount": 40000},
        {"order_id": 4, "amount": 22000},
    ]

    # Restricting the document hides its table from the member.
    await pg_client.put(
        f"/api/v1/documents/{doc['id']}/access",
        headers=_h(admin),
        json={"visibility": "RESTRICTED", "grants": []},
    )
    listed = (await pg_client.get("/api/v1/knowledge/tables", headers=_h(member))).json()
    assert listed["total"] == 0
    denied = await pg_client.post(url, headers=_h(member), json={"aggregate": {"op": "count"}})
    assert denied.status_code == 404


async def test_agent_answers_counts_from_tables_it_may_read(pg_client, pg_sessions):
    admin = await _register(pg_client, "tagent@acl.example.com")
    kb = await _kb(pg_client, admin, "Ops data")
    doc = await _upload_csv(pg_client, admin, kb, "orders.csv", _ORDERS.replace("1,000", "1000"))
    table_id = (
        await pg_client.get(f"/api/v1/knowledge/tables?document_id={doc['id']}", headers=_h(admin))
    ).json()["items"][0]["table_id"]
    agent_id = await _agent(pg_client, admin, kb)

    listed = await _agent_tool_result(pg_client, admin, agent_id, "list_data_tables", {})
    assert [t["table_id"] for t in listed["tables"]] == [table_id]
    answer = await _agent_tool_result(
        pg_client,
        admin,
        agent_id,
        "query_data_table",
        {
            "table_id": table_id,
            "aggregate": {"op": "count"},
            "filters": [{"column": "status", "op": "eq", "value": "refunded"}],
        },
    )
    assert answer["rows"] == [{"count": 1}]
