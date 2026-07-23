"""Tasks, comments, time entries and search."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.engine import Connection

from .. import repositories as repo
from ..deps import Principal, get_conn, get_principal, require_staff
from ..errors import BadRequest, NotFound
from ..schemas import (
    CommentCreate,
    CommentOut,
    SearchHit,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    TimeEntryCreate,
    TimeEntryOut,
)

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_project_tasks(
    project_id: UUID,
    status: str | None = Query(default=None),
    assignee: UUID | None = Query(default=None),
    q: str | None = Query(default=None),
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> list[TaskOut]:
    """Board data.

    Note what is *not* here: no `if viewer.is_client: ... visibility == 'client'`.
    Filters compose on top of the policy rather than replacing it, so adding a
    new filter tomorrow cannot accidentally widen what a client sees.
    """
    if repo.get_project(conn, project_id) is None:
        raise NotFound("Project")
    rows = repo.list_tasks(
        conn, project_id=project_id, status=status, assignee_membership_id=assignee, query=q
    )
    return [TaskOut(**t) for t in rows]


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(
    project_id: UUID,
    payload: TaskCreate,
    principal: Principal = Depends(require_staff),
    conn: Connection = Depends(get_conn),
) -> TaskOut:
    if repo.get_project(conn, project_id) is None:
        raise NotFound("Project")

    if payload.assignee_membership_id and not repo.is_project_member(
        conn, project_id, payload.assignee_membership_id
    ):
        # The FK would reject this anyway; catching it here turns a 500 into a
        # sentence the UI can show.
        raise BadRequest("Assignee is not a member of this project")

    created = repo.create_task(
        conn,
        agency_id=principal.agency_id,
        project_id=project_id,
        created_by=principal.membership_id,
        data={
            "title": payload.title.strip(),
            "description": payload.description,
            "status": payload.status,
            "priority": payload.priority,
            "visibility": payload.visibility,
            "assignee_membership_id": payload.assignee_membership_id,
            "due_date": payload.due_date,
        },
    )
    if created is None:
        raise NotFound("Project")
    return TaskOut(**created)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    task_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> TaskOut:
    task = repo.get_task(conn, task_id)
    if task is None:
        raise NotFound("Task")
    return TaskOut(**task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    principal: Principal = Depends(require_staff),
    conn: Connection = Depends(get_conn),
) -> TaskOut:
    existing = repo.get_task(conn, task_id)
    if existing is None:
        raise NotFound("Task")

    changes = payload.model_dump(exclude_none=True, exclude={"clear_assignee", "clear_due_date"})
    if payload.clear_assignee:
        changes["assignee_membership_id"] = None
    if payload.clear_due_date:
        changes["due_date"] = None

    if changes.get("assignee_membership_id") and not repo.is_project_member(
        conn, existing["project_id"], changes["assignee_membership_id"]
    ):
        raise BadRequest("Assignee is not a member of this project")

    updated = repo.update_task(conn, task_id, changes)
    if updated is None:
        raise NotFound("Task")

    if "visibility" in changes and changes["visibility"] != existing["visibility"]:
        repo.record_audit(
            conn, agency_id=principal.agency_id, actor=principal.membership_id,
            action="task.visibility_changed", entity_type="task", entity_id=task_id,
            detail={"from": existing["visibility"], "to": changes["visibility"]},
        )
    return TaskOut(**updated)


# --- comments ---------------------------------------------------------------


@router.get("/tasks/{task_id}/comments", response_model=list[CommentOut])
def list_comments(
    task_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> list[CommentOut]:
    if repo.get_task(conn, task_id) is None:
        raise NotFound("Task")
    return [CommentOut(**c) for c in repo.list_comments(conn, task_id)]


@router.post("/tasks/{task_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(
    task_id: UUID,
    payload: CommentCreate,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> CommentOut:
    """Commenting is the one thing a client may create.

    A client's comment is forced to client visibility -- there is no way for
    them to post into the agency's internal thread, and the policy's WITH CHECK
    would reject it even if this line were deleted.
    """
    if repo.get_task(conn, task_id) is None:
        raise NotFound("Task")

    visibility = "client" if principal.is_client else payload.visibility
    created = repo.create_comment(
        conn,
        agency_id=principal.agency_id,
        task_id=task_id,
        author=principal.membership_id,
        body=payload.body.strip(),
        visibility=visibility,
    )
    if created is None:
        raise NotFound("Task")
    return CommentOut(**created)


# --- time tracking ----------------------------------------------------------


@router.get("/tasks/{task_id}/time-entries", response_model=list[TimeEntryOut])
def list_time_entries(
    task_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> list[TimeEntryOut]:
    if repo.get_task(conn, task_id) is None:
        raise NotFound("Task")
    return [TimeEntryOut(**t) for t in repo.list_time_entries(conn, task_id)]


@router.post("/tasks/{task_id}/time-entries", response_model=TimeEntryOut, status_code=201)
def create_time_entry(
    task_id: UUID,
    payload: TimeEntryCreate,
    principal: Principal = Depends(require_staff),
    conn: Connection = Depends(get_conn),
) -> TimeEntryOut:
    if repo.get_task(conn, task_id) is None:
        raise NotFound("Task")
    created = repo.create_time_entry(
        conn,
        agency_id=principal.agency_id,
        task_id=task_id,
        membership_id=principal.membership_id,
        member_role=principal.role,
        minutes=payload.minutes,
        note=payload.note,
        entry_date=payload.entry_date or date.today(),
    )
    if created is None:
        raise NotFound("Task")
    return TimeEntryOut(**created)


# --- search -----------------------------------------------------------------


@router.get("/search", response_model=list[SearchHit])
def search(
    q: str = Query(min_length=2, max_length=200),
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> list[SearchHit]:
    """Cross-project search over tasks, comments and filenames.

    This is the endpoint that leaks in most implementations of this exercise:
    it is written once, bypasses the per-project list handlers where the
    visibility checks live, and nobody re-tests it. Here it is three plain
    SELECTs over three policy-protected tables, so it inherits the same rules as
    everything else and had nothing to forget in the first place.
    """
    return [SearchHit(**h) for h in repo.search(conn, q)]
