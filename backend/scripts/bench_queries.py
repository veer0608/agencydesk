"""Benchmark the query shapes behind the task board.

Run against a seeded database:

    docker compose exec api python -m scripts.bench_queries

It bulk-loads a large project, times three ways of computing the per-task
comment/file/hours counts, and rolls everything back. Nothing is left behind.

The point is the *second* table. `TASK_COLUMNS` in repositories.py uses
correlated subqueries, which look like the naive choice -- a grouped-join
rewrite is roughly 4x faster on a 2,000-task board. It is also about 100x
slower on a 2-task board, because it aggregates every row the caller can see
before joining, and real agency boards are small. The current shape is the
deliberate one.
"""

from __future__ import annotations

import os
import re
import statistics

from sqlalchemy import create_engine, text

OWNER_URL = os.environ.get(
    "OWNER_DATABASE_URL",
    "postgresql+psycopg://agencydesk_owner:owner_pw@db:5432/agencydesk",
)

COUNTS_CORRELATED = """
SELECT t.id,
       (SELECT count(*) FROM comments cm WHERE cm.task_id = t.id)::int AS comment_count,
       (SELECT count(*) FROM files f WHERE f.task_id = t.id)::int AS file_count,
       (SELECT coalesce(sum(te.minutes),0) FROM time_entries te WHERE te.task_id = t.id)::int AS mins
FROM tasks t JOIN projects p ON p.id = t.project_id
WHERE t.project_id = :p
"""

COUNTS_LATERAL = """
SELECT t.id, c.n::int, f.n::int, te.mins::int
FROM tasks t JOIN projects p ON p.id = t.project_id
LEFT JOIN LATERAL (SELECT count(*) n FROM comments cm WHERE cm.task_id = t.id) c ON true
LEFT JOIN LATERAL (SELECT count(*) n FROM files fl WHERE fl.task_id = t.id) f ON true
LEFT JOIN LATERAL (SELECT coalesce(sum(minutes),0) mins FROM time_entries x
                    WHERE x.task_id = t.id) te ON true
WHERE t.project_id = :p
"""

COUNTS_GROUPED = """
SELECT t.id, coalesce(c.n,0)::int, coalesce(f.n,0)::int, coalesce(te.mins,0)::int
FROM tasks t JOIN projects p ON p.id = t.project_id
LEFT JOIN (SELECT task_id, count(*) n FROM comments GROUP BY task_id) c ON c.task_id = t.id
LEFT JOIN (SELECT task_id, count(*) n FROM files GROUP BY task_id) f ON f.task_id = t.id
LEFT JOIN (SELECT task_id, sum(minutes) mins FROM time_entries GROUP BY task_id) te
       ON te.task_id = t.id
WHERE t.project_id = :p
"""

SHAPES = {
    "correlated subqueries (current)": COUNTS_CORRELATED,
    "LEFT JOIN LATERAL": COUNTS_LATERAL,
    "grouped LEFT JOINs": COUNTS_GROUPED,
}

BULK_TASKS = 2000


def _time(conn, sql: str, params: dict, runs: int = 7) -> float:
    times = []
    for _ in range(runs):
        plan = "\n".join(
            r[0] for r in conn.execute(text("EXPLAIN (ANALYZE) " + sql), params)
        )
        times.append(float(re.search(r"Execution Time: ([\d.]+) ms", plan).group(1)))
    return statistics.median(times)


def main() -> None:
    engine = create_engine(OWNER_URL, future=True)
    with engine.connect() as conn:
        trans = conn.begin()  # rolled back at the end; nothing is persisted

        big = conn.execute(
            text("SELECT id, agency_id FROM projects WHERE name = 'Harbor Foods Rebrand'")
        ).one()
        small = conn.execute(
            text("SELECT id FROM projects WHERE name = 'Veldt Spring Catalog'")
        ).scalar_one()
        member = conn.execute(
            text("SELECT membership_id, member_role FROM project_members "
                 "WHERE project_id = :p LIMIT 1"),
            {"p": big.id},
        ).one()

        print(f"Loading {BULK_TASKS:,} tasks into 'Harbor Foods Rebrand'...")
        args = {
            "a": big.agency_id, "p": big.id, "m": member.membership_id,
            "r": member.member_role, "n": BULK_TASKS,
        }
        conn.execute(text(
            """INSERT INTO tasks (agency_id, project_id, title, status, priority, visibility,
                                  assignee_membership_id, created_by_membership_id)
               SELECT :a, :p, 'Bulk ' || g, 'todo', 'medium', 'client', :m, :m
               FROM generate_series(1, :n) g"""), args)
        conn.execute(text(
            """INSERT INTO time_entries (agency_id, task_id, membership_id, member_role,
                                         minutes, note, entry_date)
               SELECT :a, t.id, :m, :r, 30, 'bulk', current_date
               FROM tasks t, generate_series(1, 15) g WHERE t.project_id = :p"""), args)
        conn.execute(text(
            """INSERT INTO comments (agency_id, task_id, author_membership_id, body, visibility)
               SELECT :a, t.id, :m, 'bulk', 'client'
               FROM tasks t, generate_series(1, 5) g WHERE t.project_id = :p"""), args)
        conn.execute(text("ANALYZE tasks"))
        conn.execute(text("ANALYZE time_entries"))
        conn.execute(text("ANALYZE comments"))

        for label, project_id in (
            (f"LARGE board (~{BULK_TASKS:,} tasks)", big.id),
            ("SMALL board (the realistic case)", small),
        ):
            print(f"\n{label}")
            results = {n: _time(conn, s, {"p": project_id}) for n, s in SHAPES.items()}
            best = min(results.values())
            for name, ms in sorted(results.items(), key=lambda kv: kv[1]):
                note = "  <-- fastest" if ms == best else f"  ({ms / best:.1f}x slower)"
                print(f"    {name:34} {ms:8.2f} ms{note}")

        trans.rollback()
        print("\nRolled back. The database is unchanged.")


if __name__ == "__main__":
    main()
