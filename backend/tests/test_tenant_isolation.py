"""Edge case 1: Tenant A can't read or write Tenant B's data, even by guessing an id.

The centrepiece is `test_every_id_route_is_opaque_across_tenants`, which walks the
application's own route table rather than a hand-written list of URLs. Add an
endpoint tomorrow and it is covered by this test the moment it is registered --
which is the only way a sweep like this stays true.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from app.main import app

from .conftest import Actor

# Path parameters that carry a tenant-owned identifier.
ID_PARAMS = (
    "project_id", "task_id", "file_id", "membership_id", "client_id", "invite_id",
)


def _routes_with_ids() -> list[tuple[str, str]]:
    found = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not any(f"{{{p}}}" in path for p in ID_PARAMS):
            continue
        for method in methods & {"GET", "DELETE"}:  # safe to fire blind
            found.append((method, path))
    return sorted(set(found))


def test_route_sweep_is_not_empty() -> None:
    assert len(_routes_with_ids()) >= 5, "sweep would pass vacuously"


@pytest.mark.parametrize("method,path", _routes_with_ids())
def test_every_id_route_is_opaque_across_tenants(
    method: str, path: str, raj: Actor, northwind: dict
) -> None:
    """Raj (Bluepeak admin) aims every id-bearing route at Northwind's ids.

    404 everywhere -- never 403. A 403 would confirm the id is real and turn the
    endpoint into an oracle for enumerating another agency's data.
    """
    values = {
        "project_id": northwind["rebrand"]["id"],
        "task_id": northwind["tasks"][0]["id"],
        "file_id": (northwind["files"][0]["id"] if northwind["files"] else northwind["tasks"][0]["id"]),
        "membership_id": northwind["members"][0]["membership_id"],
        "client_id": northwind["clients"][0]["id"],
        "invite_id": northwind["tasks"][0]["id"],
    }
    url = path
    for name, value in values.items():
        url = url.replace(f"{{{name}}}", str(value))

    response = raj.get(url) if method == "GET" else raj.delete(url)
    assert response.status_code == 404, (
        f"{method} {path} returned {response.status_code} for a foreign id "
        f"({response.text[:200]})"
    )


def test_listing_endpoints_never_show_the_other_tenant(raj: Actor, ada: Actor) -> None:
    raj_projects = {p["name"] for p in raj.get("/api/projects").json()}
    ada_projects = {p["name"] for p in ada.get("/api/projects").json()}

    assert raj_projects == {"Lumen Patient App"}
    assert "Harbor Foods Rebrand" in ada_projects
    assert not (raj_projects & ada_projects)

    raj_clients = {c["name"] for c in raj.get("/api/clients").json()}
    assert raj_clients == {"Lumen Health"}


def test_search_does_not_cross_tenants(raj: Actor, ada: Actor) -> None:
    # Bluepeak's own internal task is findable by Bluepeak...
    mine = raj.get("/api/search", params={"q": "renewal risk"}).json()
    assert any("renewal" in hit["title"].lower() for hit in mine)

    # ...and invisible to Northwind, who is searching the same shared table.
    theirs = ada.get("/api/search", params={"q": "renewal risk"}).json()
    assert theirs == []

    # And the reverse direction.
    assert raj.get("/api/search", params={"q": "margin"}).json() == []


def test_cannot_write_into_another_tenant(raj: Actor, northwind: dict) -> None:
    task = northwind["tasks"][0]

    assert raj.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 404
    assert raj.post(
        f"/api/tasks/{task['id']}/comments", json={"body": "hello", "visibility": "internal"}
    ).status_code == 404
    assert raj.post(
        f"/api/projects/{northwind['rebrand']['id']}/tasks", json={"title": "injected"}
    ).status_code == 404
    assert raj.post(
        f"/api/tasks/{task['id']}/time-entries", json={"minutes": 30}
    ).status_code == 404


def test_cannot_borrow_another_tenants_client_id(raj: Actor, northwind: dict) -> None:
    """Even holding a genuine client id, Raj cannot hang a project off it.

    Two independent things stop him. The handler looks the client up first and
    sees nothing, because the clients policy already scoped the row away. And if
    that check were deleted, the composite foreign key
    projects(client_id, agency_id) -> clients(id, agency_id) would still reject
    the insert: his agency_id paired with her client_id is not a row that exists.
    """
    harbor = next(c for c in northwind["clients"] if c["name"] == "Harbor Foods")
    response = raj.post(
        "/api/projects", json={"client_id": harbor["id"], "name": "Smash and grab"}
    )
    assert response.status_code == 404


def test_a_member_only_sees_their_own_projects(ben: Actor, cleo: Actor) -> None:
    """Isolation is not only between agencies -- agency_member is scoped too.

    Ben and Cleo are colleagues at the same agency. Each is on one project and
    must be blind to the other's, which is the same policy machinery as
    cross-tenant isolation, just at a finer grain.
    """
    ben_projects = {p["name"] for p in ben.get("/api/projects").json()}
    cleo_projects = {p["name"] for p in cleo.get("/api/projects").json()}

    assert "Harbor Foods Rebrand" in ben_projects
    assert "Veldt Spring Catalog" not in ben_projects

    assert "Veldt Spring Catalog" in cleo_projects
    assert "Harbor Foods Rebrand" not in cleo_projects

    rebrand_id = next(
        p["id"] for p in ben.get("/api/projects").json() if p["name"] == "Harbor Foods Rebrand"
    )
    assert cleo.get(f"/api/projects/{rebrand_id}").status_code == 404
    assert cleo.get(f"/api/projects/{rebrand_id}/tasks").status_code == 404
    assert cleo.get("/api/search", params={"q": "moodboard"}).json() == []


def test_database_denies_everything_without_a_request_context() -> None:
    """The floor under the whole design.

    Connect as the application role, set no context at all, and ask for the
    tables directly. Zero rows -- not because a WHERE clause was remembered, but
    because every policy evaluates against a NULL agency. If someone later writes
    a query that forgets to filter, this is what catches it.
    """
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    try:
        with engine.connect() as conn:
            for table in ("agencies", "projects", "tasks", "comments", "files",
                          "time_entries", "clients", "invites"):
                count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                assert count == 0, f"{table} leaked {count} rows with no tenant context"
    finally:
        engine.dispose()


def test_application_role_cannot_disable_its_own_policies() -> None:
    """Belt and braces: the app role owns nothing, so it cannot turn RLS off."""
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    try:
        with engine.connect() as conn:
            with pytest.raises(Exception):
                conn.execute(text("ALTER TABLE tasks DISABLE ROW LEVEL SECURITY"))
    finally:
        engine.dispose()


def test_agency_staff_roster_is_tenant_scoped(ada: Actor, raj: Actor) -> None:
    """The roster feeds the 'add somebody to this project' picker, so it is a
    list of real people's names and addresses -- exactly the sort of endpoint
    that leaks quietly."""
    northwind = ada.get("/api/agency/staff").json()
    bluepeak = raj.get("/api/agency/staff").json()

    assert "ben@northwind.test" in {s["email"] for s in northwind}
    assert "sam@bluepeak.test" in {s["email"] for s in bluepeak}

    # Isolation is per *membership*, not per person. A human who works at both
    # agencies appears on both rosters, and that is correct -- what must never
    # be shared is the membership row, because that is what carries the tenant.
    assert not (
        {s["membership_id"] for s in northwind} & {s["membership_id"] for s in bluepeak}
    )

    # Nobody from the other agency's roster leaks in under a different membership.
    assert not any(s["email"].endswith("@bluepeak.test") for s in northwind)
    assert not any(s["email"].endswith("@northwind.test") for s in bluepeak)


def test_the_roster_never_includes_clients(ada: Actor) -> None:
    """Clients cannot be project members -- project_members.member_role has a
    CHECK constraint -- so offering one in the picker would only produce a 400."""
    roles = {s["role"] for s in ada.get("/api/agency/staff").json()}
    assert roles <= {"agency_admin", "agency_member"}
    assert "mia@harborfoods.test" not in {s["email"] for s in ada.get("/api/agency/staff").json()}


def test_a_client_cannot_enumerate_agency_personnel(mia_client: Actor) -> None:
    assert mia_client.get("/api/agency/staff").status_code == 403
