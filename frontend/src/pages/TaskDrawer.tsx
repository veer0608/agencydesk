import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, type Task, type TaskPriority, type TaskStatus, type Visibility } from "../api";
import { useRole } from "../auth";

const STATUSES: TaskStatus[] = ["todo", "in_progress", "blocked", "review", "done"];
const PRIORITIES: TaskPriority[] = ["low", "medium", "high", "urgent"];

function hours(minutes: number) {
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/**
 * Task detail: conversation, time and files.
 *
 * Everything rendered here came back from an endpoint that already applied the
 * viewer's policies, so there is no filtering in this component. What a client
 * cannot see simply never arrives.
 */
export default function TaskDrawer({ task, onClose }: { task: Task; onClose: () => void }) {
  const { isStaff, isClient, isAdmin } = useRole();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [body, setBody] = useState("");
  const [commentVisibility, setCommentVisibility] = useState<Visibility>("internal");
  const [minutes, setMinutes] = useState("30");
  const [timeNote, setTimeNote] = useState("");
  // Defaults to today, but agencies routinely log Friday's work on Monday.
  const [entryDate, setEntryDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [uploadVisibility, setUploadVisibility] = useState<Visibility>("internal");
  const [error, setError] = useState<string | null>(null);

  const comments = useQuery({
    queryKey: ["comments", task.id],
    queryFn: () => api.comments(task.id),
  });
  const timeEntries = useQuery({
    queryKey: ["time", task.id],
    queryFn: () => api.timeEntries(task.id),
  });
  const files = useQuery({ queryKey: ["files", task.id], queryFn: () => api.files(task.id) });
  // Only project members can be assigned -- the API enforces it with a foreign
  // key, so this list is a convenience rather than the check.
  const members = useQuery({
    queryKey: ["members", task.project_id],
    queryFn: () => api.members(task.project_id),
    enabled: isStaff,
  });

  const flipped: Visibility = task.visibility === "internal" ? "client" : "internal";

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Something went wrong");

  const addComment = useMutation({
    mutationFn: () => api.addComment(task.id, body, isClient ? "client" : commentVisibility),
    onSuccess: () => {
      setBody("");
      setError(null);
      void comments.refetch();
      invalidate();
    },
    onError: fail,
  });

  const logTime = useMutation({
    mutationFn: () => api.logTime(task.id, Number(minutes), timeNote, entryDate),
    onSuccess: () => {
      setTimeNote("");
      setError(null);
      void timeEntries.refetch();
      invalidate();
    },
    onError: fail,
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadFile(task.id, file, uploadVisibility),
    onSuccess: () => {
      setError(null);
      if (fileInput.current) fileInput.current.value = "";
      void files.refetch();
      invalidate();
    },
    onError: fail,
  });

  const decide = useMutation({
    mutationFn: (input: { id: string; status: "approved" | "needs_changes"; note: string | null }) =>
      api.setApproval(input.id, input.status, input.note),
    onSuccess: () => {
      setError(null);
      void files.refetch();
      invalidate();
    },
    onError: fail,
  });

  // One mutation for every field on the task. The server decides what is
  // allowed; this component only has to send the change.
  const edit = useMutation({
    mutationFn: (changes: Record<string, unknown>) => api.updateTask(task.id, changes),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: fail,
  });

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer">
        <div className="between">
          <span className={`badge ${task.visibility}`}>
            {task.visibility === "internal" ? "Internal only" : "Visible to client"}
          </span>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </div>

        <h1 style={{ marginTop: 12 }}>{task.title}</h1>
        <p className="muted small">
          {task.project_name} &middot; {task.priority} priority &middot;{" "}
          {task.assignee_name ?? <span className="unassigned">unassigned</span>}
          {task.due_date && ` · due ${task.due_date}`}
        </p>
        {task.description && <p>{task.description}</p>}

        {error && <div className="error">{error}</div>}

        {isStaff && (
          <div className="stack" style={{ gap: 8, marginTop: 12 }}>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <select
                value={task.status}
                onChange={(e) => edit.mutate({ status: e.target.value as TaskStatus })}
                style={{ width: "auto" }}
              >
                {STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {status.replace("_", " ")}
                  </option>
                ))}
              </select>

              <select
                value={task.priority}
                onChange={(e) => edit.mutate({ priority: e.target.value as TaskPriority })}
                style={{ width: "auto" }}
              >
                {PRIORITIES.map((priority) => (
                  <option key={priority} value={priority}>
                    {priority} priority
                  </option>
                ))}
              </select>

              <button onClick={() => edit.mutate({ visibility: flipped })}>
                {task.visibility === "internal" ? "Share with client" : "Make internal"}
              </button>
            </div>

            <div className="row" style={{ flexWrap: "wrap" }}>
              <select
                value={task.assignee_membership_id ?? ""}
                onChange={(e) =>
                  edit.mutate(
                    e.target.value
                      ? { assignee_membership_id: e.target.value }
                      : { clear_assignee: true },
                  )
                }
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
                value={task.due_date ?? ""}
                onChange={(e) =>
                  edit.mutate(
                    e.target.value ? { due_date: e.target.value } : { clear_due_date: true },
                  )
                }
                style={{ width: "auto" }}
              />
            </div>
          </div>
        )}

        {/* --- conversation --- */}
        <h2>Conversation</h2>
        {comments.data?.length === 0 && <p className="muted small">Nothing here yet.</p>}
        {comments.data?.map((comment) => (
          <div key={comment.id} className={`comment ${comment.visibility}`}>
            <div className="row small muted">
              <strong style={{ color: "var(--text)" }}>{comment.author_name ?? "Removed user"}</strong>
              <span className={`badge ${comment.visibility}`}>
                {comment.visibility === "internal" ? "internal" : "client"}
              </span>
              <span>{new Date(comment.created_at).toLocaleString()}</span>
            </div>
            <div>{comment.body}</div>
          </div>
        ))}

        <div className="stack" style={{ marginTop: 10 }}>
          <textarea
            rows={3}
            placeholder={isClient ? "Reply to your agency…" : "Add a comment…"}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <div className="between">
            {isStaff ? (
              <select
                value={commentVisibility}
                onChange={(e) => setCommentVisibility(e.target.value as Visibility)}
                style={{ width: "auto" }}
                disabled={task.visibility === "internal"}
              >
                <option value="internal">Internal note</option>
                <option value="client">Visible to client</option>
              </select>
            ) : (
              <span className="muted small">Your agency will see this.</span>
            )}
            <button
              className="primary"
              disabled={!body.trim() || addComment.isPending}
              onClick={() => addComment.mutate()}
            >
              Post
            </button>
          </div>
          {isStaff && task.visibility === "internal" && (
            <p className="muted small" style={{ margin: 0 }}>
              This task is internal, so every comment on it is internal too.
            </p>
          )}
        </div>

        {/* --- files --- */}
        <h2>Files</h2>
        {files.data?.length === 0 && <p className="muted small">No attachments.</p>}
        {files.data?.map((record) => (
          <div key={record.id} className="card" style={{ marginBottom: 8, padding: 12 }}>
            <div className="between">
              <div>
                <div>{record.filename}</div>
                <div className="muted small">
                  {(record.size_bytes / 1024).toFixed(0)} KB &middot; {record.uploaded_by_name ?? "—"}
                </div>
              </div>
              <span className={`badge ${record.visibility}`}>{record.visibility}</span>
            </div>

            <div className="row small" style={{ marginTop: 8, flexWrap: "wrap" }}>
              <span className="muted">Approval:</span>
              <strong>{record.approval_status.replace("_", " ")}</strong>
              {record.approved_by_name && <span className="muted">by {record.approved_by_name}</span>}
              <button
                className="link"
                onClick={() =>
                  api.downloadFile(record.id, record.filename).catch(fail)
                }
              >
                download
              </button>
            </div>
            {record.approval_note && <p className="small muted">“{record.approval_note}”</p>}

            {(isClient || isAdmin) && record.visibility === "client" && (
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  onClick={() =>
                    decide.mutate({ id: record.id, status: "approved", note: null })
                  }
                >
                  Approve
                </button>
                <button
                  onClick={() =>
                    decide.mutate({
                      id: record.id,
                      status: "needs_changes",
                      note: window.prompt("What needs changing?") ?? null,
                    })
                  }
                >
                  Request changes
                </button>
              </div>
            )}
          </div>
        ))}

        {isStaff && (
          <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <input
              ref={fileInput}
              type="file"
              style={{ width: "auto" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) upload.mutate(file);
              }}
            />
            <select
              value={uploadVisibility}
              onChange={(e) => setUploadVisibility(e.target.value as Visibility)}
              style={{ width: "auto" }}
              disabled={task.visibility === "internal"}
            >
              <option value="internal">Internal</option>
              <option value="client">Share with client</option>
            </select>
          </div>
        )}

        {/* --- time --- */}
        <h2>Time logged &mdash; {hours(task.minutes_logged)}</h2>
        {timeEntries.data?.map((entry) => (
          <div key={entry.id} className="between small" style={{ padding: "4px 0" }}>
            <span>
              {entry.member_name ?? "—"} &middot; {entry.note || "no note"}
            </span>
            <span className="muted">
              {entry.entry_date} &middot; {hours(entry.minutes)}
            </span>
          </div>
        ))}

        {isStaff && (
          <div className="row" style={{ marginTop: 10 }}>
            <input
              type="number"
              min={1}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              style={{ width: 90 }}
            />
            <input
              placeholder="What did you work on?"
              value={timeNote}
              onChange={(e) => setTimeNote(e.target.value)}
            />
            <input
              type="date"
              value={entryDate}
              onChange={(e) => setEntryDate(e.target.value)}
              style={{ width: "auto" }}
            />
            <button onClick={() => logTime.mutate()} disabled={logTime.isPending}>
              Log
            </button>
          </div>
        )}
      </aside>
    </>
  );
}
