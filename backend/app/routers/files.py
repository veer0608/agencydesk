"""File attachments and the client approval loop."""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.engine import Connection

from .. import repositories as repo
from ..config import settings
from ..deps import Principal, get_conn, get_principal, require_staff
from ..errors import BadRequest, Forbidden, NotFound
from ..schemas import FileApproval, FileOut, Visibility

router = APIRouter(prefix="/api", tags=["files"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _storage_dir() -> Path:
    path = Path(settings.upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/tasks/{task_id}/files", response_model=list[FileOut])
def list_files(
    task_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> list[FileOut]:
    if repo.get_task(conn, task_id) is None:
        raise NotFound("Task")
    return [FileOut(**f) for f in repo.list_files(conn, task_id)]


@router.post("/tasks/{task_id}/files", response_model=FileOut, status_code=201)
async def upload_file(
    task_id: UUID,
    upload: UploadFile = File(...),
    visibility: Visibility = Form("internal"),
    principal: Principal = Depends(require_staff),
    conn: Connection = Depends(get_conn),
) -> FileOut:
    """Only agency staff upload. Files carry the same internal/client flag as
    tasks and comments, and a client-visible file may only hang off a
    client-visible task (enforced by the files write policy)."""
    task = repo.get_task(conn, task_id)
    if task is None:
        raise NotFound("Task")
    if visibility == "client" and task["visibility"] != "client":
        raise BadRequest("Cannot attach a client-visible file to an internal task")

    payload = await upload.read()
    if len(payload) > settings.max_upload_bytes:
        raise BadRequest(f"File exceeds {settings.max_upload_bytes // (1024 * 1024)}MB limit")

    # Never trust the client-supplied name on disk; keep it only as a label.
    clean = _SAFE_NAME.sub("_", (upload.filename or "upload").strip())[:120] or "upload"
    storage_key = f"{secrets.token_hex(16)}_{clean}"
    (_storage_dir() / storage_key).write_bytes(payload)

    created = repo.create_file(
        conn,
        agency_id=principal.agency_id,
        task_id=task_id,
        uploaded_by=principal.membership_id,
        filename=upload.filename or clean,
        content_type=upload.content_type or "application/octet-stream",
        size_bytes=len(payload),
        storage_key=storage_key,
        visibility=visibility,
    )
    if created is None:
        raise NotFound("Task")
    return FileOut(**created)


@router.get("/files/{file_id}/download")
def download_file(
    file_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> FileResponse:
    """The blob is fetched by primary key, but only after the row survives the
    same policies as the listing. An internal file's id is useless to a client:
    the SELECT returns nothing and they get a 404, exactly as if it never
    existed."""
    record = repo.get_file_storage(conn, file_id)
    if record is None:
        raise NotFound("File")

    path = _storage_dir() / record["storage_key"]
    if not path.exists():
        raise NotFound("File contents")
    return FileResponse(path, media_type=record["content_type"], filename=record["filename"])


@router.patch("/files/{file_id}/approval", response_model=FileOut)
def set_approval(
    file_id: UUID,
    payload: FileApproval,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
) -> FileOut:
    """Approve / request changes.

    Clients are the intended users. Admins may also record a decision (agencies
    do get approvals over the phone), but an agency_member cannot -- sign-off is
    not theirs to give.
    """
    existing = repo.get_file(conn, file_id)
    if existing is None:
        raise NotFound("File")
    if not (principal.is_client or principal.is_admin):
        raise Forbidden("Only the client or an agency admin can record an approval decision")
    if existing["visibility"] != "client":
        # Unreachable for a client (they cannot see the row at all); this is the
        # guard for an admin trying it on an internal file, which the
        # files_internal_cannot_be_decided CHECK would reject anyway.
        raise BadRequest("Internal files cannot carry a client approval decision")

    updated = repo.set_file_approval(
        conn,
        file_id=file_id,
        status=payload.approval_status,
        note=payload.note,
        decided_by=principal.membership_id,
    )
    if updated is None:
        raise NotFound("File")
    return FileOut(**updated)
