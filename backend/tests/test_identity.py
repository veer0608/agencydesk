"""Edge case 3: one person, two agencies, different roles in each.

Mia Halvorsen is a client contact at Northwind Studio and an agency admin at
Bluepeak Digital, under one email address. She is a single row in `users` with
two rows in `memberships`.
"""

from __future__ import annotations

from app.seed import PASSWORD

from .conftest import Actor


def test_one_email_two_agencies_no_default_tenant(client) -> None:
    """Login answers "who are you", not "which tenant are you".

    Because there is no correct single answer for Mia, /login returns both
    options and no session token. A design that guessed here would silently pick
    a tenant for her -- and eventually pick the wrong one.
    """
    response = client.post(
        "/api/auth/login", json={"email": "mia@harborfoods.test", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["access_token"] is None, "must not auto-select a tenant"
    assert len(body["memberships"]) == 2

    by_agency = {m["agency_name"]: m for m in body["memberships"]}
    assert by_agency["Northwind Studio"]["role"] == "client_user"
    assert by_agency["Northwind Studio"]["client_name"] == "Harbor Foods"
    assert by_agency["Bluepeak Digital"]["role"] == "agency_admin"
    assert by_agency["Bluepeak Digital"]["client_name"] is None


def test_single_agency_users_skip_the_picker(client) -> None:
    response = client.post(
        "/api/auth/login", json={"email": "ada@northwind.test", "password": PASSWORD}
    )
    body = response.json()
    assert len(body["memberships"]) == 1
    assert body["access_token"] is not None


def test_each_hat_is_a_separate_session(mia_client: Actor, mia_admin: Actor) -> None:
    assert mia_client.me["user_id"] == mia_admin.me["user_id"], "same human"
    assert mia_client.me["membership_id"] != mia_admin.me["membership_id"]
    assert mia_client.me["agency_id"] != mia_admin.me["agency_id"]
    assert mia_client.me["role"] == "client_user"
    assert mia_admin.me["role"] == "agency_admin"


def test_a_session_never_spans_both_agencies(mia_client: Actor, mia_admin: Actor) -> None:
    """The important half of the model: holding two memberships must not add up
    to seeing two tenants at once."""
    as_client = {p["name"] for p in mia_client.get("/api/projects").json()}
    as_admin = {p["name"] for p in mia_admin.get("/api/projects").json()}

    assert "Harbor Foods Rebrand" in as_client
    assert "Lumen Patient App" in as_admin
    # The point is not what each hat contains, but that the two never overlap.
    assert not (as_client & as_admin)

    # Her admin rights at Bluepeak grant her nothing at Northwind.
    northwind_project = mia_client.get("/api/projects").json()[0]["id"]
    assert mia_admin.get(f"/api/projects/{northwind_project}").status_code == 404
    assert mia_admin.post(
        f"/api/projects/{northwind_project}/tasks", json={"title": "elevated"}
    ).status_code == 404

    # And her client status at Northwind does not follow her to Bluepeak, where
    # she is staff and can create work.
    lumen = mia_admin.get("/api/projects").json()[0]["id"]
    created = mia_admin.post(f"/api/projects/{lumen}/tasks", json={"title": "Admin-created"})
    assert created.status_code == 201


def test_role_is_read_from_the_database_not_the_token(mia_client: Actor) -> None:
    """The session token carries only `sub` and `mid` -- no role, no agency id.

    Anything that governs access is re-read on every request, so a stolen or
    stale token cannot outlive a change of rights.
    """
    import jwt

    from app.config import settings

    claims = jwt.decode(mia_client.token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert set(claims) == {"sub", "mid", "iat", "exp"}
    assert "role" not in claims and "agency_id" not in claims


def test_cannot_borrow_someone_elses_membership(client, ada: Actor, mia_client: Actor) -> None:
    """Swapping the membership id into another person's login is rejected twice:
    by the WHERE clause, and independently by the memberships policy."""
    response = client.post(
        "/api/auth/select-agency",
        json={
            "email": "ada@northwind.test",
            "password": PASSWORD,
            "membership_id": mia_client.me["membership_id"],
        },
    )
    assert response.status_code == 401


def test_switching_agencies_mints_a_new_session(client, mia_client: Actor) -> None:
    """In-app switcher, no re-login. Crucially it issues a *new* token rather
    than mutating the old one, so any given session is pinned to one tenant for
    its whole life."""
    options = mia_client.get("/api/auth/my-agencies").json()
    assert len(options) == 2

    bluepeak = next(o for o in options if o["agency_name"] == "Bluepeak Digital")
    switched = mia_client.post(f"/api/auth/switch-agency/{bluepeak['membership_id']}")
    assert switched.status_code == 200

    new_token = switched.json()["access_token"]
    assert new_token != mia_client.token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}).json()
    assert me["role"] == "agency_admin"
    assert me["agency_name"] == "Bluepeak Digital"

    # The old token is untouched and still scoped to Northwind.
    assert mia_client.get("/api/auth/me").json()["role"] == "client_user"


def test_cannot_switch_into_an_agency_you_do_not_belong_to(
    ada: Actor, mia_admin: Actor
) -> None:
    response = ada.post(f"/api/auth/switch-agency/{mia_admin.me['membership_id']}")
    assert response.status_code == 401


def test_login_does_not_reveal_whether_an_address_exists(client) -> None:
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@nowhere.test", "password": PASSWORD}
    )
    wrong_password = client.post(
        "/api/auth/login", json={"email": "ada@northwind.test", "password": "not-the-password"}
    )
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["detail"] == wrong_password.json()["detail"]
