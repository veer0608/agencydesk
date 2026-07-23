"""Test harness.

Everything runs against a real PostgreSQL with the real policies enabled and the
API connecting as the real unprivileged role. Mocking the database here would
mock away the entire subject of the exercise.
"""

from __future__ import annotations

import os

# Repoint at the test database BEFORE any app module is imported: app.db builds
# its engine at import time from these variables.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://agencydesk_app:app_pw@localhost:5433/agencydesk_test",
)
os.environ["OWNER_DATABASE_URL"] = os.environ.get(
    "TEST_OWNER_DATABASE_URL",
    "postgresql+psycopg://agencydesk_owner:owner_pw@localhost:5433/agencydesk_test",
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import PASSWORD, seed  # noqa: E402

TABLES = [
    "audit_log", "invites", "time_entries", "files", "comments", "tasks",
    "project_members", "projects", "memberships", "clients", "users", "agencies",
]


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    owner = create_engine(os.environ["OWNER_DATABASE_URL"], future=True)
    with owner.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    owner.dispose()
    seed()


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


class Actor:
    """A logged-in session, pinned to exactly one membership."""

    def __init__(self, client: TestClient, token: str, me: dict) -> None:
        self._client = client
        self.token = token
        self.me = me

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url: str, **kw):
        return self._client.get(url, headers=self.headers, **kw)

    def post(self, url: str, **kw):
        return self._client.post(url, headers=self.headers, **kw)

    def patch(self, url: str, **kw):
        return self._client.patch(url, headers=self.headers, **kw)

    def delete(self, url: str, **kw):
        return self._client.delete(url, headers=self.headers, **kw)


def sign_in(client: TestClient, email: str, agency_name: str | None = None) -> Actor:
    """Log in and, when the person belongs to several agencies, choose one."""
    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    body = login.json()

    token = body["access_token"]
    if token is None:
        assert agency_name, f"{email} belongs to several agencies; specify which"
        match = next(m for m in body["memberships"] if m["agency_name"] == agency_name)
        chosen = client.post(
            "/api/auth/select-agency",
            json={"email": email, "password": PASSWORD, "membership_id": match["membership_id"]},
        )
        assert chosen.status_code == 200, chosen.text
        token = chosen.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    return Actor(client, token, me.json())


# --- the cast ---------------------------------------------------------------


@pytest.fixture(scope="session")
def ada(client: TestClient) -> Actor:
    """Northwind admin."""
    return sign_in(client, "ada@northwind.test")


@pytest.fixture(scope="session")
def ben(client: TestClient) -> Actor:
    """Northwind member, on the Rebrand project only."""
    return sign_in(client, "ben@northwind.test")


@pytest.fixture(scope="session")
def cleo(client: TestClient) -> Actor:
    """Northwind member, on the Catalog project only."""
    return sign_in(client, "cleo@northwind.test")


@pytest.fixture(scope="session")
def mia_client(client: TestClient) -> Actor:
    """Mia wearing her Harbor Foods client hat at Northwind."""
    return sign_in(client, "mia@harborfoods.test", "Northwind Studio")


@pytest.fixture(scope="session")
def mia_admin(client: TestClient) -> Actor:
    """The same human, wearing her admin hat at Bluepeak."""
    return sign_in(client, "mia@harborfoods.test", "Bluepeak Digital")


@pytest.fixture(scope="session")
def raj(client: TestClient) -> Actor:
    """Bluepeak admin -- the other tenant."""
    return sign_in(client, "raj@bluepeak.test")


# --- handy lookups ----------------------------------------------------------


@pytest.fixture(scope="session")
def northwind(ada: Actor) -> dict:
    """Ids from tenant A, gathered with full admin sight."""
    projects = ada.get("/api/projects").json()
    rebrand = next(p for p in projects if p["name"] == "Harbor Foods Rebrand")
    catalog = next(p for p in projects if p["name"] == "Veldt Spring Catalog")

    tasks = ada.get(f"/api/projects/{rebrand['id']}/tasks").json()
    internal = [t for t in tasks if t["visibility"] == "internal"]
    shared = [t for t in tasks if t["visibility"] == "client"]

    files: list[dict] = []
    for task in tasks:
        files.extend(ada.get(f"/api/tasks/{task['id']}/files").json())

    return {
        "rebrand": rebrand,
        "catalog": catalog,
        "tasks": tasks,
        "internal_tasks": internal,
        "client_tasks": shared,
        "files": files,
        "clients": ada.get("/api/clients").json(),
        "members": ada.get(f"/api/projects/{rebrand['id']}/members").json(),
    }
