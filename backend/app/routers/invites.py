"""Invitations.

The interesting requirement is "resending an invite shouldn't duplicate it".
This is handled as an upsert against a partial unique index rather than a
read-then-write in Python, because read-then-write is exactly what loses the
race when an impatient admin double-clicks Resend.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.engine import Connection

from .. import repositories as repo
from ..config import settings
from ..db import fetch_all, fetch_one
from ..deps import Principal, get_conn, require_admin
from ..errors import BadRequest, NotFound
from ..schemas import InviteCreate, InviteOut
from ..security import new_invite_token

router = APIRouter(prefix="/api/invites", tags=["invites"])


_LIST_SQL = """
SELECT i.id, i.email, i.role::text AS role, i.client_id, i.status::text AS status,
       i.expires_at, i.created_at, i.accepted_at
FROM invites i
ORDER BY i.created_at DESC
"""


@router.get("", response_model=list[InviteOut])
def list_invites(
    _: Principal = Depends(require_admin), conn: Connection = Depends(get_conn)
) -> list[InviteOut]:
    return [InviteOut(**row) for row in fetch_all(conn, _LIST_SQL)]


@router.post("", response_model=InviteOut, status_code=201)
def create_or_resend(
    payload: InviteCreate,
    request: Request,
    principal: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> InviteOut:
    """Invite somebody, or resend an existing invitation.

    One statement, no branch. `invites_one_pending_per_email` is a UNIQUE index
    on (agency_id, lower(email)) WHERE status = 'pending', so:

      * first call            -> INSERT wins, a fresh token is stored
      * resend                -> ON CONFLICT fires, the SAME ROW gets a new token
                                 and a new expiry
      * two simultaneous calls -> one inserts, the other conflicts and updates;
                                 there is no window in which two pending invites
                                 for the same address can exist

    Rotating the token on resend also invalidates whatever was in the older
    email, so a forwarded link cannot be redeemed after the fact.

    Note the index is partial: accepted and revoked invites for the same address
    stay in the table as history and never collide with a new one.
    """
    if payload.role == "client_user" and payload.client_id is None:
        raise BadRequest("A client contact must be attached to a client")
    if payload.role != "client_user" and payload.client_id is not None:
        raise BadRequest("Only client contacts may be attached to a client")
    if payload.client_id is not None and repo.get_client(conn, payload.client_id) is None:
        raise NotFound("Client")

    token, token_hash = new_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.invite_ttl_days)

    row = fetch_one(
        conn,
        """
        INSERT INTO invites (agency_id, email, role, client_id, token_hash,
                             invited_by_membership_id, expires_at)
        VALUES (:agency_id, lower(btrim(:email)), CAST(:role AS membership_role), :client_id,
                :token_hash, :invited_by, :expires_at)
        ON CONFLICT (agency_id, lower(email)) WHERE status = 'pending'
        DO UPDATE SET token_hash   = EXCLUDED.token_hash,
                      expires_at   = EXCLUDED.expires_at,
                      role         = EXCLUDED.role,
                      client_id    = EXCLUDED.client_id,
                      invited_by_membership_id = EXCLUDED.invited_by_membership_id
        RETURNING id, email, role::text AS role, client_id, status::text AS status,
                  expires_at, created_at, accepted_at,
                  (xmax <> 0) AS was_update
        """,
        {
            "agency_id": principal.agency_id,
            "email": payload.email,
            "role": payload.role,
            "client_id": payload.client_id,
            "token_hash": token_hash,
            "invited_by": principal.membership_id,
            "expires_at": expires_at,
        },
    )
    if row is None:
        raise BadRequest("Could not create invite")

    resent = bool(row.pop("was_update"))
    repo.record_audit(
        conn, agency_id=principal.agency_id, actor=principal.membership_id,
        action="invite.resent" if resent else "invite.created",
        entity_type="invite", entity_id=row["id"], detail={"email": row["email"]},
    )

    # No mail server in a take-home: hand the link straight back to the admin.
    base = str(request.base_url).rstrip("/")
    return InviteOut(**row, invite_url=f"{base}/accept-invite?token={token}", resent=resent)


@router.delete("/{invite_id}", response_model=InviteOut)
def revoke(
    invite_id: UUID,
    principal: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> InviteOut:
    """Revoking clears the pending slot so a fresh invite can be sent, while
    keeping the row as an audit trail."""
    row = fetch_one(
        conn,
        """
        UPDATE invites SET status = 'revoked'
         WHERE id = :id AND status = 'pending'
        RETURNING id, email, role::text AS role, client_id, status::text AS status,
                  expires_at, created_at, accepted_at
        """,
        {"id": invite_id},
    )
    if row is None:
        raise NotFound("Pending invite")
    repo.record_audit(
        conn, agency_id=principal.agency_id, actor=principal.membership_id,
        action="invite.revoked", entity_type="invite", entity_id=invite_id, detail={},
    )
    return InviteOut(**row)
