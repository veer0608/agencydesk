import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Task, type TaskPriority, type TaskStatus, type Visibility } from "../api";
import { useRole } from "../auth";
import TaskDrawer from "./TaskDrawer";

const COLUMNS: { status: TaskStatus; label: string }[] = [
  { status: "todo", label: "To do" },
  { status: "in_progress", label: "In progress" },
  { status: "blocked", label: "Blocked" },
  { status: "review", label: "Review" },
  { status: "done", label: "Done" },
];

export default function Board({ projectId }: { projectId: string }) {
  const { isStaff, isClient, isAdmin } = useRole();
  const queryClient = useQueryClient();

  const [open, setOpen] = useState<Task | null>(null);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("internal");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("medium");
  const [dueDate, setDueDate] = useState("");
  const [newMember, setNewMember] = useState("");
  const [error, setError] = useState<string | null>(null);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.project(projectId),
  });
  const tasks = useQuery({
    queryKey: ["tasks", projectId, search],
    queryFn: () => api.tasks(projectId, search ? { q: search } : undefined),
  });
  const members = useQuery({
    queryKey: ["members", projectId],
    queryFn: () => api.members(projectId),
    enabled: isStaff,
  });

  const create = useMutation({
    mutationFn: () =>
      api.createTask(projectId, {
        title,
        visibility,
        priority,
        assignee_membership_id: assignee || null,
        due_date: dueDate || null,
      }),
    onSuccess: () => {
      setTitle("");
      setAssignee("");
      setDueDate("");
      setPriority("medium");
      setShowCreate(false);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (caught) =>
      setError(caught instanceof Error ? caught.message : "Could not create the task"),
  });

  const staff = useQuery({
    queryKey: ["agency-staff"],
    queryFn: api.agencyStaff,
    enabled: isAdmin,
  });

  // Anyone at the agency who is not already on this project.
  const onProject = new Set((members.data ?? []).map((m) => m.membership_id));
  const candidates = (staff.data ?? []).filter((s) => !onProject.has(s.membership_id));

  const addMember = useMutation({
    mutationFn: () => api.addMember(projectId, newMember),
    onSuccess: () => {
      setError(null);
      setNewMember("");
      void queryClient.invalidateQueries({ queryKey: ["members", projectId] });
    },
    onError: (caught) =>
      setError(caught instanceof Error ? caught.message : "Could not add that person"),
  });

  const removeMember = useMutation({
    mutationFn: (membershipId: string) => api.removeMember(projectId, membershipId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void members.refetch();
      window.alert(result.detail);
    },
  });

  if (project.isLoading) return <p className="muted">Loading…</p>;
  if (project.isError) return <p className="error">This project is not available to you.</p>;

  const grouped = (status: TaskStatus) =>
    (tasks.data ?? []).filter((task) => task.status === status);

  return (
    <>
      <div className="between">
        <div>
          <h1>{project.data?.name}</h1>
          <p className="muted small" style={{ margin: 0 }}>
            {project.data?.client_name}
            {isClient && " — your portal shows the work your agency has shared with you."}
          </p>
        </div>
        <div className="row">
          <input
            placeholder="Filter tasks…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 200 }}
          />
          {isStaff && (
            <button className="primary" onClick={() => setShowCreate((value) => !value)}>
              New task
            </button>
          )}
        </div>
      </div>

      {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}

      {showCreate && isStaff && (
        <div className="card" style={{ margin: "14px 0" }}>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <input
              placeholder="Task title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              style={{ flex: 2, minWidth: 220 }}
            />
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value as Visibility)}
              style={{ width: "auto" }}
            >
              <option value="internal">Internal</option>
              <option value="client">Visible to client</option>
            </select>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as TaskPriority)}
              style={{ width: "auto" }}
            >
              {(["low", "medium", "high", "urgent"] as TaskPriority[]).map((level) => (
                <option key={level} value={level}>
                  {level} priority
                </option>
              ))}
            </select>
            <select
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              style={{ width: "auto" }}
            >
              <option value="">Unassigned</option>
              {members.data?.map((member) => (
                <option key={member.membership_id} value={member.membership_id}>
                  {member.full_name}
                </option>
              ))}
            </select>
            <input
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
              style={{ width: "auto" }}
              title="Due date"
            />
            <button
              className="primary"
              disabled={!title.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              Add
            </button>
          </div>
          <p className="muted small" style={{ marginBottom: 0 }}>
            Only people on this project can be assigned &mdash; enforced by a foreign key,
            not by this dropdown.
          </p>
        </div>
      )}

      <div className="board" style={{ marginTop: 14 }}>
        {COLUMNS.map((column) => (
          <div className="column" key={column.status}>
            <h3>
              {column.label} · {grouped(column.status).length}
            </h3>
            {grouped(column.status).map((task) => (
              <button className="task" key={task.id} onClick={() => setOpen(task)}>
                <div className="title">{task.title}</div>
                <div className="meta">
                  <span className={`badge ${task.visibility}`}>
                    {task.visibility === "internal" ? "internal" : "client"}
                  </span>
                  {task.priority !== "medium" && (
                    <span className={`badge ${task.priority}`}>{task.priority}</span>
                  )}
                  <span className={task.assignee_name ? "" : "unassigned"}>
                    {task.assignee_name ?? "unassigned"}
                  </span>
                  {task.comment_count > 0 && <span>{task.comment_count} 💬</span>}
                  {task.file_count > 0 && <span>{task.file_count} 📎</span>}
                </div>
              </button>
            ))}
          </div>
        ))}
      </div>

      {isAdmin && members.data && (
        <>
          <h2>Project team</h2>

          {/* Without this, a freshly created project is inert: tasks can only be
              assigned to project members, so an empty team means no assignees. */}
          <div className="row" style={{ marginBottom: 12 }}>
            <select
              value={newMember}
              onChange={(e) => setNewMember(e.target.value)}
              style={{ width: "auto" }}
            >
              <option value="">Add somebody to this project…</option>
              {candidates.map((candidate) => (
                <option key={candidate.membership_id} value={candidate.membership_id}>
                  {candidate.full_name} ({candidate.role.replace("agency_", "")})
                </option>
              ))}
            </select>
            <button disabled={!newMember || addMember.isPending} onClick={() => addMember.mutate()}>
              Add
            </button>
          </div>

          {members.data.length === 0 && (
            <p className="muted small">
              Nobody is on this project yet, so tasks cannot be assigned.
            </p>
          )}

          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.data.map((member) => (
                <tr key={member.membership_id}>
                  <td>
                    {member.full_name} <span className="muted small">{member.email}</span>
                  </td>
                  <td className="muted">{member.role.replace("agency_", "")}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Remove ${member.full_name} from this project?\n\n` +
                              "Their tasks stay on the board and become unassigned. " +
                              "Comments, files and logged hours are kept.",
                          )
                        ) {
                          removeMember.mutate(member.membership_id);
                        }
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {open && (
        <TaskDrawer
          task={(tasks.data ?? []).find((task) => task.id === open.id) ?? open}
          onClose={() => setOpen(null)}
        />
      )}
    </>
  );
}
