"""Database access.

One request == one transaction == one tenant context.

The context is installed with `SET LOCAL`, which PostgreSQL scopes to the
current transaction. When the transaction ends the setting is gone, so a pooled
connection handed to the next request never carries the previous caller's
identity. There is no code path that sets the context outside a transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Row

from .config import settings

engine = create_engine(
    settings.database_url,
    future=True,
    # Every request holds a connection for the whole of its transaction, because
    # the tenant context is transaction-scoped -- so pool size is the real
    # concurrency limit, not a detail. Stated explicitly rather than inherited
    # from SQLAlchemy's defaults.
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
)


@contextmanager
def transaction() -> Iterator[Connection]:
    """Open a connection as the *unprivileged* application role."""
    with engine.connect() as conn:
        with conn.begin():
            yield conn


def set_request_context(
    conn: Connection,
    *,
    user_id: UUID | str | None = None,
    membership_id: UUID | str | None = None,
) -> None:
    """Bind the caller's identity to this transaction.

    Anything not supplied is cleared to the empty string, which the SQL helpers
    read back as NULL. An unauthenticated request therefore evaluates every
    policy against a NULL agency and sees nothing at all -- deny-by-default falls
    out of the design rather than being bolted on.
    """
    conn.execute(
        text("SELECT set_config('app.user_id', :uid, true), "
             "       set_config('app.membership_id', :mid, true)"),
        {"uid": str(user_id) if user_id else "", "mid": str(membership_id) if membership_id else ""},
    )


def fetch_all(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    rows = conn.execute(text(sql), params or {}).mappings().all()
    return [dict(r) for r in rows]


def fetch_one(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> dict | None:
    row: Row | None = conn.execute(text(sql), params or {}).mappings().first()
    return dict(row) if row is not None else None


def scalar(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar()
