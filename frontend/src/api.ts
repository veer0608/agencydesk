/**
 * API client.
 *
 * The session token is the only client-side state that matters, and it is
 * opaque: it carries no role and no agency id, so the UI cannot decide what
 * someone may see. It asks the server and renders the answer.
 */

const TOKEN_KEY = "agencydesk.token";

export type Role = "agency_admin" | "agency_member" | "client_user";
export type Visibility = "internal" | "client";
export type TaskStatus = "todo" | "in_progress" | "blocked" | "review" | "done";
export type TaskPriority = "low" | "medium" | "high" | "urgent";
export type ApprovalStatus = "pending" | "approved" | "needs_changes";

export interface Me {
  membership_id: string;
  user_id: string;
  agency_id: string;
  agency_name: string;
  role: Role;
  client_id: string | null;
  full_name: string;
  email: string;
}

export interface MembershipOption {
  membership_id: string;
  agency_id: string;
  agency_name: string;
  agency_slug: string;
  role: Role;
  client_id: string | null;
  client_name: string | null;
}

export interface LoginResponse {
  user_id: string;
  full_name: string;
  memberships: MembershipOption[];
  access_token: string | null;
}

export interface Client {
  id: string;
  name: string;
  contact_email: string | null;
  project_count: number;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  status: "active" | "on_hold" | "completed" | "archived";
  client_id: string;
  client_name: string;
  created_at: string;
}

export interface Task {
  id: string;
  project_id: string;
  project_name: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  visibility: Visibility;
  assignee_membership_id: string | null;
  assignee_name: string | null;
  due_date: string | null;
  created_at: string;
  updated_at: string;
  comment_count: number;
  file_count: number;
  minutes_logged: number;
}

export interface Comment {
  id: string;
  task_id: string;
  body: string;
  visibility: Visibility;
  author_name: string | null;
  author_role: Role | null;
  created_at: string;
}

export interface TimeEntry {
  id: string;
  task_id: string;
  minutes: number;
  note: string;
  entry_date: string;
  member_name: string | null;
  created_at: string;
}

export interface FileRecord {
  id: string;
  task_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  visibility: Visibility;
  approval_status: ApprovalStatus;
  approval_note: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  uploaded_by_name: string | null;
  created_at: string;
}

export interface ProjectMember {
  membership_id: string;
  full_name: string;
  email: string;
  role: Role;
  added_at: string;
}

export type AgencyStaff = Omit<ProjectMember, "added_at">;

export interface Dashboard {
  project_id: string;
  project_name: string;
  client_name: string;
  viewer_role: Role;
  tasks_by_status: { status: TaskStatus; count: number }[];
  total_tasks: number;
  open_tasks: number;
  overdue_tasks: number;
  minutes_logged: number;
  files_awaiting_approval: number;
  scope_note: string;
}

export interface SearchHit {
  kind: "task" | "comment" | "file";
  task_id: string;
  project_id: string;
  project_name: string;
  title: string;
  snippet: string;
  visibility: Visibility;
}

export interface Invite {
  id: string;
  email: string;
  role: Role;
  client_id: string | null;
  status: "pending" | "accepted" | "revoked";
  expires_at: string;
  created_at: string;
  accepted_at: string | null;
  invite_url: string | null;
  resent: boolean;
}

export interface RemovalResult {
  removed_membership_id: string;
  unassigned_task_ids: string[];
  detail: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (value: string) => localStorage.setItem(TOKEN_KEY, value),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

async function request<T>(
  path: string,
  options: RequestInit & { raw?: boolean } = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const current = token.get();
  if (current) headers.set("Authorization", `Bearer ${current}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    // fetch only rejects when the request never got a reply at all.
    throw new ApiError(0, "Cannot reach the API. Is the backend running?");
  }

  // A dev-server proxy with nothing behind it answers 502/504, and Vite reports
  // a refused connection as a 500 with an HTML body -- all of which would
  // otherwise surface to the user as a bare "Internal Server Error" on the login
  // form, which sends you looking in the wrong place entirely.
  if (response.status >= 500 && !response.headers.get("content-type")?.includes("json")) {
    throw new ApiError(
      response.status,
      "Cannot reach the API. Is the backend running on port 8000?",
    );
  }

  if (response.status === 401 && current) {
    token.clear();
    window.location.reload();
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const patch = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = <T,>(path: string) => request<T>(path, { method: "DELETE" });

export const api = {
  login: (email: string, password: string) =>
    post<LoginResponse>("/auth/login", { email, password }),
  selectAgency: (email: string, password: string, membership_id: string) =>
    post<{ access_token: string; principal: Me }>("/auth/select-agency", {
      email,
      password,
      membership_id,
    }),
  switchAgency: (membership_id: string) =>
    post<{ access_token: string; principal: Me }>(`/auth/switch-agency/${membership_id}`),
  me: () => get<Me>("/auth/me"),
  myAgencies: () => get<MembershipOption[]>("/auth/my-agencies"),

  clients: () => get<Client[]>("/clients"),
  createClient: (body: { name: string; contact_email?: string | null }) =>
    post<Client>("/clients", body),
  projects: () => get<Project[]>("/projects"),
  project: (id: string) => get<Project>(`/projects/${id}`),
  createProject: (body: { client_id: string; name: string; description?: string }) =>
    post<Project>("/projects", body),

  members: (projectId: string) => get<ProjectMember[]>(`/projects/${projectId}/members`),
  agencyStaff: () => get<AgencyStaff[]>("/agency/staff"),

  addMember: (projectId: string, membership_id: string) =>
    post<ProjectMember[]>(`/projects/${projectId}/members`, { membership_id }),

  removeMember: (projectId: string, membershipId: string) =>
    del<RemovalResult>(`/projects/${projectId}/members/${membershipId}`),

  tasks: (projectId: string, params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return get<Task[]>(`/projects/${projectId}/tasks${query ? `?${query}` : ""}`);
  },
  task: (id: string) => get<Task>(`/tasks/${id}`),
  createTask: (projectId: string, body: Partial<Task>) =>
    post<Task>(`/projects/${projectId}/tasks`, body),
  updateTask: (id: string, body: Record<string, unknown>) => patch<Task>(`/tasks/${id}`, body),

  comments: (taskId: string) => get<Comment[]>(`/tasks/${taskId}/comments`),
  addComment: (taskId: string, body: string, visibility: Visibility) =>
    post<Comment>(`/tasks/${taskId}/comments`, { body, visibility }),

  timeEntries: (taskId: string) => get<TimeEntry[]>(`/tasks/${taskId}/time-entries`),
  logTime: (taskId: string, minutes: number, note: string, entry_date?: string) =>
    post<TimeEntry>(`/tasks/${taskId}/time-entries`, { minutes, note, entry_date }),

  files: (taskId: string) => get<FileRecord[]>(`/tasks/${taskId}/files`),
  uploadFile: (taskId: string, file: File, visibility: Visibility) => {
    const form = new FormData();
    form.append("upload", file);
    form.append("visibility", visibility);
    return request<FileRecord>(`/tasks/${taskId}/files`, { method: "POST", body: form });
  },
  setApproval: (fileId: string, approval_status: ApprovalStatus, note: string | null) =>
    patch<FileRecord>(`/files/${fileId}/approval`, { approval_status, note }),

  /**
   * Download an attachment.
   *
   * Not an `<a href>`: the browser would issue that request without the
   * Authorization header, and every download would 401. Fetch the blob with the
   * session token, then hand it to the browser as an object URL.
   */
  downloadFile: async (fileId: string, filename: string) => {
    const headers = new Headers();
    const current = token.get();
    if (current) headers.set("Authorization", `Bearer ${current}`);

    const response = await fetch(`/api/files/${fileId}/download`, { headers });
    if (!response.ok) {
      throw new ApiError(response.status, "That file is not available to you");
    }

    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  },

  dashboard: (projectId: string) => get<Dashboard>(`/projects/${projectId}/dashboard`),
  search: (q: string) => get<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),

  invites: () => get<Invite[]>("/invites"),
  createInvite: (body: { email: string; role: Role; client_id?: string | null }) =>
    post<Invite>("/invites", body),
  revokeInvite: (id: string) => del<Invite>(`/invites/${id}`),
};
