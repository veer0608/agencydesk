"""Authentication and invite acceptance.

Login is two steps on purpose:

    POST /auth/login          -> "who are you?"   returns every agency you belong to
    POST /auth/select-agency  -> "act as whom?"   returns a session bound to ONE membership

A single-step login would have to answer "which tenant is this person?", and for
somebody who is a client contact at one agency and an admin at another there is
no correct answer. Splitting the question makes the ambiguity explicit instead of
resolving it arbitrarily.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from ..config import settings
from ..db import fetch_all, fetch_one, set_request_context
from ..deps import Principal, get_conn, get_principal
from ..errors import BadRequest, NotFound, Unauthorized
from ..schemas import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    InvitePreview,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MembershipOption,
    SelectAgencyRequest,
    TokenResponse,
)
from ..security import (
    hash_invite_token,
    hash_password,
    issue_session_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


_MEMBERSHIPS_SQL = """
SELECT m.id AS membership_id, m.agency_id, a.name AS agency_name, a.slug AS agency_slug,
       m.role::text AS role, m.client_id, c.name AS client_name
FROM memberships m
JOIN agencies a  ON a.id = m.agency_id
LEFT JOIN clients c ON c.id = m.client_id
WHERE m.user_id = :user_id AND m.status = 'active'
ORDER BY a.name
"""


def _authenticate(conn: Connection, email: str, password: str) -> dict:
    """Verify credentials and put the person (not yet a tenant) into context."""
    # `users` is behind RLS like everything else; this is one of exactly two
    # SECURITY DEFINER functions that may be called before a context exists.
    row = fetch_one(conn, "SELECT * FROM app.lookup_login(:email)", {"email": email})
    if row is None or not verify_password(password, row["password_hash"]):
        # Same message either way -- never reveal whether an address is registered.
        raise Unauthorized("Invalid email or password")

    set_request_context(conn, user_id=row["id"])
    return row


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, conn: Connection = Depends(get_conn)) -> LoginResponse:
    user = _authenticate(conn, payload.email, payload.password)
    memberships = fetch_all(conn, _MEMBERSHIPS_SQL, {"user_id": user["id"]})

    if not memberships:
        raise Unauthorized("This account is not active at any agency")

    token = None
    if len(memberships) == 1:
        token = issue_session_token(
            user_id=str(user["id"]), membership_id=str(memberships[0]["membership_id"])
        )

    return LoginResponse(
        user_id=user["id"],
        full_name=user["full_name"],
        memberships=[MembershipOption(**m) for m in memberships],
        access_token=token,
    )


@router.post("/select-agency", response_model=TokenResponse)
def select_agency(
    payload: SelectAgencyRequest, conn: Connection = Depends(get_conn)
) -> TokenResponse:
    user = _authenticate(conn, payload.email, payload.password)

    # The membership must belong to *this* user. The WHERE clause says so, and
    # the memberships policy says so independently -- guessing a membership id
    # from another agency returns nothing either way.
    chosen = fetch_one(
        conn,
        _MEMBERSHIPS_SQL.replace(
            "WHERE m.user_id = :user_id", "WHERE m.user_id = :user_id AND m.id = :membership_id"
        ),
        {"user_id": user["id"], "membership_id": payload.membership_id},
    )
    if chosen is None:
        raise Unauthorized("That membership does not belong to this account")

    token = issue_session_token(
        user_id=str(user["id"]), membership_id=str(chosen["membership_id"])
    )
    return TokenResponse(
        access_token=token,
        principal=MeResponse(
            membership_id=chosen["membership_id"],
            user_id=user["id"],
            agency_id=chosen["agency_id"],
            agency_name=chosen["agency_name"],
            role=chosen["role"],
            client_id=chosen["client_id"],
            full_name=user["full_name"],
            email=payload.email,
        ),
    )


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        membership_id=principal.membership_id,
        user_id=principal.user_id,
        agency_id=principal.agency_id,
        agency_name=principal.agency_name,
        role=principal.role,  # type: ignore[arg-type]
        client_id=principal.client_id,
        full_name=principal.full_name,
        email=principal.email,
    )


@router.get("/my-agencies", response_model=list[MembershipOption])
def my_agencies(
    principal: Principal = Depends(get_principal), conn: Connection = Depends(get_conn)
) -> list[MembershipOption]:
    """Powers the in-app agency switcher for people who wear two hats."""
    rows = fetch_all(conn, _MEMBERSHIPS_SQL, {"user_id": principal.user_id})
    return [MembershipOption(**r) for r in rows]


@router.post("/switch-agency/{membership_id}", response_model=TokenResponse)
def switch_agency(
    membership_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> TokenResponse:
    """Mint a session for another of *my own* memberships, without re-login.

    Switching agencies issues a brand new token rather than mutating the current
    one, so a session is always pinned to exactly one tenant for its whole life.
    """
    chosen = fetch_one(
        conn,
        _MEMBERSHIPS_SQL.replace(
            "WHERE m.user_id = :user_id", "WHERE m.user_id = :user_id AND m.id = :membership_id"
        ),
        {"user_id": principal.user_id, "membership_id": membership_id},
    )
    if chosen is None:
        raise Unauthorized("That membership does not belong to this account")

    token = issue_session_token(
        user_id=str(principal.user_id), membership_id=str(chosen["membership_id"])
    )
    return TokenResponse(
        access_token=token,
        principal=MeResponse(
            membership_id=chosen["membership_id"],
            user_id=principal.user_id,
            agency_id=chosen["agency_id"],
            agency_name=chosen["agency_name"],
            role=chosen["role"],
            client_id=chosen["client_id"],
            full_name=principal.full_name,
            email=principal.email,
        ),
    )


# --- invites ----------------------------------------------------------------


@router.get("/invite/{token}", response_model=InvitePreview)
def preview_invite(token: str, conn: Connection = Depends(get_conn)) -> InvitePreview:
    row = fetch_one(
        conn, "SELECT * FROM app.peek_invite(:h)", {"h": hash_invite_token(token)}
    )
    if row is None:
        raise NotFound("Invite")
    return InvitePreview(**row)


@router.post("/accept-invite", response_model=AcceptInviteResponse)
def accept_invite(
    payload: AcceptInviteRequest, conn: Connection = Depends(get_conn)
) -> AcceptInviteResponse:
    """Accepting is idempotent by construction -- see app.accept_invite().

    The password supplied here is only used if this email has never been seen
    before. Somebody who already has an account at another agency keeps their
    existing credentials and simply gains a second membership; we are adding a
    hat to a known person, not creating a second person.
    """
    result = fetch_one(
        conn,
        "SELECT * FROM app.accept_invite(:token_hash, :full_name, :password_hash)",
        {
            "token_hash": hash_invite_token(payload.token),
            "full_name": payload.full_name.strip(),
            "password_hash": hash_password(payload.password),
        },
    )
    if result is None or result["out_membership_id"] is None:
        # Deliberately one message for four different causes (unknown, expired,
        # revoked, already accepted): distinguishing them would let anyone probe
        # which invite tokens exist.
        raise BadRequest("This invite link is invalid, expired, or has already been used")

    created = bool(result["out_created_user"])
    token = issue_session_token(
        user_id=str(result["out_user_id"]), membership_id=str(result["out_membership_id"])
    )
    return AcceptInviteResponse(
        agency_id=result["out_agency_id"],
        created_user=created,
        already_had_account=not created,
        access_token=token,
    )


def invite_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.invite_ttl_days)
