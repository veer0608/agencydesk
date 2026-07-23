import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

function hours(minutes: number) {
  return `${(minutes / 60).toFixed(1)}h`;
}

/**
 * One dashboard, two answers.
 *
 * The agency and the client hit the same endpoint and the same SQL. The numbers
 * differ because row-level security narrows the rows before they are counted,
 * which is why the totals can never disagree with the board beside them.
 */
export default function Dashboard({ projectId }: { projectId: string }) {
  const dashboard = useQuery({
    queryKey: ["dashboard", projectId],
    queryFn: () => api.dashboard(projectId),
  });

  if (dashboard.isLoading) return <p className="muted">Loading…</p>;
  if (dashboard.isError || !dashboard.data)
    return <p className="error">This project is not available to you.</p>;

  const data = dashboard.data;

  return (
    <>
      <h1>{data.project_name}</h1>
      <p className="muted small">
        {data.client_name} &middot; {data.scope_note}
      </p>

      <div className="stats" style={{ marginTop: 16 }}>
        <div className="stat">
          <div className="value">{data.total_tasks}</div>
          <div className="label">Tasks</div>
        </div>
        <div className="stat">
          <div className="value">{data.open_tasks}</div>
          <div className="label">Still open</div>
        </div>
        <div className="stat">
          <div className="value" style={{ color: data.overdue_tasks ? "var(--danger)" : undefined }}>
            {data.overdue_tasks}
          </div>
          <div className="label">Overdue</div>
        </div>
        <div className="stat">
          <div className="value">{hours(data.minutes_logged)}</div>
          <div className="label">Hours logged</div>
        </div>
        <div className="stat">
          <div className="value">{data.files_awaiting_approval}</div>
          <div className="label">Files awaiting approval</div>
        </div>
      </div>

      <h2>By status</h2>
      <table>
        <tbody>
          {data.tasks_by_status.map((row) => (
            <tr key={row.status}>
              <td style={{ width: 140 }}>{row.status.replace("_", " ")}</td>
              <td>
                <div
                  style={{
                    background: "var(--accent-dim)",
                    border: "1px solid var(--accent)",
                    borderRadius: 6,
                    height: 18,
                    width: `${data.total_tasks ? (row.count / data.total_tasks) * 100 : 0}%`,
                    minWidth: row.count ? 22 : 0,
                  }}
                />
              </td>
              <td style={{ width: 50, textAlign: "right" }}>{row.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
