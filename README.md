# AgencyDesk

[![docker compose up](https://github.com/veer0608/agencydesk/actions/workflows/ci.yml/badge.svg)](https://github.com/veer0608/agencydesk/actions/workflows/ci.yml)

Multi-tenant client & project management for agencies. One deployment, many
agencies, each with its own clients, projects and client portal.

That badge is the setup instructions below, run end to end on a clean machine:
build, migrate, seed, 70 tests, and the isolation proof — inside the containers.

Tenant isolation and internal/client visibility are enforced by **PostgreSQL
row-level security**, not by application code. See [DESIGN.md](DESIGN.md) for the
reasoning; this file is just how to run it.

**Stack:** React + TypeScript · Python (FastAPI) · PostgreSQL 16

---

## Run it

Requires Docker.

```bash
git clone <this repo> && cd agencydesk
docker compose up --build
```

That is the whole setup. On first boot the stack creates the database roles,
applies migrations, and seeds two agencies. Takes about two minutes.

| | |
|---|---|
| App | <http://localhost:5173> |
| API docs | <http://localhost:8000/docs> |
| Postgres | `localhost:5433` |

### Sign in

Every account uses the password `password123`.

| Email | Agency | Role | What they see |
|---|---|---|---|
| `ada@northwind.test` | Northwind Studio | agency_admin | Everything at Northwind |
| `ben@northwind.test` | Northwind Studio | agency_member | Harbor Foods Rebrand only |
| `cleo@northwind.test` | Northwind Studio | agency_member | Veldt Catalog only |
| `mia@harborfoods.test` | **both** | client_user / agency_admin | See below |
| `raj@bluepeak.test` | Bluepeak Digital | agency_admin | Everything at Bluepeak |

**Start with `mia@harborfoods.test`.** She is a client contact at Northwind *and*
an agency admin at Bluepeak, under one email — so she gets the agency picker, and
the two sessions see completely different systems.

---

## Prove it

Two ways, both against the running stack.

**The readable one** — walks the five edge cases and prints what it tried, what
came back, and whether that was correct:

```bash
docker compose exec api python -m scripts.prove_isolation
```

**The test suite** — 70 tests against a real Postgres with the real policies, the
API connecting as the real unprivileged role:

```bash
docker compose exec api pytest
```

```
tests/test_client_visibility.py ...............  [ 21%]
tests/test_files.py             ........         [ 32%]
tests/test_identity.py          .........        [ 45%]
tests/test_invites.py           ...........      [ 61%]
tests/test_member_removal.py    .....            [ 68%]
tests/test_tenant_isolation.py  ......................  [100%]
70 passed
```

The isolation sweep walks the app's **own route table** rather than a hand-written
list of URLs, firing every id-bearing endpoint at another tenant's ids. A new
endpoint is covered the moment it is registered.

---

## See it yourself, in about two minutes

1. Sign in as **ada@northwind.test** → *Harbor Foods Rebrand*. Six tasks. Three
   are badged `internal`, including "Renegotiate print margin with supplier".
2. Open **Moodboard round 2**. Three comments, one of them internal: *"cap this
   at one more round, we are already over budget."*
3. Check **Dashboard**: 6 tasks, 15.5h logged.
4. Sign out. Sign in as **mia@harborfoods.test** → choose **Northwind Studio**.
5. Same project. **Three** tasks. The margin negotiation is gone — and so is
   Veldt Spring Catalog, another client's project, from the sidebar entirely.
   She does keep **both** Harbor Foods projects, though: scoping is per client,
   not per project. Open *Harbor Foods Q4 Campaign* and the split is the same —
   the agency sees 5 tasks / 8.9h, Harbor sees 3 tasks / 7.8h.
6. Open **Moodboard round 2** again. **Two** comments. The budget note is not
   there.
7. **Dashboard**: 3 tasks, 11.0h. The hours logged against internal tasks are not
   merely hidden — they were never counted.
8. **Search** for `margin`. Nothing. Now search `moodboard`. Results. Same
   endpoint, same tables.
9. Use **Acting as** in the sidebar to switch to **Bluepeak Digital**. Same
   person, now an admin, looking at a completely different agency's work.

---

## Layout

```
backend/
  alembic/versions/
    0001_schema.py     tables, composite FKs, constraints
    0002_rls.py        context helpers, policies, triggers, grants   <- the interesting one
  app/
    db.py              one request = one transaction = one tenant context
    deps.py            principal resolution (role re-read per request)
    repositories.py    every query, in plain SQL
    routers/           auth · projects · tasks · files · invites
    seed.py            two agencies, run as the owner role
  scripts/
    prove_isolation.py the readable proof
    bench_queries.py   query-shape benchmark (loads, measures, rolls back)
  tests/               70 tests, one file per edge case
frontend/src/
  api.ts               typed client
  pages/               Login · Board · TaskDrawer · Dashboard · Team · Search
                       NewProject · AcceptInvite
infra/initdb/          creates the unprivileged app role
```

### Endpoints

`POST /api/auth/login` · `/select-agency` · `/switch-agency/{id}` · `GET /me` ·
`/my-agencies` · `POST /accept-invite`
`GET|POST /api/clients` · `/api/projects` · `GET /api/projects/{id}/dashboard`
`GET /api/agency/staff` · `GET|POST /api/projects/{id}/members` · `DELETE …/members/{id}`
`GET|POST /api/projects/{id}/tasks` · `GET|PATCH /api/tasks/{id}`
`GET|POST /api/tasks/{id}/comments` · `/time-entries` · `/files`
`GET /api/files/{id}/download` · `PATCH /api/files/{id}/approval`
`GET /api/search` · `GET|POST /api/invites` · `DELETE /api/invites/{id}`

---

## Running without Docker

```bash
# Postgres 16 on :5433, then:
cd backend
pip install -r requirements.txt
psql -f ../infra/initdb/00-roles-and-test-db.sql
export OWNER_DATABASE_URL=postgresql+psycopg://agencydesk_owner:owner_pw@localhost:5433/agencydesk
export DATABASE_URL=postgresql+psycopg://agencydesk_app:app_pw@localhost:5433/agencydesk
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload

cd ../frontend && npm install && npm run dev
```

The frontend proxies `/api` to `http://127.0.0.1:8000` by default, so this path
needs no extra configuration. (Under Docker the API is a sibling container, which
is why `docker-compose.yml` sets `VITE_API_TARGET=http://api:8000`.)

The two URLs are not interchangeable. Migrations and the seed run as the owner;
the API must run as `agencydesk_app`, which cannot bypass row-level security.
Pointing the API at the owner URL would disable every guarantee in this repo.

## Scope

### Required

| | Where |
|---|---|
| Projects belong to a client | `projects.client_id`, composite FK to `clients(id, agency_id)` |
| Tasks: status, priority, assignee, due date | `tasks` table; all four editable in the task drawer |
| Task marked internal or client-visible | `visibility` enum on tasks, comments and files |
| Clients log in separately, see only their own projects and client-visible tasks | `client_user` role; `projects_read` / `tasks_read` policies |
| Clients can comment; cannot create tasks or change status | `comments_write` allows them, `tasks_write` requires `app.is_staff()` |
| Agency users log time (duration, note, date) | `time_entries`; `time_entries_staff_only` CHECK keeps clients out |
| Per-project hours summary | project dashboard |
| Files attached to a task, same internal/client flag | `files.visibility` |
| Client marks a file approved / needs changes | `PATCH /api/files/{id}/approval` + column-guard trigger |
| Dashboard: task counts by status and hours, scoped to viewer | same SQL, two RLS contexts |
| Every agency an isolated tenant | composite FKs + RLS, `tests/test_tenant_isolation.py` |
| Three roles | `membership_role` enum |

### Edge cases

Each has its own test file section: cross-tenant access, internal content
leaking (incl. search, filters and comments), one person at two agencies, invite
races, and removing a member mid-task.

### Not built

**Bonus, skipped:** client intake forms; automations & notifications.
**Discussed only:** timeline / Gantt — see the end of DESIGN.md.
**Out of scope:** CRM.
