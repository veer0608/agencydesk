"""Request dependencies: the transaction, and who is inside it."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.engine import Connection

from .db import fetch_one, set_request_context, transaction
from .errors import Forbidden, Unauthorized
from .security import read_session_token


@dataclass(frozen=True)
class Principal:
    membership_id: UUID
    user_id: UUID
    agency_id: UUID
    agency_name: str
    role: str
    client_id: UUID | None
    full_name: str
    email: str

    @property
    def is_admin(self) -> bool:
        return self.role == "agency_admin"

    @property
    def is_staff(self) -> bool:
        return self.role in ("agency_admin", "agency_member")

    @property
    def is_client(self) -> bool:
        return self.role == "client_user"


def get_conn() -> Iterator[Connection]:
    with transaction() as conn:
        yield conn


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token or None if scheme.lower() == "bearer" else None


# Reading the membership back on every request is deliberate. The token carries
# only `sub` (person) and `mid` (which membership they are acting as) -- never a
# role or an agency id. Roles therefore come from the database at the moment of
# use, so revoking someone takes effect on their next request rather than
# whenever their token happens to expire.
_PRINCIPAL_SQL = """
SELECT m.id            AS membership_id,
       m.user_id       AS user_id,
       m.agency_id     AS agency_id,
       a.name          AS agency_name,
       m.role::text    AS role,
       m.client_id     AS client_id,
       u.full_name     AS full_name,
       u.email         AS email
FROM memberships m
JOIN agencies a ON a.id = m.agency_id
JOIN users    u ON u.id = m.user_id
WHERE m.id = :membership_id
  AND m.user_id = :user_id
  AND m.status = 'active'
"""


def get_principal(
    request: Request, conn: Connection = Depends(get_conn)
) -> Principal:
    token = _bearer(request)
    if not token:
        raise Unauthorized()

    claims = read_session_token(token)
    if not claims or "mid" not in claims or "sub" not in claims:
        raise Unauthorized("Invalid or expired session")

    # Install the context first: the lookup below runs *through* the policies it
    # is about to authorise, which means a tampered membership id resolves to
    # nothing rather than to somebody else's session.
    set_request_context(conn, user_id=claims["sub"], membership_id=claims["mid"])

    row = fetch_one(
        conn, _PRINCIPAL_SQL, {"membership_id": claims["mid"], "user_id": claims["sub"]}
    )
    if row is None:
        raise Unauthorized("Membership is no longer active")

    return Principal(
        membership_id=row["membership_id"],
        user_id=row["user_id"],
        agency_id=row["agency_id"],
        agency_name=row["agency_name"],
        role=row["role"],
        client_id=row["client_id"],
        full_name=row["full_name"],
        email=row["email"],
    )


def require_staff(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_staff:
        raise Forbidden("Agency staff only")
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise Forbidden("Agency administrators only")
    return principal
