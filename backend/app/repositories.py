"""Data access.

Deliberately hand-written SQL rather than an ORM. With row-level security doing
the enforcement, the thing a reviewer most needs to be able to do is read a
query and see exactly what hits the database -- no lazy loads, no relationship
traversals firing off queries under a different context, no `.all()` that
quietly widened a join.

None of the SELECTs below filter by agency_id. They do not need to: the policies
in migration 0002 have already narrowed every table to the caller's tenant, role
and visibility. If a query here is wrong, it returns too little, never too much.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db import fetch_all, fetch_one, scalar

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

CLIENT_COLUMNS = """
SELECT c.id, c.name, c.contact_email,
       (SELECT count(*) FROM projects p WHERE p.client_id = c.id)::int AS project_count
FROM clients c
"""


def list_clients(conn: Connection) -> list[dict]:
    return fetch_all(conn, CLIENT_COLUMNS + " ORDER BY c.name")


def get_client(conn: Connection, client_id: UUID) -> dict | None:
    return fetch_one(conn, CLIENT_COLUMNS + " WHERE c.id = :id", {"id": client_id})


def create_client(conn: Connection, *, agency_id: UUID, name: str, contact_email: str | None) -> dict:
    return fetch_one(
        conn,
        """
        INSERT INTO clients (agency_id, name, contact_email)
        VALUES (:agency_id, :name, :contact_email)
        RETURNING id, name, contact_email, 0::int AS project_count
        """,
        {"agency_id": agency_id, "name": name, "contact_email": contact_email},
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

PROJECT_COLUMNS = """
SELECT p.id, p.name, p.description, p.status::text AS status,
       p.client_id, c.name AS client_name, p.created_at
FROM projects p
JOIN clients c ON c.id = p.client_id
"""


def list_projects(conn: Connection) -> list[dict]:
    return fetch_all(conn, PROJECT_COLUMNS + " ORDER BY p.created_at DESC")


def get_project(conn: Connection, project_id: UUID) -> dict | None:
    return fetch_one(conn, PROJECT_COLUMNS + " WHERE p.id = :id", {"id": project_id})


def create_project(
    conn: Connection,
    *,
    agency_id: UUID,
    client_id: UUID,
    name: str,
    description: str,
    created_by: UUID | None = None,
) -> dict | None:
    """Create a project and put its creator on it.

    The membership row is not decoration. `tasks.assignee_membership_id` has a
    composite foreign key into `project_members`, so a project with no members
    cannot have a single assigned task -- it is inert until somebody joins it.
    Enrolling the creator in the same transaction means the project is usable
    the moment it exists.
    """
    row = fetch_one(
        conn,
        """
        INSERT INTO projects (agency_id, client_id, name, description)
        VALUES (:agency_id, :client_id, :name, :description)
        RETURNING id
        """,
        {"agency_id": agency_id, "client_id": client_id, "name": name, "description": description},
    )
    if row is None:
        return None

    if created_by is not None:
        add_project_member(
            conn, project_id=row["id"], agency_id=agency_id, membership_id=created_by
        )

    return get_project(conn, row["id"])


def update_project(conn: Connection, project_id: UUID, changes: dict[str, Any]) -> dict | None:
    if not changes:
        return get_project(conn, project_id)
    assignments = ", ".join(f"{col} = :{col}" for col in changes)
    updated = fetch_one(
        conn,
        f"UPDATE projects SET {assignments} WHERE id = :id RETURNING id",
        {**changes, "id": project_id},
    )
    return get_project(conn, project_id) if updated else None


# ---------------------------------------------------------------------------
# Project membership
# ---------------------------------------------------------------------------


def list_project_members(conn: Connection, project_id: UUID) -> list[dict]:
    return fetch_all(
        conn,
        """
        SELECT pm.membership_id, u.full_name, u.email,
               pm.member_role::text AS role, pm.added_at
        FROM project_members pm
        JOIN memberships m ON m.id = pm.membership_id
        JOIN users u       ON u.id = m.user_id
        WHERE pm.project_id = :project_id
        ORDER BY u.full_name
        """,
        {"project_id": project_id},
    )


def list_agency_staff(conn: Connection) -> list[dict]:
    """Everyone at the caller's agency who can be put on a project.

    No agency filter here: `memberships_read` already restricts the roster to
    the agency the caller is acting in, and refuses it outright to a client --
    enumerating agency personnel is not something a client portal should do.

    Client memberships are excluded because they are not merely unwanted here,
    they are impossible: `project_members.member_role` carries
    CHECK (member_role IN ('agency_admin', 'agency_member')).
    """
    return fetch_all(
        conn,
        """
        SELECT m.id AS membership_id, u.full_name, u.email, m.role::text AS role
        FROM memberships m
        JOIN users u ON u.id = m.user_id
        WHERE m.status = 'active'
          AND m.role <> 'client_user'
        ORDER BY u.full_name
        """,
        {},
    )


def is_project_member(conn: Connection, project_id: UUID, membership_id: UUID) -> bool:
    return bool(
        scalar(
            conn,
            "SELECT 1 FROM project_members WHERE project_id = :p AND membership_id = :m",
            {"p": project_id, "m": membership_id},
        )
    )


def add_project_member(
    conn: Connection, *, project_id: UUID, agency_id: UUID, membership_id: UUID
) -> bool:
    # member_role is copied from memberships rather than supplied by the caller;
    # the composite FK then guarantees the copy matches, and the CHECK
    # constraint rejects client_user outright.
    row = fetch_one(
        conn,
        """
        INSERT INTO project_members (project_id, membership_id, agency_id, member_role)
        SELECT :project_id, m.id, m.agency_id, m.role
        FROM memberships m
        WHERE m.id = :membership_id AND m.status = 'active'
        ON CONFLICT (project_id, membership_id) DO NOTHING
        RETURNING membership_id
        """,
        {"project_id": project_id, "membership_id": membership_id, "agency_id": agency_id},
    )
    return row is not None


def tasks_assigned_to(conn: Connection, project_id: UUID, membership_id: UUID) -> list[UUID]:
    rows = fetch_all(
        conn,
        """
        SELECT id FROM tasks
        WHERE project_id = :project_id AND assignee_membership_id = :membership_id
        ORDER BY created_at
        """,
        {"project_id": project_id, "membership_id": membership_id},
    )
    return [r["id"] for r in rows]


def remove_project_member(conn: Connection, project_id: UUID, membership_id: UUID) -> bool:
    row = fetch_one(
        conn,
        """
        DELETE FROM project_members
        WHERE project_id = :project_id AND membership_id = :membership_id
        RETURNING membership_id
        """,
        {"project_id": project_id, "membership_id": membership_id},
    )
    return row is not None


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

TASK_COLUMNS = """
SELECT t.id, t.project_id, p.name AS project_name, t.title, t.description,
       t.status::text     AS status,
       t.priority::text   AS priority,
       t.visibility::text AS visibility,
       t.assignee_membership_id,
       app.display_name(t.assignee_membership_id) AS assignee_name,
       t.due_date, t.created_at, t.updated_at,
       (SELECT count(*) FROM comments cm WHERE cm.task_id = t.id)::int          AS comment_count,
       (SELECT count(*) FROM files f WHERE f.task_id = t.id)::int               AS file_count,
       (SELECT coalesce(sum(te.minutes), 0) FROM time_entries te
         WHERE te.task_id = t.id)::int                                          AS minutes_logged
FROM tasks t
JOIN projects p ON p.id = t.project_id
"""


def list_tasks(
    conn: Connection,
    *,
    project_id: UUID | None = None,
    status: str | None = None,
    assignee_membership_id: UUID | None = None,
    query: str | None = None,
) -> list[dict]:
    where = []
    params: dict[str, Any] = {}
    if project_id is not None:
        where.append("t.project_id = :project_id")
        params["project_id"] = project_id
    if status is not None:
        where.append("t.status = CAST(:status AS task_status)")
        params["status"] = status
    if assignee_membership_id is not None:
        where.append("t.assignee_membership_id = :assignee")
        params["assignee"] = assignee_membership_id
    if query:
        where.append("(lower(t.title) LIKE :q OR lower(t.description) LIKE :q)")
        params["q"] = f"%{query.lower()}%"

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order = " ORDER BY t.status, t.priority DESC, t.created_at"
    return fetch_all(conn, TASK_COLUMNS + clause + order, params)


def get_task(conn: Connection, task_id: UUID) -> dict | None:
    return fetch_one(conn, TASK_COLUMNS + " WHERE t.id = :id", {"id": task_id})


def create_task(conn: Connection, *, agency_id: UUID, project_id: UUID, created_by: UUID, data: dict) -> dict | None:
    row = fetch_one(
        conn,
        """
        INSERT INTO tasks (agency_id, project_id, title, description, status, priority,
                           visibility, assignee_membership_id, due_date, created_by_membership_id)
        VALUES (:agency_id, :project_id, :title, :description,
                CAST(:status AS task_status), CAST(:priority AS task_priority),
                CAST(:visibility AS visibility), :assignee_membership_id, :due_date, :created_by)
        RETURNING id
        """,
        {"agency_id": agency_id, "project_id": project_id, "created_by": created_by, **data},
    )
    return get_task(conn, row["id"]) if row else None


# Enum columns need an explicit cast. Note the CAST(:x AS t) spelling rather
# than PostgreSQL's :x::t shorthand -- SQLAlchemy's text() bind-parameter regex
# refuses a name followed by ':', so the shorthand would silently pass through
# unbound and blow up at the driver.
_TASK_CASTS = {
    "status": "task_status",
    "priority": "task_priority",
    "visibility": "visibility",
}


def _assignment(col: str) -> str:
    cast = _TASK_CASTS.get(col)
    return f"{col} = CAST(:{col} AS {cast})" if cast else f"{col} = :{col}"


def update_task(conn: Connection, task_id: UUID, changes: dict[str, Any]) -> dict | None:
    if not changes:
        return get_task(conn, task_id)
    assignments = ", ".join(_assignment(col) for col in changes)
    updated = fetch_one(
        conn,
        f"UPDATE tasks SET {assignments} WHERE id = :id RETURNING id",
        {**changes, "id": task_id},
    )
    return get_task(conn, task_id) if updated else None


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

COMMENT_COLUMNS = """
SELECT cm.id, cm.task_id, cm.body, cm.visibility::text AS visibility,
       app.display_name(cm.author_membership_id) AS author_name,
       (SELECT m.role::text FROM memberships m WHERE m.id = cm.author_membership_id
         AND m.agency_id = app.current_agency_id()) AS author_role,
       cm.created_at
FROM comments cm
"""


def list_comments(conn: Connection, task_id: UUID) -> list[dict]:
    return fetch_all(
        conn, COMMENT_COLUMNS + " WHERE cm.task_id = :task_id ORDER BY cm.created_at",
        {"task_id": task_id},
    )


def create_comment(
    conn: Connection, *, agency_id: UUID, task_id: UUID, author: UUID, body: str, visibility: str
) -> dict | None:
    row = fetch_one(
        conn,
        """
        INSERT INTO comments (agency_id, task_id, author_membership_id, body, visibility)
        VALUES (:agency_id, :task_id, :author, :body, CAST(:visibility AS visibility))
        RETURNING id
        """,
        {"agency_id": agency_id, "task_id": task_id, "author": author,
         "body": body, "visibility": visibility},
    )
    if not row:
        return None
    return fetch_one(conn, COMMENT_COLUMNS + " WHERE cm.id = :id", {"id": row["id"]})


# ---------------------------------------------------------------------------
# Time entries
# ---------------------------------------------------------------------------

TIME_COLUMNS = """
SELECT te.id, te.task_id, te.minutes, te.note, te.entry_date,
       app.display_name(te.membership_id) AS member_name, te.created_at
FROM time_entries te
"""


def list_time_entries(conn: Connection, task_id: UUID) -> list[dict]:
    return fetch_all(
        conn, TIME_COLUMNS + " WHERE te.task_id = :task_id ORDER BY te.entry_date DESC, te.created_at DESC",
        {"task_id": task_id},
    )


def create_time_entry(
    conn: Connection, *, agency_id: UUID, task_id: UUID, membership_id: UUID,
    member_role: str, minutes: int, note: str, entry_date: date,
) -> dict | None:
    row = fetch_one(
        conn,
        """
        INSERT INTO time_entries (agency_id, task_id, membership_id, member_role,
                                  minutes, note, entry_date)
        VALUES (:agency_id, :task_id, :membership_id, CAST(:member_role AS membership_role),
                :minutes, :note, :entry_date)
        RETURNING id
        """,
        {"agency_id": agency_id, "task_id": task_id, "membership_id": membership_id,
         "member_role": member_role, "minutes": minutes, "note": note, "entry_date": entry_date},
    )
    if not row:
        return None
    return fetch_one(conn, TIME_COLUMNS + " WHERE te.id = :id", {"id": row["id"]})


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

FILE_COLUMNS = """
SELECT f.id, f.task_id, f.filename, f.content_type, f.size_bytes,
       f.visibility::text      AS visibility,
       f.approval_status::text AS approval_status,
       f.approval_note,
       app.display_name(f.approved_by_membership_id)  AS approved_by_name,
       f.approved_at,
       app.display_name(f.uploaded_by_membership_id)  AS uploaded_by_name,
       f.created_at
FROM files f
"""


def list_files(conn: Connection, task_id: UUID) -> list[dict]:
    return fetch_all(
        conn, FILE_COLUMNS + " WHERE f.task_id = :task_id ORDER BY f.created_at DESC",
        {"task_id": task_id},
    )


def get_file(conn: Connection, file_id: UUID) -> dict | None:
    return fetch_one(conn, FILE_COLUMNS + " WHERE f.id = :id", {"id": file_id})


def get_file_storage(conn: Connection, file_id: UUID) -> dict | None:
    return fetch_one(
        conn,
        "SELECT id, filename, content_type, storage_key FROM files WHERE id = :id",
        {"id": file_id},
    )


def create_file(conn: Connection, **kw: Any) -> dict | None:
    row = fetch_one(
        conn,
        """
        INSERT INTO files (agency_id, task_id, uploaded_by_membership_id, filename,
                           content_type, size_bytes, storage_key, visibility)
        VALUES (:agency_id, :task_id, :uploaded_by, :filename, :content_type,
                :size_bytes, :storage_key, CAST(:visibility AS visibility))
        RETURNING id
        """,
        kw,
    )
    return get_file(conn, row["id"]) if row else None


def set_file_approval(
    conn: Connection, *, file_id: UUID, status: str, note: str | None, decided_by: UUID
) -> dict | None:
    row = fetch_one(
        conn,
        """
        UPDATE files
           SET approval_status = CAST(:status AS approval_status),
               approval_note = :note,
               approved_by_membership_id = :decided_by,
               approved_at = now()
         WHERE id = :id
        RETURNING id
        """,
        {"id": file_id, "status": status, "note": note, "decided_by": decided_by},
    )
    return get_file(conn, file_id) if row else None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def project_dashboard(conn: Connection, project_id: UUID) -> dict:
    by_status = fetch_all(
        conn,
        """
        SELECT s.status::text AS status,
               count(t.id)::int AS count
        FROM unnest(enum_range(NULL::task_status)) AS s(status)
        LEFT JOIN tasks t ON t.status = s.status AND t.project_id = :project_id
        GROUP BY s.status
        ORDER BY s.status
        """,
        {"project_id": project_id},
    )
    totals = fetch_one(
        conn,
        """
        SELECT
            (SELECT count(*) FROM tasks t WHERE t.project_id = :project_id)::int AS total_tasks,
            (SELECT count(*) FROM tasks t WHERE t.project_id = :project_id
               AND t.status <> 'done')::int AS open_tasks,
            (SELECT count(*) FROM tasks t WHERE t.project_id = :project_id
               AND t.status <> 'done' AND t.due_date < current_date)::int AS overdue_tasks,
            (SELECT coalesce(sum(te.minutes), 0) FROM time_entries te
               JOIN tasks t ON t.id = te.task_id
              WHERE t.project_id = :project_id)::int AS minutes_logged,
            (SELECT count(*) FROM files f
               JOIN tasks t ON t.id = f.task_id
              WHERE t.project_id = :project_id AND f.approval_status = 'pending'
                AND f.visibility = 'client')::int AS files_awaiting_approval
        """,
        {"project_id": project_id},
    )
    return {"tasks_by_status": by_status, **(totals or {})}


# ---------------------------------------------------------------------------
# Search -- the path that is easiest to forget and easiest to leak through.
# It is three UNIONed queries over three RLS-protected tables; there is no
# separate "search index" that could drift out of sync with the policies.
# ---------------------------------------------------------------------------


def search(conn: Connection, query: str, limit: int = 50) -> list[dict]:
    return fetch_all(
        conn,
        """
        SELECT 'task' AS kind, t.id AS task_id, t.project_id, p.name AS project_name,
               t.title, left(t.description, 160) AS snippet,
               t.visibility::text AS visibility, t.created_at
        FROM tasks t JOIN projects p ON p.id = t.project_id
        WHERE lower(t.title) LIKE :q OR lower(t.description) LIKE :q

        UNION ALL

        SELECT 'comment', cm.task_id, t.project_id, p.name, t.title,
               left(cm.body, 160), cm.visibility::text, cm.created_at
        FROM comments cm
        JOIN tasks t    ON t.id = cm.task_id
        JOIN projects p ON p.id = t.project_id
        WHERE lower(cm.body) LIKE :q

        UNION ALL

        SELECT 'file', f.task_id, t.project_id, p.name, t.title,
               f.filename, f.visibility::text, f.created_at
        FROM files f
        JOIN tasks t    ON t.id = f.task_id
        JOIN projects p ON p.id = t.project_id
        WHERE lower(f.filename) LIKE :q

        ORDER BY created_at DESC
        LIMIT :limit
        """,
        {"q": f"%{query.lower()}%", "limit": limit},
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def record_audit(
    conn: Connection, *, agency_id: UUID, actor: UUID | None, action: str,
    entity_type: str, entity_id: UUID | None, detail: dict | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO audit_log (agency_id, actor_membership_id, action,
                                   entity_type, entity_id, detail)
            VALUES (:agency_id, :actor, :action, :entity_type, :entity_id, CAST(:detail AS jsonb))
            """
        ),
        {"agency_id": agency_id, "actor": actor, "action": action,
         "entity_type": entity_type, "entity_id": entity_id,
         "detail": json.dumps(detail or {})},
    )
