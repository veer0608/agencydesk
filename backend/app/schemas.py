"""Request/response contracts.

Response models double as an outbound filter: FastAPI serialises only the fields
declared here, so an accidentally over-selecting query cannot turn into a leak on
the wire.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

import email_validator
from pydantic import BaseModel, EmailStr, Field

# RFC 6761 reserves `.test` for exactly this: fixtures and local demos that must
# never resolve to a real mailbox. email-validator rejects it by default, which
# would make the seed accounts unusable, so we allow that one name back in and
# keep every other special-use domain (localhost, .invalid, .onion) blocked.
if "test" in email_validator.SPECIAL_USE_DOMAIN_NAMES:
    email_validator.SPECIAL_USE_DOMAIN_NAMES.remove("test")

Role = Literal["agency_admin", "agency_member", "client_user"]
Visibility = Literal["internal", "client"]
TaskStatus = Literal["todo", "in_progress", "blocked", "review", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]
ProjectStatus = Literal["active", "on_hold", "completed", "archived"]
ApprovalStatus = Literal["pending", "approved", "needs_changes"]


# --- auth -------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MembershipOption(BaseModel):
    """One agency this person belongs to, offered at the agency picker."""

    membership_id: UUID
    agency_id: UUID
    agency_name: str
    agency_slug: str
    role: Role
    client_id: UUID | None = None
    client_name: str | None = None


class LoginResponse(BaseModel):
    user_id: UUID
    full_name: str
    memberships: list[MembershipOption]
    # Convenience only: when a person belongs to exactly one agency we hand back
    # a session immediately instead of showing a one-item picker.
    access_token: str | None = None


class SelectAgencyRequest(BaseModel):
    email: EmailStr
    password: str
    membership_id: UUID


class TokenResponse(BaseModel):
    access_token: str
    principal: "MeResponse"


class MeResponse(BaseModel):
    membership_id: UUID
    user_id: UUID
    agency_id: UUID
    agency_name: str
    role: Role
    client_id: UUID | None
    full_name: str
    email: str


class AcceptInviteRequest(BaseModel):
    token: str
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class InvitePreview(BaseModel):
    agency_name: str
    email: str
    role: Role
    expires_at: datetime


class AcceptInviteResponse(BaseModel):
    agency_id: UUID
    created_user: bool
    already_had_account: bool
    access_token: str


# --- clients ----------------------------------------------------------------
class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr | None = None


class ClientOut(BaseModel):
    id: UUID
    name: str
    contact_email: str | None
    project_count: int = 0


# --- projects ---------------------------------------------------------------
class ProjectCreate(BaseModel):
    client_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: ProjectStatus | None = None


class ProjectOut(BaseModel):
    id: UUID
    name: str
    description: str
    status: ProjectStatus
    client_id: UUID
    client_name: str
    created_at: datetime


class ProjectMemberOut(BaseModel):
    membership_id: UUID
    full_name: str
    email: str
    role: Role
    added_at: datetime


class AgencyStaffOut(BaseModel):
    """A candidate for project membership. No `added_at` -- they are not on a
    project yet, which is the whole point of listing them."""

    membership_id: UUID
    full_name: str
    email: str
    role: Role


class ProjectMemberAdd(BaseModel):
    membership_id: UUID


class RemovalResult(BaseModel):
    removed_membership_id: UUID
    unassigned_task_ids: list[UUID]
    detail: str


# --- tasks ------------------------------------------------------------------
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    visibility: Visibility = "internal"
    assignee_membership_id: UUID | None = None
    due_date: date | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    visibility: Visibility | None = None
    assignee_membership_id: UUID | None = None
    due_date: date | None = None
    # `assignee_membership_id: None` is ambiguous in JSON -- this makes
    # "unassign" an explicit instruction rather than a missing field.
    clear_assignee: bool = False
    clear_due_date: bool = False


class TaskOut(BaseModel):
    id: UUID
    project_id: UUID
    project_name: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    visibility: Visibility
    assignee_membership_id: UUID | None
    assignee_name: str | None
    due_date: date | None
    created_at: datetime
    updated_at: datetime
    comment_count: int = 0
    file_count: int = 0
    minutes_logged: int = 0


# --- comments ---------------------------------------------------------------
class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    # Ignored for clients: their comments are always client-visible.
    visibility: Visibility = "internal"


class CommentOut(BaseModel):
    id: UUID
    task_id: UUID
    body: str
    visibility: Visibility
    author_name: str | None
    author_role: Role | None
    created_at: datetime


# --- time -------------------------------------------------------------------
class TimeEntryCreate(BaseModel):
    minutes: int = Field(gt=0, le=24 * 60)
    note: str = ""
    entry_date: date | None = None


class TimeEntryOut(BaseModel):
    id: UUID
    task_id: UUID
    minutes: int
    note: str
    entry_date: date
    member_name: str | None
    created_at: datetime


# --- files ------------------------------------------------------------------
class FileOut(BaseModel):
    id: UUID
    task_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    visibility: Visibility
    approval_status: ApprovalStatus
    approval_note: str | None
    approved_by_name: str | None
    approved_at: datetime | None
    uploaded_by_name: str | None
    created_at: datetime


class FileApproval(BaseModel):
    approval_status: Literal["approved", "needs_changes"]
    note: str | None = Field(default=None, max_length=2000)


# --- invites ----------------------------------------------------------------
class InviteCreate(BaseModel):
    email: EmailStr
    role: Role
    client_id: UUID | None = None


class InviteOut(BaseModel):
    id: UUID
    email: str
    role: Role
    client_id: UUID | None
    status: Literal["pending", "accepted", "revoked"]
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None
    # Dev-only convenience: there is no mail server in this take-home, so the
    # link is returned to the admin who created it.
    invite_url: str | None = None
    resent: bool = False


# --- dashboard / search -----------------------------------------------------
class StatusCount(BaseModel):
    status: TaskStatus
    count: int


class ProjectDashboard(BaseModel):
    project_id: UUID
    project_name: str
    client_name: str
    viewer_role: Role
    # Every number below is computed through the same policies that filter the
    # lists, so a client's totals describe the client's slice of the project.
    tasks_by_status: list[StatusCount]
    total_tasks: int
    open_tasks: int
    overdue_tasks: int
    minutes_logged: int
    files_awaiting_approval: int
    scope_note: str


class SearchHit(BaseModel):
    kind: Literal["task", "comment", "file"]
    task_id: UUID
    project_id: UUID
    project_name: str
    title: str
    snippet: str
    visibility: Visibility


TokenResponse.model_rebuild()
