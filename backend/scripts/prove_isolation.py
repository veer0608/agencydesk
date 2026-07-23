"""A readable walk through the guarantees, for humans (and for the video).

    docker compose exec api python -m scripts.prove_isolation

Hits the running API over HTTP exactly as a browser would. Prints what it tried,
what came back, and whether that is what should have happened. Exits non-zero if
any guarantee fails, so it doubles as a smoke test.
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("API_URL", "http://localhost:8000")
PASSWORD = "password123"

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

failures: list[str] = []


def check(description: str, condition: bool, detail: str = "") -> None:
    mark = f"{GREEN}PASS{RESET}" if condition else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {description}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")
    if not condition:
        failures.append(description)


def heading(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}\n{'-' * len(text)}")


class Session:
    def __init__(self, email: str, agency: str | None = None) -> None:
        self.http = httpx.Client(base_url=BASE, timeout=20.0)
        body = self.http.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}
        ).json()
        token = body["access_token"]
        if token is None:
            match = next(m for m in body["memberships"] if m["agency_name"] == agency)
            token = self.http.post(
                "/api/auth/select-agency",
                json={"email": email, "password": PASSWORD,
                      "membership_id": match["membership_id"]},
            ).json()["access_token"]
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.me = self.http.get("/api/auth/me").json()

    def get(self, url: str, **kw):
        return self.http.get(url, **kw)

    def post(self, url: str, **kw):
        return self.http.post(url, **kw)

    def patch(self, url: str, **kw):
        return self.http.patch(url, **kw)

    def delete(self, url: str, **kw):
        return self.http.delete(url, **kw)


def main() -> int:
    ada = Session("ada@northwind.test")                       # Northwind admin
    raj = Session("raj@bluepeak.test")                        # Bluepeak admin
    mia = Session("mia@harborfoods.test", "Northwind Studio")  # Harbor Foods client

    rebrand = next(
        p for p in ada.get("/api/projects").json() if p["name"] == "Harbor Foods Rebrand"
    )
    all_tasks = ada.get(f"/api/projects/{rebrand['id']}/tasks").json()
    internal = [t for t in all_tasks if t["visibility"] == "internal"]

    heading("1. Cross-tenant access")
    print(f"  {DIM}Raj administers Bluepeak. He is holding Northwind's real ids.{RESET}")
    check(
        "GET a foreign project by id -> 404",
        raj.get(f"/api/projects/{rebrand['id']}").status_code == 404,
        f"project {rebrand['id']}",
    )
    check(
        "GET a foreign task by id -> 404",
        raj.get(f"/api/tasks/{all_tasks[0]['id']}").status_code == 404,
    )
    check(
        "PATCH a foreign task -> 404 (not 403: a 403 would confirm the id is real)",
        raj.patch(f"/api/tasks/{all_tasks[0]['id']}", json={"status": "done"}).status_code == 404,
    )
    check(
        "Foreign agency's work never appears in his own listing",
        {p["name"] for p in raj.get("/api/projects").json()} == {"Lumen Patient App"},
    )
    check(
        "Search spans the same physical tables and still finds nothing",
        raj.get("/api/search", params={"q": "margin"}).json() == [],
    )

    heading("2. Internal content vs the client portal")
    print(f"  {DIM}Mia is Harbor Foods' contact. The agency has {len(internal)} internal tasks.{RESET}")
    visible = mia.get(f"/api/projects/{rebrand['id']}/tasks").json()
    check(
        f"Board shows {len(visible)} of {len(all_tasks)} tasks, all client-visible",
        all(t["visibility"] == "client" for t in visible) and len(visible) < len(all_tasks),
        "hidden: " + ", ".join(t["title"] for t in internal),
    )
    check(
        "Internal task fetched directly by id -> 404",
        all(mia.get(f"/api/tasks/{t['id']}").status_code == 404 for t in internal),
    )
    check(
        "Search finds no internal content",
        all(
            all(h["visibility"] == "client" for h in mia.get("/api/search", params={"q": w}).json())
            for w in ("margin", "supplier", "scope", "budget")
        ),
    )

    moodboard = next(t for t in visible if t["title"] == "Moodboard round 2")
    ours = ada.get(f"/api/tasks/{moodboard['id']}/comments").json()
    theirs = mia.get(f"/api/tasks/{moodboard['id']}/comments").json()
    check(
        "Internal comment on a task the client CAN see stays hidden",
        len(theirs) < len(ours) and all(c["visibility"] == "client" for c in theirs),
        f"agency reads {len(ours)} comments here, client reads {len(theirs)}",
    )

    ours_dash = ada.get(f"/api/projects/{rebrand['id']}/dashboard").json()
    theirs_dash = mia.get(f"/api/projects/{rebrand['id']}/dashboard").json()
    check(
        "Dashboard counts are computed under the viewer's own policies",
        theirs_dash["total_tasks"] == len(visible)
        and theirs_dash["minutes_logged"] < ours_dash["minutes_logged"],
        f"agency: {ours_dash['total_tasks']} tasks / {ours_dash['minutes_logged']}min   "
        f"client: {theirs_dash['total_tasks']} tasks / {theirs_dash['minutes_logged']}min",
    )
    check(
        "Client cannot create a task",
        mia.post(f"/api/projects/{rebrand['id']}/tasks", json={"title": "x"}).status_code == 403,
    )
    check(
        "Client cannot move a task across the board",
        mia.patch(f"/api/tasks/{moodboard['id']}", json={"status": "done"}).status_code == 403,
    )

    heading("3. One person, two agencies")
    both = httpx.post(
        f"{BASE}/api/auth/login",
        json={"email": "mia@harborfoods.test", "password": PASSWORD},
        timeout=20.0,
    ).json()
    roles = {m["agency_name"]: m["role"] for m in both["memberships"]}
    check(
        "Same email resolves to two memberships with different roles",
        roles == {"Northwind Studio": "client_user", "Bluepeak Digital": "agency_admin"},
        str(roles),
    )
    check(
        "Login issues no token until an agency is chosen",
        both["access_token"] is None,
    )
    mia_admin = Session("mia@harborfoods.test", "Bluepeak Digital")
    check(
        "Her admin session at Bluepeak grants nothing at Northwind",
        mia_admin.get(f"/api/projects/{rebrand['id']}").status_code == 404,
    )
    check(
        "One account, two membership ids",
        mia.me["user_id"] == mia_admin.me["user_id"]
        and mia.me["membership_id"] != mia_admin.me["membership_id"],
    )

    heading("4. Invite races")
    email = "prove-script@northwind.test"
    first = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    second = ada.post("/api/invites", json={"email": email, "role": "agency_member"}).json()
    check(
        "Resending updates the same invite instead of creating another",
        first["id"] == second["id"] and second["resent"] is True,
    )
    check(
        "Resending rotates the token, so the older emailed link stops working",
        first["invite_url"] != second["invite_url"]
        and httpx.get(
            f"{BASE}/api/auth/invite/{first['invite_url'].split('token=')[1]}", timeout=20.0
        ).status_code == 404,
    )

    token = second["invite_url"].split("token=")[1]
    accept = lambda: httpx.post(  # noqa: E731
        f"{BASE}/api/auth/accept-invite",
        json={"token": token, "full_name": "Prove Script", "password": "hunter2hunter2"},
        timeout=20.0,
    ).status_code
    check("First acceptance succeeds", accept() == 200)
    check("Second acceptance of the same link is refused", accept() == 400)

    heading("5. Removing a team member mid-task")
    harbor = next(c for c in ada.get("/api/clients").json() if c["name"] == "Harbor Foods")
    drill = ada.post(
        "/api/projects", json={"client_id": harbor["id"], "name": "Removal drill (script)"}
    ).json()
    ben = next(
        m for m in ada.get(f"/api/projects/{rebrand['id']}/members").json()
        if m["email"] == "ben@northwind.test"
    )
    ada.post(f"/api/projects/{drill['id']}/members", json={"membership_id": ada.me["membership_id"]})
    ada.post(f"/api/projects/{drill['id']}/members", json={"membership_id": ben["membership_id"]})
    task = ada.post(
        f"/api/projects/{drill['id']}/tasks",
        json={"title": "Mid-flight work", "assignee_membership_id": ben["membership_id"],
              "status": "in_progress"},
    ).json()
    ada.post(f"/api/tasks/{task['id']}/time-entries", json={"minutes": 45, "note": "round one"})

    result = ada.delete(f"/api/projects/{drill['id']}/members/{ben['membership_id']}").json()
    after = ada.get(f"/api/tasks/{task['id']}").json()
    check(
        "Removal unassigns the task (by foreign key, in the same statement)",
        after["assignee_membership_id"] is None and result["unassigned_task_ids"] == [task["id"]],
    )
    check(
        "...and destroys nothing: status, title and logged hours all survive",
        after["status"] == "in_progress" and after["minutes_logged"] == 45,
        f"'{after['title']}' is now unassigned with {after['minutes_logged']} minutes on it",
    )

    print()
    if failures:
        print(f"{RED}{BOLD}{len(failures)} guarantee(s) FAILED:{RESET}")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"{GREEN}{BOLD}All guarantees hold.{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
