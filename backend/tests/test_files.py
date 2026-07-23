"""Files: upload, download, and the client approval loop.

The round trip is tested end to end deliberately. Listing a file only proves a
row exists; it says nothing about whether the bytes behind it are reachable, or
whether the authorisation in front of those bytes works. Both of those broke at
one point while every listing test stayed green.
"""

from __future__ import annotations

from .conftest import Actor


def _rebrand(ada: Actor) -> str:
    return next(
        p["id"] for p in ada.get("/api/projects").json() if p["name"] == "Harbor Foods Rebrand"
    )


def test_seeded_files_are_actually_downloadable(ada: Actor, northwind: dict) -> None:
    """Fixtures must be real files, not just rows.

    A metadata-only seed makes every download 404, which quietly turns the
    cross-tenant download tests into vacuous ones -- they would pass because
    *nobody* can download, not because the policy works.
    """
    assert northwind["files"], "fixture must contain files"
    for record in northwind["files"]:
        response = ada.get(f"/api/files/{record['id']}/download")
        assert response.status_code == 200, f"{record['filename']} is not downloadable"
        assert response.content.startswith(b"%PDF"), "seeded blob is not a real PDF"
        assert len(response.content) == record["size_bytes"]


def test_upload_list_download_round_trip(ada: Actor) -> None:
    project_id = _rebrand(ada)
    task = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Round trip attachment", "visibility": "client"},
    ).json()

    payload = b"%PDF-1.4 pretend design file\n"
    created = ada.post(
        f"/api/tasks/{task['id']}/files",
        files={"upload": ("proposal v3.pdf", payload, "application/pdf")},
        data={"visibility": "client"},
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["filename"] == "proposal v3.pdf"
    assert record["size_bytes"] == len(payload)
    assert record["approval_status"] == "pending"
    assert record["uploaded_by_name"] == "Ada Okafor"

    listed = ada.get(f"/api/tasks/{task['id']}/files").json()
    assert [f["id"] for f in listed] == [record["id"]]

    fetched = ada.get(f"/api/files/{record['id']}/download")
    assert fetched.status_code == 200
    assert fetched.content == payload


def test_upload_does_not_trust_the_supplied_filename(ada: Actor) -> None:
    """The name is a label, not a path. Storage keys are generated."""
    project_id = _rebrand(ada)
    task = ada.post(
        f"/api/projects/{project_id}/tasks", json={"title": "Traversal attempt"}
    ).json()

    created = ada.post(
        f"/api/tasks/{task['id']}/files",
        files={"upload": ("../../etc/passwd", b"nope", "text/plain")},
        data={"visibility": "internal"},
    )
    assert created.status_code == 201
    # Still downloadable under its generated key, and still exactly what we sent.
    fetched = ada.get(f"/api/files/{created.json()['id']}/download")
    assert fetched.status_code == 200
    assert fetched.content == b"nope"


def test_a_client_visible_file_cannot_hang_off_an_internal_task(ada: Actor) -> None:
    project_id = _rebrand(ada)
    internal = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Internal work", "visibility": "internal"},
    ).json()

    rejected = ada.post(
        f"/api/tasks/{internal['id']}/files",
        files={"upload": ("leak.pdf", b"x", "application/pdf")},
        data={"visibility": "client"},
    )
    assert rejected.status_code == 400


def test_clients_cannot_upload(mia_client: Actor, northwind: dict) -> None:
    task = northwind["client_tasks"][0]
    response = mia_client.post(
        f"/api/tasks/{task['id']}/files",
        files={"upload": ("client-upload.pdf", b"x", "application/pdf")},
        data={"visibility": "client"},
    )
    assert response.status_code == 403


def test_the_download_route_is_not_a_side_door(
    mia_client: Actor, raj: Actor, northwind: dict
) -> None:
    """Now meaningful, because we know these files *are* downloadable by the
    people who should have them."""
    internal = [f for f in northwind["files"] if f["visibility"] == "internal"]
    shared = [f for f in northwind["files"] if f["visibility"] == "client"]
    assert internal and shared, "fixture must contain both kinds"

    for record in internal:
        assert mia_client.get(f"/api/files/{record['id']}/download").status_code == 404
    for record in shared:
        assert mia_client.get(f"/api/files/{record['id']}/download").status_code == 200

    # And the other tenant gets nothing at all, either way.
    for record in internal + shared:
        assert raj.get(f"/api/files/{record['id']}/download").status_code == 404


def test_approval_decision_is_recorded_and_scoped(
    ada: Actor, ben: Actor, mia_client: Actor
) -> None:
    project_id = _rebrand(ada)
    task = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Needs sign-off", "visibility": "client"},
    ).json()
    record = ada.post(
        f"/api/tasks/{task['id']}/files",
        files={"upload": ("packaging.pdf", b"%PDF-1.4 packaging", "application/pdf")},
        data={"visibility": "client"},
    ).json()

    # An agency_member cannot sign off on the client's behalf.
    assert ben.patch(
        f"/api/files/{record['id']}/approval", json={"approval_status": "approved"}
    ).status_code == 403

    decided = mia_client.patch(
        f"/api/files/{record['id']}/approval",
        json={"approval_status": "approved", "note": "Looks great"},
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["approval_status"] == "approved"
    assert body["approval_note"] == "Looks great"
    assert body["approved_by_name"] == "Mia Halvorsen"
    assert body["approved_at"] is not None
    # The decision must not have disturbed the file itself.
    assert body["filename"] == "packaging.pdf"
    assert body["size_bytes"] == record["size_bytes"]

    assert ada.get(f"/api/files/{record['id']}/download").content == b"%PDF-1.4 packaging"


def test_approval_shows_up_on_the_project_dashboard(ada: Actor) -> None:
    project_id = _rebrand(ada)
    before = ada.get(f"/api/projects/{project_id}/dashboard").json()["files_awaiting_approval"]

    task = ada.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Awaiting sign-off", "visibility": "client"},
    ).json()
    ada.post(
        f"/api/tasks/{task['id']}/files",
        files={"upload": ("await.pdf", b"%PDF-1.4 await", "application/pdf")},
        data={"visibility": "client"},
    )

    after = ada.get(f"/api/projects/{project_id}/dashboard").json()["files_awaiting_approval"]
    assert after == before + 1
