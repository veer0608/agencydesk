"""Core schema.

Revision ID: 0001_schema
Revises:
"""

from alembic import op

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = r"""
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------------
-- Enumerated domains. Using real enum types (rather than free text) means an
-- invalid role or visibility is rejected by the database, not by a validator
-- someone can forget to call.
-- ---------------------------------------------------------------------------
CREATE TYPE membership_role   AS ENUM ('agency_admin', 'agency_member', 'client_user');
CREATE TYPE membership_status AS ENUM ('active', 'removed');
CREATE TYPE visibility        AS ENUM ('internal', 'client');
CREATE TYPE project_status    AS ENUM ('active', 'on_hold', 'completed', 'archived');
CREATE TYPE task_status       AS ENUM ('todo', 'in_progress', 'blocked', 'review', 'done');
CREATE TYPE task_priority     AS ENUM ('low', 'medium', 'high', 'urgent');
CREATE TYPE approval_status   AS ENUM ('pending', 'approved', 'needs_changes');
CREATE TYPE invite_status     AS ENUM ('pending', 'accepted', 'revoked');


-- ---------------------------------------------------------------------------
-- Tenants
-- ---------------------------------------------------------------------------
CREATE TABLE agencies (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    slug       text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agencies_name_not_blank CHECK (length(btrim(name)) > 0)
);


-- ---------------------------------------------------------------------------
-- Identity.
--
-- `users` is deliberately GLOBAL and carries no agency_id. A human being is one
-- row here forever, no matter how many agencies they work with. Everything
-- tenant-scoped hangs off `memberships` instead. This is what makes "the same
-- email is a client at Agency A and an admin at Agency B" a normal case rather
-- than a conflict.
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL,
    password_hash text NOT NULL,
    full_name     text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_email_not_blank CHECK (length(btrim(email)) > 0)
);

-- Case-insensitive uniqueness without requiring the citext extension.
CREATE UNIQUE INDEX users_email_key ON users (lower(email));


CREATE TABLE clients (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id     uuid NOT NULL REFERENCES agencies (id) ON DELETE CASCADE,
    name          text NOT NULL,
    contact_email text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT clients_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT clients_name_unique_per_agency UNIQUE (agency_id, name),
    -- Referenced by the composite foreign keys below; this is the anchor that
    -- lets a child row prove it belongs to the same tenant as its parent.
    CONSTRAINT clients_id_agency_key UNIQUE (id, agency_id)
);


-- A user's identity *within one agency*. One row per (person, agency).
CREATE TABLE memberships (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    agency_id  uuid NOT NULL REFERENCES agencies (id) ON DELETE CASCADE,
    role       membership_role NOT NULL,
    client_id  uuid,
    status     membership_status NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT memberships_one_per_agency UNIQUE (user_id, agency_id),
    CONSTRAINT memberships_id_agency_key  UNIQUE (id, agency_id),
    -- Looks redundant next to the PK, but it is the target of the composite FKs
    -- in project_members and time_entries: those tables carry a copy of the role
    -- so that "only staff may be a project member / log time" is a database
    -- constraint. ON UPDATE CASCADE keeps the copies honest if a role changes.
    CONSTRAINT memberships_id_agency_role_key UNIQUE (id, agency_id, role),

    -- A client contact must point at a client; agency staff must not.
    CONSTRAINT memberships_client_iff_client_user
        CHECK ((role = 'client_user') = (client_id IS NOT NULL)),
    -- ...and that client must live in the same agency as the membership.
    CONSTRAINT memberships_client_same_agency
        FOREIGN KEY (client_id, agency_id) REFERENCES clients (id, agency_id) ON DELETE RESTRICT
);

CREATE INDEX memberships_user_idx   ON memberships (user_id);
CREATE INDEX memberships_agency_idx ON memberships (agency_id);


-- ---------------------------------------------------------------------------
-- Work
-- ---------------------------------------------------------------------------
CREATE TABLE projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id   uuid NOT NULL REFERENCES agencies (id) ON DELETE CASCADE,
    client_id   uuid NOT NULL,
    name        text NOT NULL,
    description text NOT NULL DEFAULT '',
    status      project_status NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT projects_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT projects_id_agency_key  UNIQUE (id, agency_id),
    -- A project cannot be attached to another tenant's client. Not "should not"
    -- -- cannot: the pair (client_id, agency_id) has to exist in clients.
    CONSTRAINT projects_client_same_agency
        FOREIGN KEY (client_id, agency_id) REFERENCES clients (id, agency_id) ON DELETE CASCADE
);

CREATE INDEX projects_agency_client_idx ON projects (agency_id, client_id);


CREATE TABLE project_members (
    project_id    uuid NOT NULL,
    membership_id uuid NOT NULL,
    agency_id     uuid NOT NULL,
    member_role   membership_role NOT NULL,
    added_at      timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (project_id, membership_id),
    CONSTRAINT project_members_project_same_agency
        FOREIGN KEY (project_id, agency_id) REFERENCES projects (id, agency_id) ON DELETE CASCADE,
    CONSTRAINT project_members_membership_same_agency
        FOREIGN KEY (membership_id, agency_id, member_role)
        REFERENCES memberships (id, agency_id, role) ON UPDATE CASCADE ON DELETE CASCADE,
    -- Clients are never project members; they reach projects through their
    -- client record instead. Enforced structurally via the role copy above.
    CONSTRAINT project_members_staff_only
        CHECK (member_role IN ('agency_admin', 'agency_member'))
);

CREATE INDEX project_members_membership_idx ON project_members (membership_id);


CREATE TABLE tasks (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id                uuid NOT NULL,
    project_id               uuid NOT NULL,
    title                    text NOT NULL,
    description              text NOT NULL DEFAULT '',
    status                   task_status NOT NULL DEFAULT 'todo',
    priority                 task_priority NOT NULL DEFAULT 'medium',
    visibility               visibility NOT NULL DEFAULT 'internal',
    assignee_membership_id   uuid,
    due_date                 date,
    created_by_membership_id uuid,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT tasks_title_not_blank CHECK (length(btrim(title)) > 0),
    CONSTRAINT tasks_id_agency_key   UNIQUE (id, agency_id),
    CONSTRAINT tasks_project_same_agency
        FOREIGN KEY (project_id, agency_id) REFERENCES projects (id, agency_id) ON DELETE CASCADE,

    -- The assignee must be a member OF THIS PROJECT, and when that membership is
    -- removed from the project the assignment is dropped by the database in the
    -- same statement. PostgreSQL 15+ lets SET NULL name a subset of columns,
    -- so project_id (NOT NULL) survives while the assignee is cleared.
    -- MATCH SIMPLE means an unassigned task (NULL assignee) skips the check.
    CONSTRAINT tasks_assignee_is_project_member
        FOREIGN KEY (project_id, assignee_membership_id)
        REFERENCES project_members (project_id, membership_id)
        ON UPDATE CASCADE
        ON DELETE SET NULL (assignee_membership_id),

    CONSTRAINT tasks_creator_same_agency
        FOREIGN KEY (created_by_membership_id, agency_id)
        REFERENCES memberships (id, agency_id)
        ON DELETE SET NULL (created_by_membership_id)
);

CREATE INDEX tasks_project_status_idx ON tasks (project_id, status);
CREATE INDEX tasks_agency_idx         ON tasks (agency_id);
CREATE INDEX tasks_assignee_idx       ON tasks (assignee_membership_id);
CREATE INDEX tasks_title_lower_idx    ON tasks (lower(title));


CREATE TABLE comments (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id            uuid NOT NULL,
    task_id              uuid NOT NULL,
    author_membership_id uuid,
    body                 text NOT NULL,
    visibility           visibility NOT NULL DEFAULT 'internal',
    created_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT comments_body_not_blank CHECK (length(btrim(body)) > 0),
    CONSTRAINT comments_task_same_agency
        FOREIGN KEY (task_id, agency_id) REFERENCES tasks (id, agency_id) ON DELETE CASCADE,
    CONSTRAINT comments_author_same_agency
        FOREIGN KEY (author_membership_id, agency_id)
        REFERENCES memberships (id, agency_id)
        ON DELETE SET NULL (author_membership_id)
);

CREATE INDEX comments_task_idx ON comments (task_id, created_at);


CREATE TABLE files (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id                 uuid NOT NULL,
    task_id                   uuid NOT NULL,
    uploaded_by_membership_id uuid,
    filename                  text NOT NULL,
    content_type              text NOT NULL DEFAULT 'application/octet-stream',
    size_bytes                bigint NOT NULL,
    storage_key               text NOT NULL UNIQUE,
    visibility                visibility NOT NULL DEFAULT 'internal',
    approval_status           approval_status NOT NULL DEFAULT 'pending',
    approval_note             text,
    approved_by_membership_id uuid,
    approved_at               timestamptz,
    created_at                timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT files_filename_not_blank CHECK (length(btrim(filename)) > 0),
    CONSTRAINT files_size_non_negative  CHECK (size_bytes >= 0),
    CONSTRAINT files_task_same_agency
        FOREIGN KEY (task_id, agency_id) REFERENCES tasks (id, agency_id) ON DELETE CASCADE,
    CONSTRAINT files_uploader_same_agency
        FOREIGN KEY (uploaded_by_membership_id, agency_id)
        REFERENCES memberships (id, agency_id)
        ON DELETE SET NULL (uploaded_by_membership_id),
    CONSTRAINT files_approver_same_agency
        FOREIGN KEY (approved_by_membership_id, agency_id)
        REFERENCES memberships (id, agency_id)
        ON DELETE SET NULL (approved_by_membership_id),

    -- A decision and its audit trail move together.
    CONSTRAINT files_decision_fields_consistent
        CHECK ((approval_status = 'pending') = (approved_at IS NULL)),
    -- An internal file can never carry a client approval decision: if a client
    -- was never allowed to see it, it cannot have been approved by one.
    CONSTRAINT files_internal_cannot_be_decided
        CHECK (approval_status = 'pending' OR visibility = 'client')
);

CREATE INDEX files_task_idx ON files (task_id, created_at);


CREATE TABLE time_entries (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id     uuid NOT NULL,
    task_id       uuid NOT NULL,
    membership_id uuid NOT NULL,
    member_role   membership_role NOT NULL,
    minutes       integer NOT NULL,
    note          text NOT NULL DEFAULT '',
    entry_date    date NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT time_entries_minutes_sane CHECK (minutes > 0 AND minutes <= 24 * 60),
    CONSTRAINT time_entries_task_same_agency
        FOREIGN KEY (task_id, agency_id) REFERENCES tasks (id, agency_id) ON DELETE CASCADE,
    CONSTRAINT time_entries_member_same_agency
        FOREIGN KEY (membership_id, agency_id, member_role)
        REFERENCES memberships (id, agency_id, role) ON UPDATE CASCADE ON DELETE RESTRICT,
    -- Clients do not log time. Same role-copy trick as project_members.
    CONSTRAINT time_entries_staff_only
        CHECK (member_role IN ('agency_admin', 'agency_member'))
);

CREATE INDEX time_entries_task_idx        ON time_entries (task_id);
CREATE INDEX time_entries_agency_date_idx ON time_entries (agency_id, entry_date);


-- ---------------------------------------------------------------------------
-- Invitations
-- ---------------------------------------------------------------------------
CREATE TABLE invites (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id                uuid NOT NULL REFERENCES agencies (id) ON DELETE CASCADE,
    email                    text NOT NULL,
    role                     membership_role NOT NULL,
    client_id                uuid,
    token_hash               text NOT NULL UNIQUE,
    invited_by_membership_id uuid,
    status                   invite_status NOT NULL DEFAULT 'pending',
    expires_at               timestamptz NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    accepted_at              timestamptz,
    accepted_membership_id   uuid,

    CONSTRAINT invites_email_not_blank CHECK (length(btrim(email)) > 0),
    CONSTRAINT invites_client_iff_client_user
        CHECK ((role = 'client_user') = (client_id IS NOT NULL)),
    CONSTRAINT invites_client_same_agency
        FOREIGN KEY (client_id, agency_id) REFERENCES clients (id, agency_id) ON DELETE CASCADE,
    CONSTRAINT invites_inviter_same_agency
        FOREIGN KEY (invited_by_membership_id, agency_id)
        REFERENCES memberships (id, agency_id)
        ON DELETE SET NULL (invited_by_membership_id),
    CONSTRAINT invites_accepted_membership_same_agency
        FOREIGN KEY (accepted_membership_id, agency_id)
        REFERENCES memberships (id, agency_id)
        ON DELETE SET NULL (accepted_membership_id),
    CONSTRAINT invites_accepted_fields_consistent
        CHECK ((status = 'accepted') = (accepted_at IS NOT NULL))
);

-- At most ONE outstanding invite per (agency, email). Resending is therefore an
-- UPDATE of the existing row -- the database will not let a duplicate exist,
-- even under two simultaneous "resend" clicks. Historical accepted/revoked rows
-- are unaffected because the index is partial.
CREATE UNIQUE INDEX invites_one_pending_per_email
    ON invites (agency_id, lower(email)) WHERE status = 'pending';

CREATE INDEX invites_agency_idx ON invites (agency_id, status);


CREATE TABLE audit_log (
    id                  bigserial PRIMARY KEY,
    agency_id           uuid NOT NULL REFERENCES agencies (id) ON DELETE CASCADE,
    actor_membership_id uuid,
    action              text NOT NULL,
    entity_type         text NOT NULL,
    entity_id           uuid,
    detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_agency_idx ON audit_log (agency_id, created_at DESC);
"""


DROP = r"""
DROP TABLE IF EXISTS audit_log, invites, time_entries, files, comments, tasks,
                     project_members, projects, memberships, clients, users, agencies CASCADE;
DROP TYPE IF EXISTS invite_status, approval_status, task_priority, task_status,
                    project_status, visibility, membership_status, membership_role;
DROP SCHEMA IF EXISTS app CASCADE;
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(DROP)
