"""Seed data.

Runs as the OWNER role, which is the only role that can write across tenants.
That is itself part of the demonstration: the application role physically cannot
create the fixture below, because half of it belongs to somebody else's agency.

What gets built (deliberately, to make the edge cases visible in the UI):

  Northwind Studio                     Bluepeak Digital
  ----------------                     ----------------
  admin  ada@northwind.test            admin  raj@bluepeak.test
  member ben@northwind.test            member sam@bluepeak.test
  client mia@harborfoods.test          client mia@harborfoods.test   <-- SAME PERSON
                                              (client contact at Northwind,
                                               agency_admin at Bluepeak)

`mia@harborfoods.test` is one row in `users` with two memberships. Logging in as
her shows the agency picker; the same session token cannot see both agencies.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import settings
from .security import hash_invite_token, hash_password

PASSWORD = "password123"

engine = create_engine(
    os.environ.get("OWNER_DATABASE_URL", settings.owner_database_url), future=True
)


def upload_dir() -> Path:
    path = Path(settings.upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def placeholder_pdf(title: str) -> bytes:
    """A small but genuinely well-formed PDF, xref table and all.

    Seeded attachments need to be downloadable and openable -- a reviewer
    clicking one is checking the authorisation path in front of it, and a
    corrupt file makes a working 200 look like a bug.
    """
    label = re.sub(r"[()\\]", "", title)[:60]
    stream = f"BT /F1 12 Tf 24 64 Td (AgencyDesk sample: {label}) Tj ET".encode()

    bodies = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 420 120]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj" + body + b"endobj\n"

    xref_at = len(out)
    out += b"xref\n0 " + str(len(bodies) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer<</Size " + str(len(bodies) + 1).encode() + b"/Root 1 0 R>>\nstartxref\n"
        + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def _exec(conn, sql: str, **params):
    return conn.execute(text(sql), params)


def _scalar(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar()


def already_seeded(conn) -> bool:
    return bool(_scalar(conn, "SELECT count(*) FROM agencies"))


def seed() -> None:
    pw = hash_password(PASSWORD)
    today = date.today()

    with engine.begin() as conn:
        if already_seeded(conn):
            print("Database already contains agencies -- skipping seed.")
            return

        # --- agencies ------------------------------------------------------
        northwind = _scalar(
            conn,
            "INSERT INTO agencies (name, slug) VALUES ('Northwind Studio','northwind') RETURNING id",
        )
        bluepeak = _scalar(
            conn,
            "INSERT INTO agencies (name, slug) VALUES ('Bluepeak Digital','bluepeak') RETURNING id",
        )

        # --- people --------------------------------------------------------
        def new_user(email: str, name: str) -> str:
            return _scalar(
                conn,
                "INSERT INTO users (email, password_hash, full_name) "
                "VALUES (:e, :p, :n) RETURNING id",
                e=email, p=pw, n=name,
            )

        ada = new_user("ada@northwind.test", "Ada Okafor")
        ben = new_user("ben@northwind.test", "Ben Reyes")
        cleo = new_user("cleo@northwind.test", "Cleo Mensah")
        raj = new_user("raj@bluepeak.test", "Raj Patel")
        sam = new_user("sam@bluepeak.test", "Sam Ortiz")
        # The dual-agency person. ONE user row, two memberships, two roles.
        mia = new_user("mia@harborfoods.test", "Mia Halvorsen")

        # --- clients -------------------------------------------------------
        harbor = _scalar(
            conn,
            "INSERT INTO clients (agency_id, name, contact_email) "
            "VALUES (:a, 'Harbor Foods', 'mia@harborfoods.test') RETURNING id",
            a=northwind,
        )
        veldt = _scalar(
            conn,
            "INSERT INTO clients (agency_id, name, contact_email) "
            "VALUES (:a, 'Veldt Outdoors', 'ops@veldt.test') RETURNING id",
            a=northwind,
        )
        lumen = _scalar(
            conn,
            "INSERT INTO clients (agency_id, name, contact_email) "
            "VALUES (:a, 'Lumen Health', 'hello@lumen.test') RETURNING id",
            a=bluepeak,
        )

        # --- memberships ---------------------------------------------------
        def membership(user: str, agency: str, role: str, client: str | None = None) -> str:
            return _scalar(
                conn,
                "INSERT INTO memberships (user_id, agency_id, role, client_id) "
                "VALUES (:u, :a, CAST(:r AS membership_role), :c) RETURNING id",
                u=user, a=agency, r=role, c=client,
            )

        m_ada = membership(ada, northwind, "agency_admin")
        m_ben = membership(ben, northwind, "agency_member")
        m_cleo = membership(cleo, northwind, "agency_member")
        m_mia_client = membership(mia, northwind, "client_user", harbor)

        m_raj = membership(raj, bluepeak, "agency_admin")
        m_sam = membership(sam, bluepeak, "agency_member")
        m_mia_admin = membership(mia, bluepeak, "agency_admin")

        # --- projects ------------------------------------------------------
        def project(agency: str, client: str, name: str, description: str) -> str:
            return _scalar(
                conn,
                "INSERT INTO projects (agency_id, client_id, name, description) "
                "VALUES (:a, :c, :n, :d) RETURNING id",
                a=agency, c=client, n=name, d=description,
            )

        rebrand = project(northwind, harbor, "Harbor Foods Rebrand",
                          "Identity refresh, packaging system and a new site.")
        # A second project for the same client. Harbor Foods' portal shows both,
        # and still nothing of Veldt's -- scoping is per client, not per project.
        campaign = project(northwind, harbor, "Harbor Foods Q4 Campaign",
                           "Seasonal photography, paid social and email for the winter range.")
        catalog = project(northwind, veldt, "Veldt Spring Catalog",
                          "Photography, layout and print production.")
        lumen_app = project(bluepeak, lumen, "Lumen Patient App",
                            "Onboarding flow and appointment booking.")

        def add_member(project_id: str, membership_id: str, agency: str, role: str) -> None:
            _exec(
                conn,
                "INSERT INTO project_members (project_id, membership_id, agency_id, member_role) "
                "VALUES (:p, :m, :a, CAST(:r AS membership_role))",
                p=project_id, m=membership_id, a=agency, r=role,
            )

        add_member(rebrand, m_ada, northwind, "agency_admin")
        add_member(rebrand, m_ben, northwind, "agency_member")
        add_member(campaign, m_ada, northwind, "agency_admin")
        add_member(campaign, m_ben, northwind, "agency_member")
        # Cleo is on the catalog only -- she must not see the rebrand board.
        add_member(catalog, m_ada, northwind, "agency_admin")
        add_member(catalog, m_cleo, northwind, "agency_member")
        add_member(lumen_app, m_raj, bluepeak, "agency_admin")
        add_member(lumen_app, m_sam, bluepeak, "agency_member")
        # Mia again -- as staff this time. Same human, other tenant, other rights.
        add_member(lumen_app, m_mia_admin, bluepeak, "agency_admin")

        # --- tasks ---------------------------------------------------------
        def task(agency, project_id, title, *, visibility, status="todo",
                 priority="medium", assignee=None, due=None, creator=None, description=""):
            return _scalar(
                conn,
                """
                INSERT INTO tasks (agency_id, project_id, title, description, status, priority,
                                   visibility, assignee_membership_id, due_date,
                                   created_by_membership_id)
                VALUES (:a, :p, :t, :d, CAST(:s AS task_status), CAST(:pr AS task_priority),
                        CAST(:v AS visibility), :asg, :due, :cb)
                RETURNING id
                """,
                a=agency, p=project_id, t=title, d=description, s=status, pr=priority,
                v=visibility, asg=assignee, due=due, cb=creator,
            )

        # Harbor Foods Rebrand -- a realistic mix.
        t_moodboard = task(northwind, rebrand, "Moodboard round 2", visibility="client",
                           status="review", priority="high", assignee=m_ben,
                           due=today + timedelta(days=3), creator=m_ada,
                           description="Three directions for Harbor to react to.")
        t_logo = task(northwind, rebrand, "Logo lockup exploration", visibility="client",
                      status="in_progress", assignee=m_ben, creator=m_ada,
                      due=today + timedelta(days=10),
                      description="Primary, stacked and one-colour variants.")
        t_sitemap = task(northwind, rebrand, "Sitemap sign-off", visibility="client",
                         status="done", assignee=m_ada, creator=m_ada)
        t_margin = task(northwind, rebrand, "Renegotiate print margin with supplier",
                        visibility="internal", status="todo", priority="urgent",
                        assignee=m_ada, creator=m_ada,
                        description="Harbor must not see this. Target 12% -> 18%.")
        t_scope = task(northwind, rebrand, "Scope creep: client keeps adding deliverables",
                       visibility="internal", status="in_progress", assignee=m_ben,
                       creator=m_ada, due=today - timedelta(days=2),
                       description="Internal note before we raise a change order.")
        t_handover = task(northwind, rebrand, "Prep handover pack", visibility="internal",
                          status="todo", creator=m_ada)

        # Harbor Foods Q4 Campaign -- same client, second project, same mix.
        t_shotlist = task(northwind, campaign, "Winter range shot list", visibility="client",
                          status="in_progress", assignee=m_ben, creator=m_ada,
                          due=today + timedelta(days=6),
                          description="Twelve hero shots plus flat lays for the winter SKUs.")
        t_social = task(northwind, campaign, "Paid social concepts", visibility="client",
                        status="todo", priority="high", assignee=m_ben, creator=m_ada,
                        due=today + timedelta(days=14),
                        description="Three concepts to take into paid testing.")
        t_emails = task(northwind, campaign, "Email flow copy", visibility="client",
                        status="review", assignee=m_ada, creator=m_ada)
        t_burn = task(northwind, campaign, "Campaign burn rate vs retainer",
                      visibility="internal", status="todo", priority="urgent",
                      assignee=m_ada, creator=m_ada,
                      description="We are 40% through the retainer at 25% delivery.")
        task(northwind, campaign, "Photographer day rate negotiation", visibility="internal",
             status="blocked", priority="high", assignee=m_ben, creator=m_ada,
             description="Hold at last year's rate before we quote Harbor.")

        task(northwind, catalog, "Shoot list for spring range", visibility="client",
             status="in_progress", assignee=m_cleo, creator=m_ada)
        task(northwind, catalog, "Studio day cost overrun", visibility="internal",
             status="blocked", priority="high", assignee=m_cleo, creator=m_ada)

        # Bluepeak: the tenant that must stay invisible to Northwind entirely.
        task(bluepeak, lumen_app, "Booking flow wireframes", visibility="client",
             status="in_progress", assignee=m_sam, creator=m_raj)
        task(bluepeak, lumen_app, "Bluepeak internal: renewal risk", visibility="internal",
             status="todo", priority="urgent", assignee=m_raj, creator=m_raj,
             description="Northwind must never be able to read this row.")

        # --- comments ------------------------------------------------------
        def comment(agency, task_id, author, body, visibility):
            _exec(
                conn,
                "INSERT INTO comments (agency_id, task_id, author_membership_id, body, visibility) "
                "VALUES (:a, :t, :au, :b, CAST(:v AS visibility))",
                a=agency, t=task_id, au=author, b=body, v=visibility,
            )

        comment(northwind, t_moodboard, m_ben,
                "Round 2 is up -- direction B leans into the coastal palette.", "client")
        comment(northwind, t_moodboard, m_mia_client,
                "We love B. Can we see it with the darker navy?", "client")
        # An internal comment on a task the client CAN see: the task is visible,
        # this line is not. This is the leak most implementations miss.
        comment(northwind, t_moodboard, m_ada,
                "Ben: cap this at one more round, we are already over budget.", "internal")
        comment(northwind, t_logo, m_ben, "Stacked variant needs more optical spacing.", "internal")
        comment(northwind, t_margin, m_ada, "Supplier call Thursday. Do not discuss with Harbor.",
                "internal")
        comment(northwind, t_handover, m_ada, "Blocked until the margin conversation lands.",
                "internal")

        comment(northwind, t_shotlist, m_ben,
                "Draft shot list attached -- flag anything missing from the winter range.", "client")
        comment(northwind, t_shotlist, m_mia_client,
                "Add the gift bundle, it is the hero SKU this year.", "client")
        # Same shape as the moodboard trap: task shared, this line is not.
        comment(northwind, t_shotlist, m_ada,
                "Ben: the gift bundle is a change order, do not shoot it until Harbor signs.",
                "internal")
        comment(northwind, t_burn, m_ada, "Raise this at the Thursday internal before Harbor asks.",
                "internal")

        # --- files ---------------------------------------------------------
        def file_row(agency, task_id, uploader, filename, visibility, approval="pending"):
            # Write real bytes, not just a row. A metadata-only fixture makes the
            # download endpoint 404 on every seeded file, which hides whether the
            # authorisation path in front of it actually works.
            storage_key = f"seed-{task_id}-{filename}"
            blob = placeholder_pdf(filename)
            (upload_dir() / storage_key).write_bytes(blob)

            _exec(
                conn,
                """
                INSERT INTO files (agency_id, task_id, uploaded_by_membership_id, filename,
                                   content_type, size_bytes, storage_key, visibility,
                                   approval_status, approved_at)
                VALUES (:a, :t, :u, :f, 'application/pdf', :s, :k, CAST(:v AS visibility),
                        CAST(:ap AS approval_status), CASE WHEN :ap = 'pending' THEN NULL ELSE now() END)
                """,
                a=agency, t=task_id, u=uploader, f=filename, s=len(blob),
                k=storage_key, v=visibility, ap=approval,
            )

        file_row(northwind, t_moodboard, m_ben, "harbor-moodboard-r2.pdf", "client")
        file_row(northwind, t_sitemap, m_ada, "harbor-sitemap-v4.pdf", "client", approval="approved")
        file_row(northwind, t_margin, m_ada, "supplier-margin-analysis.xlsx", "internal")
        file_row(northwind, t_shotlist, m_ben, "winter-shot-list-v1.pdf", "client")
        file_row(northwind, t_emails, m_ada, "email-flow-copy.pdf", "client",
                 approval="needs_changes")
        file_row(northwind, t_burn, m_ada, "q4-burn-rate.xlsx", "internal")

        # --- time entries ---------------------------------------------------
        def time_entry(agency, task_id, membership, role, minutes, note, days_ago=0):
            _exec(
                conn,
                """
                INSERT INTO time_entries (agency_id, task_id, membership_id, member_role,
                                          minutes, note, entry_date)
                VALUES (:a, :t, :m, CAST(:r AS membership_role), :mi, :n, :d)
                """,
                a=agency, t=task_id, m=membership, r=role, mi=minutes, n=note,
                d=today - timedelta(days=days_ago),
            )

        time_entry(northwind, t_moodboard, m_ben, "agency_member", 210, "Round 2 boards", 2)
        time_entry(northwind, t_moodboard, m_ben, "agency_member", 90, "Palette revisions", 1)
        time_entry(northwind, t_logo, m_ben, "agency_member", 300, "Lockup exploration", 1)
        time_entry(northwind, t_sitemap, m_ada, "agency_admin", 60, "Sign-off call", 4)
        # Hours on internal tasks: these must not reach Harbor's dashboard total.
        time_entry(northwind, t_margin, m_ada, "agency_admin", 120, "Supplier prep", 3)
        time_entry(northwind, t_scope, m_ben, "agency_member", 150, "Change-order draft", 2)

        time_entry(northwind, t_shotlist, m_ben, "agency_member", 180, "Shot list v1", 3)
        time_entry(northwind, t_shotlist, m_ben, "agency_member", 75, "Winter SKU research", 1)
        time_entry(northwind, t_social, m_ben, "agency_member", 120, "Concept sketches", 1)
        time_entry(northwind, t_emails, m_ada, "agency_admin", 95, "Flow copy pass", 2)
        # Internal again: these minutes must stay out of Harbor's campaign total.
        time_entry(northwind, t_burn, m_ada, "agency_admin", 65, "Retainer reconciliation", 1)

        # --- an outstanding invite ------------------------------------------
        _exec(
            conn,
            """
            INSERT INTO invites (agency_id, email, role, client_id, token_hash,
                                 invited_by_membership_id, expires_at)
            VALUES (:a, 'ops@veldt.test', 'client_user', :c, :h, :m, :e)
            """,
            a=northwind, c=veldt, h=hash_invite_token("seed-invite-token-veldt"), m=m_ada,
            e=datetime.now(timezone.utc) + timedelta(days=7),
        )

    print(
        "\n".join(
            [
                "",
                "Seeded 2 agencies.",
                f"  All passwords: {PASSWORD}",
                "",
                "  Northwind Studio",
                "    ada@northwind.test    agency_admin   (sees everything, both projects)",
                "    ben@northwind.test    agency_member  (Rebrand only)",
                "    cleo@northwind.test   agency_member  (Catalog only -- cannot see Rebrand)",
                "    mia@harborfoods.test  client_user    (Harbor Foods portal)",
                "",
                "  Bluepeak Digital",
                "    raj@bluepeak.test     agency_admin",
                "    sam@bluepeak.test     agency_member",
                "    mia@harborfoods.test  agency_admin   <-- same person, second hat",
                "",
                "  Log in as mia@harborfoods.test to see the agency picker.",
                "  Pending invite token (dev): seed-invite-token-veldt",
                "",
            ]
        )
    )


if __name__ == "__main__":
    seed()
