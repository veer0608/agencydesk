"""Row-level security: request context, policies, grants.

Revision ID: 0002_rls
Revises: 0001_schema
"""

from alembic import op

revision = "0002_rls"
down_revision = "0001_schema"
branch_labels = None
depends_on = None


CONTEXT_FUNCTIONS = r"""
-- ---------------------------------------------------------------------------
-- Request context.
--
-- Every API request opens one transaction and does:
--     SELECT set_config('app.membership_id', '<uuid>', true);
--
-- `true` = SET LOCAL, so the value dies with the transaction and can never leak
-- into the next request that borrows the same pooled connection.
--
-- The resolvers below are SECURITY DEFINER (they run as agencydesk_owner, which
-- bypasses RLS) for one specific reason: they read `memberships`, and
-- `memberships` is itself protected by a policy that calls them. Running them as
-- the owner breaks that cycle. They are safe because each one is keyed strictly
-- by the caller's own membership id -- there is no parameter an attacker can
-- steer.
-- ---------------------------------------------------------------------------

CREATE FUNCTION app.membership_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.membership_id', true), '')::uuid;
$$;

-- Set immediately after a password check, before an agency has been chosen.
-- It is what lets the agency picker list "which agencies is this person in?".
CREATE FUNCTION app.request_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::uuid;
$$;

CREATE FUNCTION app.current_agency_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT m.agency_id FROM memberships m
    WHERE m.id = app.membership_id() AND m.status = 'active';
$$;

CREATE FUNCTION app.actor_role() RETURNS membership_role
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT m.role FROM memberships m
    WHERE m.id = app.membership_id() AND m.status = 'active';
$$;

CREATE FUNCTION app.current_client_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT m.client_id FROM memberships m
    WHERE m.id = app.membership_id() AND m.status = 'active';
$$;

-- Projects the caller has been explicitly added to. SECURITY DEFINER so that
-- the projects policy can consult project_members without the two policies
-- recursing into each other.
CREATE FUNCTION app.my_project_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT pm.project_id FROM project_members pm
    WHERE pm.membership_id = app.membership_id();
$$;

CREATE FUNCTION app.is_staff() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(app.actor_role() IN ('agency_admin', 'agency_member'), false);
$$;

CREATE FUNCTION app.is_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(app.actor_role() = 'agency_admin', false);
$$;

CREATE FUNCTION app.is_client() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(app.actor_role() = 'client_user', false);
$$;

-- Minimal-disclosure name lookup. A client may learn the name of a person whose
-- comment they are allowed to read, without being handed the agency's roster.
CREATE FUNCTION app.display_name(p_membership_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT u.full_name
    FROM memberships m JOIN users u ON u.id = m.user_id
    WHERE m.id = p_membership_id
      AND m.agency_id = app.current_agency_id();
$$;
"""


POLICIES = r"""
-- ---------------------------------------------------------------------------
-- Policies.
--
-- Shape used throughout:
--   <table>_read  FOR SELECT  -- who may see the row
--   <table>_write FOR ALL     -- who may create/change/delete it
--
-- Multiple permissive policies are OR-ed, so the FOR ALL policy widens SELECT
-- for writers, while INSERT/UPDATE/DELETE only ever consider the write policy.
--
-- Child tables (tasks, comments, files, time_entries) intentionally express
-- their scope as `EXISTS (SELECT 1 FROM <parent> ...)`. That subquery is itself
-- subject to the parent's policy, so visibility composes: hide a project and
-- everything beneath it disappears from every query, including aggregates,
-- searches and filters that nobody remembered to audit.
-- ---------------------------------------------------------------------------

ALTER TABLE agencies        ENABLE ROW LEVEL SECURITY;
ALTER TABLE users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients         ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships     ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects        ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE comments        ENABLE ROW LEVEL SECURITY;
ALTER TABLE files           ENABLE ROW LEVEL SECURITY;
ALTER TABLE time_entries    ENABLE ROW LEVEL SECURITY;
ALTER TABLE invites         ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log       ENABLE ROW LEVEL SECURITY;


-- agencies -------------------------------------------------------------------
-- Two ways in. Normally you see the agency you are currently acting in. The
-- second arm covers the agency picker, which by definition runs *before* an
-- agency has been chosen: at that moment only `app.user_id` is set, and a person
-- is entitled to learn the names of the agencies they belong to.
CREATE POLICY agencies_read ON agencies FOR SELECT
    USING (
        id = app.current_agency_id()
        OR EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.agency_id = agencies.id
              AND m.user_id = app.request_user_id()
              AND m.status = 'active'
        )
    );


-- users ----------------------------------------------------------------------
-- Global table, but not globally readable: you may see yourself, and people who
-- share your current agency. Login happens before any context exists and so
-- goes through app.lookup_login() instead.
CREATE POLICY users_read ON users FOR SELECT
    USING (
        id = app.request_user_id()
        OR EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = users.id
              AND m.agency_id = app.current_agency_id()
        )
    );

CREATE POLICY users_self_update ON users FOR UPDATE
    USING (id = app.request_user_id())
    WITH CHECK (id = app.request_user_id());


-- memberships ----------------------------------------------------------------
CREATE POLICY memberships_read ON memberships FOR SELECT
    USING (
        -- my own memberships, for the agency picker
        user_id = app.request_user_id()
        -- or the roster of the agency I am currently acting in (staff only;
        -- clients must never enumerate agency personnel)
        OR (agency_id = app.current_agency_id() AND app.is_staff())
    );

CREATE POLICY memberships_write ON memberships FOR ALL
    USING (agency_id = app.current_agency_id() AND app.is_admin())
    WITH CHECK (agency_id = app.current_agency_id() AND app.is_admin());


-- clients --------------------------------------------------------------------
CREATE POLICY clients_read ON clients FOR SELECT
    USING (
        (
            agency_id = app.current_agency_id()
            AND (
                app.is_admin()
                -- an agency_member sees only clients behind projects they are on
                OR (app.actor_role() = 'agency_member'
                    AND EXISTS (SELECT 1 FROM projects p WHERE p.client_id = clients.id))
                -- a client sees exactly themselves
                OR (app.is_client() AND id = app.current_client_id())
            )
        )
        -- ...plus the picker case: "you are the contact for Harbor Foods" is
        -- something you may be told before you have chosen an agency to act in.
        OR EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.client_id = clients.id
              AND m.user_id = app.request_user_id()
              AND m.status = 'active'
        )
    );

CREATE POLICY clients_write ON clients FOR ALL
    USING (agency_id = app.current_agency_id() AND app.is_admin())
    WITH CHECK (agency_id = app.current_agency_id() AND app.is_admin());


-- projects -------------------------------------------------------------------
CREATE POLICY projects_read ON projects FOR SELECT
    USING (
        agency_id = app.current_agency_id()
        AND (
            app.is_admin()
            OR (app.actor_role() = 'agency_member' AND id IN (SELECT app.my_project_ids()))
            OR (app.is_client() AND client_id = app.current_client_id())
        )
    );

CREATE POLICY projects_write ON projects FOR ALL
    USING (agency_id = app.current_agency_id() AND app.is_admin())
    WITH CHECK (agency_id = app.current_agency_id() AND app.is_admin());


-- project_members ------------------------------------------------------------
CREATE POLICY project_members_read ON project_members FOR SELECT
    USING (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND (app.is_admin() OR project_id IN (SELECT app.my_project_ids()))
    );

CREATE POLICY project_members_write ON project_members FOR ALL
    USING (agency_id = app.current_agency_id() AND app.is_admin())
    WITH CHECK (agency_id = app.current_agency_id() AND app.is_admin());


-- tasks ----------------------------------------------------------------------
CREATE POLICY tasks_read ON tasks FOR SELECT
    USING (
        agency_id = app.current_agency_id()
        AND EXISTS (SELECT 1 FROM projects p WHERE p.id = tasks.project_id)
        AND (app.is_staff() OR visibility = 'client')
    );

-- Clients can never create a task or move one across the board: `app.is_staff()`
-- is false for them, so no INSERT or UPDATE of a task row can ever pass.
CREATE POLICY tasks_write ON tasks FOR ALL
    USING (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND EXISTS (SELECT 1 FROM projects p WHERE p.id = tasks.project_id)
    )
    WITH CHECK (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND EXISTS (SELECT 1 FROM projects p WHERE p.id = tasks.project_id)
    );


-- comments -------------------------------------------------------------------
CREATE POLICY comments_read ON comments FOR SELECT
    USING (
        agency_id = app.current_agency_id()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = comments.task_id)
        AND (app.is_staff() OR visibility = 'client')
    );

CREATE POLICY comments_write ON comments FOR ALL
    USING (
        agency_id = app.current_agency_id()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = comments.task_id)
        AND author_membership_id = app.membership_id()
    )
    WITH CHECK (
        agency_id = app.current_agency_id()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = comments.task_id)
        -- you may only speak as yourself
        AND author_membership_id = app.membership_id()
        -- a client's comment is always client-visible; they have no internal voice
        AND (app.is_staff() OR visibility = 'client')
        -- and staff cannot leave a client-visible comment on an internal task,
        -- which would strand it somewhere the client can never read it
        AND (
            visibility = 'internal'
            OR EXISTS (SELECT 1 FROM tasks t WHERE t.id = comments.task_id AND t.visibility = 'client')
        )
    );


-- files ----------------------------------------------------------------------
CREATE POLICY files_read ON files FOR SELECT
    USING (
        agency_id = app.current_agency_id()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = files.task_id)
        AND (app.is_staff() OR visibility = 'client')
    );

CREATE POLICY files_write ON files FOR ALL
    USING (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = files.task_id)
    )
    WITH CHECK (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = files.task_id)
        AND (visibility = 'internal'
             OR EXISTS (SELECT 1 FROM tasks t WHERE t.id = files.task_id AND t.visibility = 'client'))
    );

-- The one write a client is permitted anywhere in the system: recording an
-- approval decision on a file they can already see. The companion trigger below
-- pins that down to the approval columns only.
CREATE POLICY files_client_approval ON files FOR UPDATE
    USING (
        agency_id = app.current_agency_id()
        AND app.is_client()
        AND visibility = 'client'
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = files.task_id)
    )
    WITH CHECK (
        agency_id = app.current_agency_id()
        AND app.is_client()
        AND visibility = 'client'
    );


-- time_entries ---------------------------------------------------------------
-- No visibility column of its own: a client sees hours logged against
-- client-visible tasks and nothing else, purely by inheriting the task policy.
CREATE POLICY time_entries_read ON time_entries FOR SELECT
    USING (
        agency_id = app.current_agency_id()
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = time_entries.task_id)
    );

CREATE POLICY time_entries_write ON time_entries FOR ALL
    USING (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND (app.is_admin() OR membership_id = app.membership_id())
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = time_entries.task_id)
    )
    WITH CHECK (
        agency_id = app.current_agency_id()
        AND app.is_staff()
        AND (app.is_admin() OR membership_id = app.membership_id())
        AND EXISTS (SELECT 1 FROM tasks t WHERE t.id = time_entries.task_id)
    );


-- invites --------------------------------------------------------------------
-- Admin-only from inside the app. Acceptance happens pre-authentication and so
-- runs through app.accept_invite() rather than these policies.
CREATE POLICY invites_admin ON invites FOR ALL
    USING (agency_id = app.current_agency_id() AND app.is_admin())
    WITH CHECK (agency_id = app.current_agency_id() AND app.is_admin());


-- audit_log ------------------------------------------------------------------
CREATE POLICY audit_read ON audit_log FOR SELECT
    USING (agency_id = app.current_agency_id() AND app.is_admin());

CREATE POLICY audit_append ON audit_log FOR INSERT
    WITH CHECK (agency_id = app.current_agency_id() AND app.is_staff());
"""


TRIGGERS = r"""
-- A client's UPDATE on a file is allowed by files_client_approval, but a policy
-- cannot restrict *which columns* an UPDATE touches. This trigger does.
CREATE FUNCTION app.files_client_update_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF app.is_client() THEN
        IF (NEW.id, NEW.agency_id, NEW.task_id, NEW.filename, NEW.content_type,
            NEW.size_bytes, NEW.storage_key, NEW.visibility, NEW.uploaded_by_membership_id)
           IS DISTINCT FROM
           (OLD.id, OLD.agency_id, OLD.task_id, OLD.filename, OLD.content_type,
            OLD.size_bytes, OLD.storage_key, OLD.visibility, OLD.uploaded_by_membership_id)
        THEN
            RAISE EXCEPTION 'client_user may only change approval fields on a file'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER files_client_update_guard
    BEFORE UPDATE ON files
    FOR EACH ROW EXECUTE FUNCTION app.files_client_update_guard();


-- Keep tasks.updated_at honest without the application having to remember.
CREATE FUNCTION app.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER tasks_touch_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
"""


PREAUTH_FUNCTIONS = r"""
-- ---------------------------------------------------------------------------
-- The only two paths that legitimately run before a tenant context exists.
--
-- Both are SECURITY DEFINER, both are narrow, and both are the reason `users`
-- and `invites` can stay behind RLS the rest of the time. If you want to audit
-- how a request could ever cross the tenant boundary, this is the entire list.
-- ---------------------------------------------------------------------------

CREATE FUNCTION app.lookup_login(p_email text)
RETURNS TABLE (id uuid, password_hash text, full_name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT u.id, u.password_hash, u.full_name
    FROM users u
    WHERE lower(u.email) = lower(btrim(p_email));
$$;


-- Public, unauthenticated preview of an invite so the accept page can say
-- "Northwind Studio invited you as a client contact" without leaking anything
-- beyond what the token holder already knows.
CREATE FUNCTION app.peek_invite(p_token_hash text)
RETURNS TABLE (agency_name text, email text, role membership_role, expires_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT a.name, i.email, i.role, i.expires_at
    FROM invites i JOIN agencies a ON a.id = i.agency_id
    WHERE i.token_hash = p_token_hash
      AND i.status = 'pending'
      AND i.expires_at > now();
$$;


-- Accepting an invite, atomically.
--
-- The first statement is the serialisation point: claiming the invite is an
-- UPDATE guarded by `status = 'pending'`, so if the same link is opened twice
-- (double click, refreshed tab, two browsers) exactly one call finds a row and
-- the other returns NULL. Everything after it is idempotent anyway:
--   * the user is upserted on lower(email), so an existing person keeps their
--     password and simply gains a second membership;
--   * the membership is upserted on (user_id, agency_id), so re-accepting can
--     never mint a second account.
CREATE FUNCTION app.accept_invite(
    p_token_hash    text,
    p_full_name     text,
    p_password_hash text
)
-- Output columns are prefixed `out_` on purpose: with RETURNS TABLE, plpgsql
-- puts those names in scope as variables, and a bare `user_id` / `agency_id`
-- would then collide with the identically-named columns in the statements below
-- (notably ON CONFLICT (user_id, agency_id)).
RETURNS TABLE (out_membership_id uuid, out_user_id uuid, out_agency_id uuid, out_created_user boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_invite     invites%ROWTYPE;
    v_user_id    uuid;
    v_created    boolean := false;
    v_membership uuid;
BEGIN
    UPDATE invites i
       SET status = 'accepted', accepted_at = now()
     WHERE i.token_hash = p_token_hash
       AND i.status = 'pending'
       AND i.expires_at > now()
    RETURNING i.* INTO v_invite;

    IF NOT FOUND THEN
        RETURN;  -- unknown, expired, revoked, or already-accepted token
    END IF;

    SELECT u.id INTO v_user_id FROM users u WHERE lower(u.email) = lower(v_invite.email);

    IF v_user_id IS NULL THEN
        INSERT INTO users (email, password_hash, full_name)
        VALUES (lower(btrim(v_invite.email)), p_password_hash, p_full_name)
        ON CONFLICT (lower(email)) DO NOTHING
        RETURNING id INTO v_user_id;

        IF v_user_id IS NULL THEN
            -- lost a race with a concurrent signup; adopt the existing account
            SELECT u.id INTO v_user_id FROM users u WHERE lower(u.email) = lower(v_invite.email);
        ELSE
            v_created := true;
        END IF;
    END IF;

    INSERT INTO memberships (user_id, agency_id, role, client_id, status)
    VALUES (v_user_id, v_invite.agency_id, v_invite.role, v_invite.client_id, 'active')
    ON CONFLICT (user_id, agency_id)
        DO UPDATE SET status = 'active'
    RETURNING id INTO v_membership;

    UPDATE invites SET accepted_membership_id = v_membership WHERE id = v_invite.id;

    INSERT INTO audit_log (agency_id, actor_membership_id, action, entity_type, entity_id, detail)
    VALUES (v_invite.agency_id, v_membership, 'invite.accepted', 'invite', v_invite.id,
            jsonb_build_object('email', v_invite.email, 'role', v_invite.role,
                               'created_user', v_created));

    RETURN QUERY SELECT v_membership, v_user_id, v_invite.agency_id, v_created;
END;
$$;
"""


GRANTS = r"""
-- The application role gets data access and nothing else: no DDL, no ownership,
-- and therefore no way to disable a policy at runtime.
GRANT USAGE ON SCHEMA public TO agencydesk_app;
GRANT USAGE ON SCHEMA app    TO agencydesk_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO agencydesk_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO agencydesk_app;
GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA app    TO agencydesk_app;

REVOKE ALL ON SCHEMA app FROM PUBLIC;
"""


def upgrade() -> None:
    op.execute(CONTEXT_FUNCTIONS)
    op.execute(POLICIES)
    op.execute(TRIGGERS)
    op.execute(PREAUTH_FUNCTIONS)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS files_client_update_guard ON files;
        DROP TRIGGER IF EXISTS tasks_touch_updated_at ON tasks;
        DROP SCHEMA IF EXISTS app CASCADE;
        CREATE SCHEMA app;
        """
    )
    for table in (
        "agencies",
        "users",
        "clients",
        "memberships",
        "projects",
        "project_members",
        "tasks",
        "comments",
        "files",
        "time_entries",
        "invites",
        "audit_log",
    ):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
