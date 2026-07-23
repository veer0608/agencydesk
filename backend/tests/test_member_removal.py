"""Edge case 5: removing a team member mid-task.

The decision: their tasks are UNASSIGNED, not deleted and not auto-reassigned.
History (comments, files, logged hours) survives untouched, and the work lands
back in the unassigned column for a lead to redistribute deliberately.

Deleting would destroy a paper trail the agency may need to bill from.
Auto-reassigning would make somebody accountable for work they have never seen.
Leaving the assignee pointing at a person who is no longer on the project would
be a lie the board keeps telling.

The unassignment is performed by the foreign key, not by application code:

    tasks(project_id, assignee_membership_id)
        -> project_members(project_id, membership_id)
        ON DELETE SET NULL (assignee_membership_id)

so it happens inside the same statement as the removal, and it happens even if a
future endpoint removes a member by some route nobody has written yet.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text

from .conftest import Actor


def _fresh_project(ada: Actor) -> dict:
    """Build a throwaway project so these tests never disturb the fixture board."""
    harbor = next(c for c in ada.get("/api/clients").json() if c["name"] == "Harbor Foods")
    rebrand = next(
        p for p in ada.get("/api/projects").json() if p["name"] == "Harbor Foods Rebrand"
    )
    ben = next(
        m for m in ada.get(f"/api/projects/{rebrand['id']}/members").json()
        if m["email"] == "ben@northwind.test"
    )

    project = ada.post(
        "/api/projects",
        json={"client_id": harbor["id"], "name": f"Removal drill {os.urandom(3).hex()}"},
    ).json()
    for membership_id in (ada.me["membership_id"], ben["membership_id"]):
        ada.post(f"/api/projects/{project['id']}/members", json={"membership_id": membership_id})

    return {"project": project, "ben": ben}


def test_removal_unassigns_without_destroying_anything(ada: Actor) -> None:
    setup = _fresh_project(ada)
    project_id = setup["project"]["id"]
    ben_id = setup["ben"]["membership_id"]

    assigned = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Half-finished banner set", "assignee_membership_id": ben_id,
              "visibility": "client", "status": "in_progress"},
    ).json()
    untouched = ada.post(
        f"/api/projects/{project_id}/tasks", json={"title": "Nothing to do with Ben"}
    ).json()

    ada.post(f"/api/tasks/{assigned['id']}/comments",
             json={"body": "Waiting on copy", "visibility": "internal"})
    ada.post(f"/api/tasks/{assigned['id']}/time-entries", json={"minutes": 45, "note": "Round one"})

    removal = ada.delete(f"/api/projects/{project_id}/members/{ben_id}")
    assert removal.status_code == 200
    body = removal.json()
    assert body["unassigned_task_ids"] == [assigned["id"]]

    after = ada.get(f"/api/tasks/{assigned['id']}").json()
    assert after["assignee_membership_id"] is None
    assert after["assignee_name"] is None
    # Everything else about the task is exactly as it was.
    assert after["status"] == "in_progress"
    assert after["title"] == assigned["title"]
    assert after["visibility"] == "client"
    assert after["comment_count"] == 1
    assert after["minutes_logged"] == 45

    # The evidence survives the person.
    assert len(ada.get(f"/api/tasks/{assigned['id']}/comments").json()) == 1
    entries = ada.get(f"/api/tasks/{assigned['id']}/time-entries").json()
    assert len(entries) == 1 and entries[0]["minutes"] == 45

    # Unrelated work is not collateral damage.
    assert ada.get(f"/api/tasks/{untouched['id']}").status_code == 200

    # And the removed person is off the roster.
    remaining = {m["membership_id"] for m in ada.get(f"/api/projects/{project_id}/members").json()}
    assert ben_id not in remaining


def test_the_removed_member_immediately_loses_access(ada: Actor, ben: Actor) -> None:
    setup = _fresh_project(ada)
    project_id = setup["project"]["id"]
    ben_id = setup["ben"]["membership_id"]

    assert ben.get(f"/api/projects/{project_id}").status_code == 200
    ada.delete(f"/api/projects/{project_id}/members/{ben_id}")

    # No re-login, no token change -- the next request is simply narrower,
    # because the policy consults project_members rather than the token.
    assert ben.get(f"/api/projects/{project_id}").status_code == 404
    assert ben.get(f"/api/projects/{project_id}/tasks").status_code == 404
    assert project_id not in {p["id"] for p in ben.get("/api/projects").json()}


def test_removal_is_enforced_by_the_database_not_the_handler(ada: Actor) -> None:
    """Delete the membership row directly in SQL, bypassing the API entirely.

    If the unassignment lived in the request handler this would leave a dangling
    assignee. It lives in the foreign key, so it does not.
    """
    setup = _fresh_project(ada)
    project_id = setup["project"]["id"]
    ben_id = setup["ben"]["membership_id"]

    task = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Assigned via API, orphaned via SQL", "assignee_membership_id": ben_id},
    ).json()

    engine = create_engine(os.environ["OWNER_DATABASE_URL"], future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM project_members WHERE project_id = :p AND membership_id = :m"),
                {"p": project_id, "m": ben_id},
            )
    finally:
        engine.dispose()

    after = ada.get(f"/api/tasks/{task['id']}").json()
    assert after["assignee_membership_id"] is None
    assert after["title"] == "Assigned via API, orphaned via SQL"


def test_cannot_assign_to_someone_who_is_not_on_the_project(ada: Actor) -> None:
    """The same foreign key, read forwards: an assignee must be a project member.

    This is what makes the removal rule coherent -- there is no state in which a
    task points at somebody who is not on its project, in either direction.
    """
    setup = _fresh_project(ada)
    project_id = setup["project"]["id"]
    ben_id = setup["ben"]["membership_id"]
    ada.delete(f"/api/projects/{project_id}/members/{ben_id}")

    rejected = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Assign to an outsider", "assignee_membership_id": ben_id},
    )
    assert rejected.status_code == 400


def test_a_client_contact_can_never_be_added_to_a_project(ada: Actor, mia_client: Actor) -> None:
    """Clients reach projects through their client record, never as members.

    Enforced by project_members carrying a copy of the role under a composite
    foreign key, plus CHECK (member_role IN ('agency_admin','agency_member')).
    """
    setup = _fresh_project(ada)
    project_id = setup["project"]["id"]

    response = ada.post(
        f"/api/projects/{project_id}/members",
        json={"membership_id": mia_client.me["membership_id"]},
    )
    assert response.status_code in (400, 404), response.text

    roster = ada.get(f"/api/projects/{project_id}/members").json()
    assert mia_client.me["membership_id"] not in {m["membership_id"] for m in roster}
