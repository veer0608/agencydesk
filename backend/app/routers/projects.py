"""Clients, projects, project membership and the per-project dashboard."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from .. import repositories as repo
from ..deps import Principal, get_conn, get_principal, require_admin, require_staff
from ..errors import NotFound
from ..schemas import (
    AgencyStaffOut,
    ClientCreate,
    ClientOut,
    ProjectCreate,
    ProjectDashboard,
    ProjectMemberAdd,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
    RemovalResult,
    StatusCount,
)

router = APIRouter(prefix="/api", tags=["projects"])


# --- clients ----------------------------------------------------------------


@router.get("/clients", response_model=list[ClientOut])
def list_clients(
    _: Principal = Depends(get_principal), conn: Connection = Depends(get_conn)
) -> list[ClientOut]:
    return [ClientOut(**c) for c in repo.list_clients(conn)]


@router.post("/clients", response_model=ClientOut, status_code=201)
def create_client(
    payload: ClientCreate,
    principal: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> ClientOut:
    created = repo.create_client(
        conn,
        agency_id=principal.agency_id,
        name=payload.name.strip(),
        contact_email=payload.contact_email,
    )
    return ClientOut(**created)


# --- projects ---------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    _: Principal = Depends(get_principal), conn: Connection = Depends(get_conn)
) -> list[ProjectOut]:
    return [ProjectOut(**p) for p in repo.list_projects(conn)]


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    principal: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> ProjectOut:
    # No explicit "is this client mine?" check: the composite foreign key
    # projects(client_id, agency_id) -> clients(id, agency_id) makes borrowing
    # another tenant's client id a constraint violation, and the read below is
    # policy-filtered anyway. We check first only to return a friendly 404.
    if repo.get_client(conn, payload.client_id) is None:
        raise NotFound("Client")

    created = repo.create_project(
        conn,
        agency_id=principal.agency_id,
        client_id=payload.client_id,
        name=payload.name.strip(),
        description=payload.description,
        created_by=principal.membership_id,
    )
    if created is None:
        raise NotFound("Client")
    repo.record_audit(
        conn, agency_id=principal.agency_id, actor=principal.membership_id,
        action="project.created", entity_type="project", entity_id=created["id"],
        detail={"name": created["name"]},
    )
    return ProjectOut(**created)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> ProjectOut:
    project = repo.get_project(conn, project_id)
    if project is None:
        raise NotFound("Project")
    return ProjectOut(**project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    _: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> ProjectOut:
    changes = payload.model_dump(exclude_none=True)
    updated = repo.update_project(conn, project_id, changes)
    if updated is None:
        raise NotFound("Project")
    return ProjectOut(**updated)


# --- project membership -----------------------------------------------------


@router.get("/agency/staff", response_model=list[AgencyStaffOut])
def list_agency_staff(
    _: Principal = Depends(require_staff),
    conn: Connection = Depends(get_conn),
) -> list[AgencyStaffOut]:
    """The agency roster, for choosing who to put on a project.

    `require_staff` gives a client a clean 403 rather than an empty list, but it
    is not what protects the data: `memberships_read` would return nothing to a
    client even if this dependency were removed.
    """
    return [AgencyStaffOut(**m) for m in repo.list_agency_staff(conn)]


@router.get("/projects/{project_id}/members", response_model=list[ProjectMemberOut])
def list_members(
    project_id: UUID,
    _: Principal = Depends(require_staff),
    conn: Connection = Depends(get_conn),
) -> list[ProjectMemberOut]:
    if repo.get_project(conn, project_id) is None:
        raise NotFound("Project")
    return [ProjectMemberOut(**m) for m in repo.list_project_members(conn, project_id)]


@router.post("/projects/{project_id}/members", response_model=list[ProjectMemberOut], status_code=201)
def add_member(
    project_id: UUID,
    payload: ProjectMemberAdd,
    principal: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> list[ProjectMemberOut]:
    if repo.get_project(conn, project_id) is None:
        raise NotFound("Project")

    added = repo.add_project_member(
        conn,
        project_id=project_id,
        agency_id=principal.agency_id,
        membership_id=payload.membership_id,
    )
    if added:
        repo.record_audit(
            conn, agency_id=principal.agency_id, actor=principal.membership_id,
            action="project.member_added", entity_type="project", entity_id=project_id,
            detail={"membership_id": str(payload.membership_id)},
        )
    return [ProjectMemberOut(**m) for m in repo.list_project_members(conn, project_id)]


@router.delete("/projects/{project_id}/members/{membership_id}", response_model=RemovalResult)
def remove_member(
    project_id: UUID,
    membership_id: UUID,
    principal: Principal = Depends(require_admin),
    conn: Connection = Depends(get_conn),
) -> RemovalResult:
    """Remove somebody from a project, mid-task.

    Policy: their work is *unassigned, not deleted*. Tasks stay exactly where
    they are on the board -- same status, same comments, same logged hours -- and
    land back in the project's unassigned column for a lead to redistribute.
    Deleting or auto-reassigning would either destroy history or silently make
    someone else accountable for work they have never seen.

    The unassignment is not performed here. The foreign key
        tasks(project_id, assignee_membership_id)
            -> project_members(project_id, membership_id)
            ON DELETE SET NULL (assignee_membership_id)
    does it inside the same statement as the DELETE. This handler only reads the
    affected ids first so it can report and audit them; if a future endpoint
    removes a member some other way, the database still cleans up behind it.
    """
    if repo.get_project(conn, project_id) is None:
        raise NotFound("Project")
    if not repo.is_project_member(conn, project_id, membership_id):
        raise NotFound("Project member")

    affected = repo.tasks_assigned_to(conn, project_id, membership_id)
    repo.remove_project_member(conn, project_id, membership_id)

    repo.record_audit(
        conn, agency_id=principal.agency_id, actor=principal.membership_id,
        action="project.member_removed", entity_type="project", entity_id=project_id,
        detail={
            "membership_id": str(membership_id),
            "unassigned_task_ids": [str(t) for t in affected],
        },
    )

    return RemovalResult(
        removed_membership_id=membership_id,
        unassigned_task_ids=affected,
        detail=(
            f"{len(affected)} task(s) returned to the unassigned column; "
            "status, comments, files and logged time were left untouched."
        ),
    )


# --- dashboard --------------------------------------------------------------


@router.get("/projects/{project_id}/dashboard", response_model=ProjectDashboard)
def project_dashboard(
    project_id: UUID,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> ProjectDashboard:
    """Counts, computed through the caller's own policies.

    There is no privileged aggregate query anywhere in this file. The client's
    "8 tasks" and the admin's "23 tasks" are the same SQL run under two different
    row-level security contexts, which is why the dashboard cannot disagree with
    the board it sits next to.
    """
    project = repo.get_project(conn, project_id)
    if project is None:
        raise NotFound("Project")

    data = repo.project_dashboard(conn, project_id)
    scope = (
        "Counts cover client-visible work only."
        if principal.is_client
        else "Counts cover all work on this project, internal included."
    )
    return ProjectDashboard(
        project_id=project["id"],
        project_name=project["name"],
        client_name=project["client_name"],
        viewer_role=principal.role,  # type: ignore[arg-type]
        tasks_by_status=[StatusCount(**s) for s in data["tasks_by_status"]],
        total_tasks=data["total_tasks"],
        open_tasks=data["open_tasks"],
        overdue_tasks=data["overdue_tasks"],
        minutes_logged=data["minutes_logged"],
        files_awaiting_approval=data["files_awaiting_approval"],
        scope_note=scope,
    )
