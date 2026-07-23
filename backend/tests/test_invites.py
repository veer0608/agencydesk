"""Edge case 4: invite races.

Two requirements, each with a teeth-bearing database object behind it:

  resending must not duplicate      -> UNIQUE (agency_id, lower(email))
                                       WHERE status = 'pending'
  accepting twice must not create
  two accounts                      -> app.accept_invite() claims the invite with
                                       a single conditional UPDATE
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, text

from .conftest import Actor


def _pending_count(email: str) -> int:
    """Counted as the owner, so the assertion sees the true state of the table
    rather than a policy-filtered view of it."""
    engine = create_engine(os.environ["OWNER_DATABASE_URL"], future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM invites WHERE lower(email) = lower(:e) "
                     "AND status = 'pending'"),
                {"e": email},
            ).scalar()
    finally:
        engine.dispose()


def _membership_count(email: str) -> int:
    engine = create_engine(os.environ["OWNER_DATABASE_URL"], future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id "
                     "WHERE lower(u.email) = lower(:e)"),
                {"e": email},
            ).scalar()
    finally:
        engine.dispose()


def _user_count(email: str) -> int:
    engine = create_engine(os.environ["OWNER_DATABASE_URL"], future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM users WHERE lower(email) = lower(:e)"), {"e": email}
            ).scalar()
    finally:
        engine.dispose()


def test_resending_updates_the_same_invite(ada: Actor) -> None:
    email = "newdesigner@northwind.test"

    first = ada.post("/api/invites", json={"email": email, "role": "agency_member"})
    assert first.status_code == 201
    assert first.json()["resent"] is False

    second = ada.post("/api/invites", json={"email": email, "role": "agency_member"})
    assert second.status_code == 201
    assert second.json()["resent"] is True

    assert second.json()["id"] == first.json()["id"], "resend must reuse the row"
    assert second.json()["invite_url"] != first.json()["invite_url"], (
        "resend must rotate the token so an older forwarded email stops working"
    )
    assert _pending_count(email) == 1


def test_rotating_the_token_invalidates_the_previous_link(ada: Actor, client) -> None:
    email = "rotated@northwind.test"
    first = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    ada.post("/api/invites", json={"email": email, "role": "agency_member"})

    stale = first["invite_url"].split("token=")[1]
    assert client.get(f"/api/auth/invite/{stale}").status_code == 404


def test_case_and_whitespace_do_not_create_a_second_invite(ada: Actor) -> None:
    ada.post("/api/invites", json={"email": "Casey@northwind.test", "role": "agency_member"})
    ada.post("/api/invites", json={"email": "casey@northwind.test", "role": "agency_member"})
    assert _pending_count("casey@northwind.test") == 1


def test_concurrent_resends_cannot_duplicate(ada: Actor) -> None:
    """The real shape of the bug: an impatient admin double-clicking Resend.

    A read-then-write implementation loses this race -- both requests see no
    pending invite and both insert. The unique index makes the second one an
    UPDATE of the first rather than a new row.
    """
    email = "raced@northwind.test"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: ada.post(
                    "/api/invites", json={"email": email, "role": "agency_member"}
                ).status_code,
                range(6),
            )
        )

    assert all(code == 201 for code in results), results
    assert _pending_count(email) == 1


def test_accepting_twice_does_not_create_two_accounts(ada: Actor, client) -> None:
    email = "firsttimer@northwind.test"
    invite = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    token = invite["invite_url"].split("token=")[1]

    first = client.post(
        "/api/auth/accept-invite",
        json={"token": token, "full_name": "First Timer", "password": "hunter2hunter2"},
    )
    assert first.status_code == 200
    assert first.json()["created_user"] is True

    second = client.post(
        "/api/auth/accept-invite",
        json={"token": token, "full_name": "Impostor", "password": "somethingelse1"},
    )
    assert second.status_code == 400

    assert _user_count(email) == 1
    assert _membership_count(email) == 1

    # The account that exists is the one from the first acceptance, and the
    # second attempt did not overwrite the password.
    login = client.post("/api/auth/login", json={"email": email, "password": "hunter2hunter2"})
    assert login.status_code == 200
    assert login.json()["full_name"] == "First Timer"


def test_simultaneous_acceptance_yields_exactly_one_winner(ada: Actor, client) -> None:
    email = "photofinish@northwind.test"
    invite = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    token = invite["invite_url"].split("token=")[1]

    def accept(_: int):
        return client.post(
            "/api/auth/accept-invite",
            json={"token": token, "full_name": "Photo Finish", "password": "hunter2hunter2"},
        ).status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(accept, range(5)))

    assert codes.count(200) == 1, f"expected exactly one winner, got {codes}"
    assert _user_count(email) == 1
    assert _membership_count(email) == 1


def test_inviting_an_existing_person_adds_a_hat_not_an_account(
    ada: Actor, raj: Actor, client
) -> None:
    """The identity model and the invite flow meeting each other.

    Somebody joins Northwind, then a completely unrelated agency invites the same
    address. They must end up with two memberships and still one account -- and
    critically, their original password must keep working, because the second
    agency does not get to set credentials for a person who already exists.
    """
    email = "twohats@example.test"
    original_password = "first-password-1"

    joined = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    client.post(
        "/api/auth/accept-invite",
        json={
            "token": joined["invite_url"].split("token=")[1],
            "full_name": "Tomas Ekwueme",
            "password": original_password,
        },
    )
    assert _user_count(email) == 1
    assert _membership_count(email) == 1

    # Now the other tenant invites the same human.
    invite = raj.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    accepted = client.post(
        "/api/auth/accept-invite",
        json={
            "token": invite["invite_url"].split("token=")[1],
            "full_name": "Tomas Ekwueme",
            "password": "second-password-2",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["created_user"] is False
    assert accepted.json()["already_had_account"] is True

    assert _user_count(email) == 1, "must not fork the person into two accounts"
    assert _membership_count(email) == 2

    # Original password still works; the second invite could not reset it.
    assert client.post(
        "/api/auth/login", json={"email": email, "password": original_password}
    ).status_code == 200
    assert client.post(
        "/api/auth/login", json={"email": email, "password": "second-password-2"}
    ).status_code == 401

    # And they now get the agency picker rather than an arbitrary tenant.
    body = client.post(
        "/api/auth/login", json={"email": email, "password": original_password}
    ).json()
    assert body["access_token"] is None
    assert {m["agency_name"] for m in body["memberships"]} == {
        "Northwind Studio", "Bluepeak Digital"
    }


def test_revoking_frees_the_slot_and_keeps_the_history(ada: Actor) -> None:
    email = "changedmymind@northwind.test"
    invite = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()

    assert ada.delete(f"/api/invites/{invite['id']}").status_code == 200
    assert _pending_count(email) == 0

    fresh = ada.post("/api/invites", json={"email": email, "role": "agency_member"})
    assert fresh.status_code == 201
    assert fresh.json()["id"] != invite["id"], "a revoked invite is history, not a slot"

    rows = ada.get("/api/invites").json()
    statuses = [r["status"] for r in rows if r["email"] == email]
    assert sorted(statuses) == ["pending", "revoked"]


def test_a_client_invite_must_name_a_client(ada: Actor, northwind: dict) -> None:
    bad = ada.post("/api/invites", json={"email": "x@harbor.test", "role": "client_user"})
    assert bad.status_code == 400

    harbor = next(c for c in northwind["clients"] if c["name"] == "Harbor Foods")
    good = ada.post(
        "/api/invites",
        json={"email": "x@harbor.test", "role": "client_user", "client_id": harbor["id"]},
    )
    assert good.status_code == 201


def test_cannot_invite_into_another_tenants_client(raj: Actor, northwind: dict) -> None:
    harbor = next(c for c in northwind["clients"] if c["name"] == "Harbor Foods")
    response = raj.post(
        "/api/invites",
        json={"email": "sneaky@bluepeak.test", "role": "client_user", "client_id": harbor["id"]},
    )
    assert response.status_code == 404


def test_only_admins_can_invite(ben: Actor) -> None:
    assert ben.post(
        "/api/invites", json={"email": "nope@northwind.test", "role": "agency_member"}
    ).status_code == 403
