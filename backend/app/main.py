from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError, IntegrityError

from .config import settings
from .routers import auth, files, invites, projects, tasks

log = logging.getLogger("agencydesk")

app = FastAPI(
    title="AgencyDesk",
    version="1.0.0",
    description=(
        "Multi-tenant client & project management. Tenant isolation and "
        "internal/client visibility are enforced by PostgreSQL row-level "
        "security, not by application code."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(files.router)
app.include_router(invites.router)


@app.exception_handler(IntegrityError)
def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    """Constraint violations are part of the design, so they get real messages.

    A cross-tenant write that slips past the handlers dies here on a composite
    foreign key -- reported as a 404, because from the caller's point of view the
    parent row they referenced does not exist.
    """
    orig = getattr(exc, "orig", None)
    log.warning("integrity error on %s %s: %s", request.method, request.url.path, orig)

    if isinstance(orig, pg_errors.ForeignKeyViolation):
        return JSONResponse(status_code=404, content={"detail": "Referenced resource not found"})
    if isinstance(orig, pg_errors.UniqueViolation):
        return JSONResponse(status_code=409, content={"detail": "That already exists"})
    if isinstance(orig, pg_errors.CheckViolation):
        return JSONResponse(status_code=400, content={"detail": "Request violates a data rule"})
    return JSONResponse(status_code=400, content={"detail": "Request could not be applied"})


@app.exception_handler(DBAPIError)
def handle_dbapi_error(request: Request, exc: DBAPIError) -> JSONResponse:
    """RLS rejections and the file-approval trigger surface here.

    A WITH CHECK failure means the caller tried to write a row they are not
    allowed to own. We answer 403 rather than 500: the request was understood and
    refused.
    """
    orig = getattr(exc, "orig", None)
    log.warning("database error on %s %s: %s", request.method, request.url.path, orig)

    if isinstance(orig, pg_errors.InsufficientPrivilege):
        return JSONResponse(
            status_code=403,
            content={"detail": "Not permitted for your role in this agency"},
        )
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
