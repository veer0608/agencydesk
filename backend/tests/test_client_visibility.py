"""Edge case 2: internal content must not reach a client -- on ANY code path.

The assignment calls out search, filters and comments as the easy ones to miss,
so those are tested explicitly rather than trusting the board view to be
representative.
"""

from __future__ import annotations

from .conftest import Actor


def test_client_sees_only_their_own_projects(mia_client: Actor, ada: Actor) -> None:
    """Scoping is per *client*, not per project.

    Harbor Foods has more than one project and Mia sees all of them -- while
    Veldt Outdoors' project, belonging to a different client of the same agency,
    is absent entirely.
    """
    projects = mia_client.get("/api/projects").json()
    names = {p["name"] for p in projects}

    assert {"Harbor Foods Rebrand", "Harbor Foods Q4 Campaign"} <= names
    assert all(p["client_name"] == "Harbor Foods" for p in projects)
    assert "Veldt Spring Catalog" not in names

    # The agency sees strictly more: Harbor's projects plus everyone else's.
    assert names < {p["name"] for p in ada.get("/api/projects").json()}


def test_board_hides_internal_tasks(mia_client: Actor, ada: Actor, northwind: dict) -> None:
    project_id = northwind["rebrand"]["id"]

    seen = mia_client.get(f"/api/projects/{project_id}/tasks").json()
    assert seen, "the client should still see their own work"
    assert all(t["visibility"] == "client" for t in seen)

    hidden = {t["title"] for t in northwind["internal_tasks"]}
    assert hidden, "fixture must contain internal tasks or this proves nothing"
    assert not ({t["title"] for t in seen} & hidden)
    assert len(seen) < len(ada.get(f"/api/projects/{project_id}/tasks").json())


def test_internal_task_is_a_404_by_direct_id(mia_client: Actor, northwind: dict) -> None:
    for task in northwind["internal_tasks"]:
        assert mia_client.get(f"/api/tasks/{task['id']}").status_code == 404
        assert mia_client.get(f"/api/tasks/{task['id']}/comments").status_code == 404
        assert mia_client.get(f"/api/tasks/{task['id']}/files").status_code == 404
        assert mia_client.get(f"/api/tasks/{task['id']}/time-entries").status_code == 404


def test_filters_cannot_widen_what_a_client_sees(mia_client: Actor, northwind: dict) -> None:
    """Filtering is the classic hole: a query parameter that quietly re-runs the
    lookup without the visibility clause. Here the filters sit on top of the
    policy, so no combination of them can reveal an extra row."""
    project_id = northwind["rebrand"]["id"]
    baseline = {t["id"] for t in mia_client.get(f"/api/projects/{project_id}/tasks").json()}

    for params in (
        {"status": "todo"}, {"status": "in_progress"}, {"status": "done"},
        {"status": "blocked"}, {"status": "review"},
        {"q": "margin"}, {"q": "scope"}, {"q": "supplier"}, {"q": "e"}, {"q": ""},
    ):
        rows = mia_client.get(f"/api/projects/{project_id}/tasks", params=params).json()
        assert {t["id"] for t in rows} <= baseline, f"filter {params} widened the result set"
        assert all(t["visibility"] == "client" for t in rows)


def test_search_never_returns_internal_content(mia_client: Actor) -> None:
    """The internal fixture rows are seeded with distinctive words on purpose."""
    for needle in ("margin", "scope creep", "supplier", "handover", "budget", "internal"):
        hits = mia_client.get("/api/search", params={"q": needle}).json()
        assert all(h["visibility"] == "client" for h in hits), f"search '{needle}' leaked"

    # Positive control: search works at all for this client.
    assert mia_client.get("/api/search", params={"q": "moodboard"}).json()


def test_internal_comments_on_a_visible_task_stay_hidden(
    mia_client: Actor, ada: Actor, northwind: dict
) -> None:
    """The subtlest one in the fixture.

    'Moodboard round 2' is client-visible and Mia is meant to read it. One of its
    comments is internal -- Ada telling Ben to cap the budget. Task visible,
    comment not.
    """
    task = next(t for t in northwind["client_tasks"] if t["title"] == "Moodboard round 2")

    theirs = mia_client.get(f"/api/tasks/{task['id']}/comments").json()
    ours = ada.get(f"/api/tasks/{task['id']}/comments").json()

    assert theirs, "the client should see the conversation they are part of"
    assert len(theirs) < len(ours), "fixture must have an internal comment here"
    assert all(c["visibility"] == "client" for c in theirs)
    assert not any("over budget" in c["body"] for c in theirs)
    assert any("over budget" in c["body"] for c in ours)


def test_internal_files_and_their_bytes_stay_hidden(
    mia_client: Actor, ada: Actor, northwind: dict
) -> None:
    internal_files = [f for f in northwind["files"] if f["visibility"] == "internal"]
    assert internal_files, "fixture must contain an internal file"

    for record in internal_files:
        # Not in any listing the client can reach...
        assert mia_client.get(f"/api/tasks/{record['task_id']}/files").status_code == 404
        # ...and the download route is not a side door either.
        assert mia_client.get(f"/api/files/{record['id']}/download").status_code == 404


def test_dashboard_totals_are_scoped_to_the_viewer(
    mia_client: Actor, ada: Actor, northwind: dict
) -> None:
    """A dashboard that counts rows the viewer cannot open is a leak with extra
    steps: it tells the client exactly how much work is being hidden."""
    project_id = northwind["rebrand"]["id"]
    theirs = mia_client.get(f"/api/projects/{project_id}/dashboard").json()
    ours = ada.get(f"/api/projects/{project_id}/dashboard").json()

    assert theirs["total_tasks"] < ours["total_tasks"]
    assert theirs["minutes_logged"] < ours["minutes_logged"]
    assert theirs["viewer_role"] == "client_user"

    # The client's totals must equal what the client can actually list.
    visible = mia_client.get(f"/api/projects/{project_id}/tasks").json()
    assert theirs["total_tasks"] == len(visible)
    assert sum(c["count"] for c in theirs["tasks_by_status"]) == len(visible)

    # Hours: only time booked against client-visible tasks.
    expected = sum(t["minutes_logged"] for t in visible)
    assert theirs["minutes_logged"] == expected


def test_client_cannot_create_or_move_tasks(mia_client: Actor, northwind: dict) -> None:
    project_id = northwind["rebrand"]["id"]
    task = northwind["client_tasks"][0]

    assert mia_client.post(
        f"/api/projects/{project_id}/tasks", json={"title": "Please do this"}
    ).status_code == 403

    moved = mia_client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert moved.status_code == 403

    # And nothing actually changed.
    assert mia_client.get(f"/api/tasks/{task['id']}").json()["status"] != "done"


def test_client_can_comment_and_the_comment_is_client_visible(
    mia_client: Actor, ada: Actor, northwind: dict
) -> None:
    task = next(t for t in northwind["client_tasks"] if t["title"] == "Moodboard round 2")

    # Even asking for an internal comment, a client gets a client-visible one.
    created = mia_client.post(
        f"/api/tasks/{task['id']}/comments",
        json={"body": "Navy version please", "visibility": "internal"},
    )
    assert created.status_code == 201
    assert created.json()["visibility"] == "client"

    assert any(
        c["body"] == "Navy version please" for c in ada.get(f"/api/tasks/{task['id']}/comments").json()
    )


def test_client_cannot_log_time(mia_client: Actor, northwind: dict) -> None:
    task = northwind["client_tasks"][0]
    assert mia_client.post(
        f"/api/tasks/{task['id']}/time-entries", json={"minutes": 60, "note": "nice try"}
    ).status_code == 403


def test_client_cannot_see_the_agency_roster(mia_client: Actor, northwind: dict) -> None:
    """Who works at the agency is internal information too."""
    assert mia_client.get(
        f"/api/projects/{northwind['rebrand']['id']}/members"
    ).status_code == 403
    assert mia_client.get("/api/invites").status_code == 403


def test_client_can_approve_a_visible_file_but_not_rename_it(
    mia_client: Actor, ada: Actor, northwind: dict
) -> None:
    shared = next(f for f in northwind["files"] if f["visibility"] == "client"
                  and f["approval_status"] == "pending")

    approved = mia_client.patch(
        f"/api/files/{shared['id']}/approval",
        json={"approval_status": "needs_changes", "note": "Darker navy please"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["approval_status"] == "needs_changes"
    assert body["approved_by_name"] == "Mia Halvorsen"
    assert body["filename"] == shared["filename"], "approval must not touch the file itself"


def test_every_row_a_client_can_reach_is_client_visible(
    mia_client: Actor, northwind: dict
) -> None:
    """A crawl rather than a spot check: walk everything Mia can reach from the
    project list and assert the invariant holds on every row of every type."""
    checked = 0
    for project in mia_client.get("/api/projects").json():
        for task in mia_client.get(f"/api/projects/{project['id']}/tasks").json():
            assert task["visibility"] == "client"
            checked += 1
            for comment in mia_client.get(f"/api/tasks/{task['id']}/comments").json():
                assert comment["visibility"] == "client"
                checked += 1
            for record in mia_client.get(f"/api/tasks/{task['id']}/files").json():
                assert record["visibility"] == "client"
                checked += 1
            for entry in mia_client.get(f"/api/tasks/{task['id']}/time-entries").json():
                # Time entries have no flag of their own -- they inherit the
                # task's, so reaching one at all means the task was visible.
                assert entry["minutes"] > 0
                checked += 1
    assert checked > 5, "crawl covered too little to be meaningful"


def test_a_new_project_enrols_its_creator(ada: Actor) -> None:
    """A project with no members is inert: tasks can only be assigned to project
    members, so nobody could be given any work on it."""
    client_id = next(c["id"] for c in ada.get("/api/clients").json() if c["name"] == "Harbor Foods")
    project = ada.post(
        "/api/projects", json={"client_id": client_id, "name": "Enrolment check", "description": ""}
    ).json()

    members = ada.get(f"/api/projects/{project['id']}/members").json()
    assert [m["email"] for m in members] == ["ada@northwind.test"]

    # And because she is on it, work can actually be assigned straight away.
    task = ada.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "First task", "assignee_membership_id": members[0]["membership_id"]},
    )
    assert task.status_code == 201
    assert task.json()["assignee_name"] == "Ada Okafor"
