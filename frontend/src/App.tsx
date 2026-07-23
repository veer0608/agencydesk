import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "./api";
import { useAuth, useRole } from "./auth";
import AcceptInvite from "./pages/AcceptInvite";
import Board from "./pages/Board";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import NewProject from "./pages/NewProject";
import Search from "./pages/Search";
import Team from "./pages/Team";

type View =
  | { kind: "board" | "dashboard"; projectId: string }
  | { kind: "search" | "team" | "new-project" };

const ROLE_LABEL: Record<string, string> = {
  agency_admin: "Agency admin",
  agency_member: "Team member",
  client_user: "Client contact",
};

/**
 * The agency switcher.
 *
 * Deliberately prominent: it is the visible half of the identity model. Picking
 * another agency asks the server for a brand new token, so nothing about the
 * previous tenant survives the switch.
 */
function AgencySwitcher() {
  const { me, signIn } = useAuth();
  const agencies = useQuery({ queryKey: ["my-agencies"], queryFn: api.myAgencies });

  if (!me || (agencies.data?.length ?? 0) < 2) return null;

  return (
    <label style={{ marginBottom: 0 }}>
      <span>Acting as</span>
      <select
        value={me.membership_id}
        onChange={async (event) => {
          const response = await api.switchAgency(event.target.value);
          await signIn(response.access_token);
          window.location.reload();
        }}
      >
        {agencies.data?.map((option) => (
          <option key={option.membership_id} value={option.membership_id}>
            {option.agency_name} — {ROLE_LABEL[option.role]}
          </option>
        ))}
      </select>
    </label>
  );
}

function Shell() {
  const { me, signOut } = useAuth();
  const { isAdmin, isClient } = useRole();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [view, setView] = useState<View | null>(null);

  useEffect(() => {
    if (!view && projects.data?.length) {
      setView({ kind: "board", projectId: projects.data[0].id });
    }
  }, [projects.data, view]);

  const activeProject = view && "projectId" in view ? view.projectId : null;

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">AgencyDesk</div>

        <div className="whoami">
          <div className="agency">{me?.agency_name}</div>
          <div className="muted small">
            {me?.full_name} &middot; {ROLE_LABEL[me?.role ?? ""]}
          </div>
          {isClient && (
            <div className="badge client" style={{ marginTop: 6 }}>
              client portal
            </div>
          )}
        </div>

        <AgencySwitcher />

        <div className="stack" style={{ gap: 2 }}>
          <div className="muted small" style={{ textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Projects
          </div>
          {projects.data?.length === 0 && (
            <div className="muted small">
              {isClient ? "Nothing shared yet." : "No projects yet."}
            </div>
          )}
          {projects.data?.map((project) => (
            <button
              key={project.id}
              className={`nav-item ${activeProject === project.id ? "active" : ""}`}
              onClick={() => setView({ kind: "board", projectId: project.id })}
            >
              {project.name}
              <div className="small muted">{project.client_name}</div>
            </button>
          ))}
          {isAdmin && (
            <button
              className={`nav-item ${view?.kind === "new-project" ? "active" : ""}`}
              onClick={() => setView({ kind: "new-project" })}
            >
              + New project
            </button>
          )}
        </div>

        {activeProject && (
          <div className="stack" style={{ gap: 2 }}>
            <button
              className={`nav-item ${view?.kind === "board" ? "active" : ""}`}
              onClick={() => setView({ kind: "board", projectId: activeProject })}
            >
              Board
            </button>
            <button
              className={`nav-item ${view?.kind === "dashboard" ? "active" : ""}`}
              onClick={() => setView({ kind: "dashboard", projectId: activeProject })}
            >
              Dashboard
            </button>
          </div>
        )}

        <div className="stack" style={{ gap: 2, marginTop: "auto" }}>
          <button
            className={`nav-item ${view?.kind === "search" ? "active" : ""}`}
            onClick={() => setView({ kind: "search" })}
          >
            Search
          </button>
          {isAdmin && (
            <button
              className={`nav-item ${view?.kind === "team" ? "active" : ""}`}
              onClick={() => setView({ kind: "team" })}
            >
              Team &amp; invites
            </button>
          )}
          <button className="nav-item" onClick={signOut}>
            Sign out
          </button>
        </div>
      </nav>

      <main className="main">
        {projects.isLoading && <p className="muted">Loading…</p>}
        {view?.kind === "board" && <Board projectId={view.projectId} />}
        {view?.kind === "dashboard" && <Dashboard projectId={view.projectId} />}
        {view?.kind === "search" && <Search />}
        {view?.kind === "team" && <Team />}
        {view?.kind === "new-project" && (
          <NewProject onCreated={(id) => setView({ kind: "board", projectId: id })} />
        )}
        {!view && !projects.isLoading && (
          <p className="muted">Nothing has been shared with you yet.</p>
        )}
      </main>
    </div>
  );
}

export default function App() {
  const { me, loading } = useAuth();
  const inviteToken = new URLSearchParams(window.location.search).get("token");

  if (inviteToken && !me) return <AcceptInvite token={inviteToken} />;
  if (loading) return <div className="center muted">Loading…</div>;
  if (!me) return <Login />;
  return <Shell />;
}
