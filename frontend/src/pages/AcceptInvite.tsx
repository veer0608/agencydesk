import { useEffect, useState } from "react";

import { useAuth } from "../auth";

interface Preview {
  agency_name: string;
  email: string;
  role: string;
  expires_at: string;
}

const ROLE_LABEL: Record<string, string> = {
  agency_admin: "an agency admin",
  agency_member: "a team member",
  client_user: "a client contact",
};

/** Standalone page reached from an invite link (?token=...). */
export default function AcceptInvite({ token: inviteToken }: { token: string }) {
  const { signIn } = useAuth();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/auth/invite/${inviteToken}`)
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then(setPreview)
      .catch(() => setError("This invite link is invalid or has expired."));
  }, [inviteToken]);

  const accept = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/accept-invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: inviteToken, full_name: fullName, password }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Could not accept this invitation");

      window.history.replaceState({}, "", "/");
      await signIn(body.access_token);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not accept this invitation");
      setBusy(false);
    }
  };

  if (error && !preview) {
    return (
      <div className="center">
        <div className="card narrow stack">
          <h1>Invitation</h1>
          <div className="error">{error}</div>
          <a href="/">Back to sign in</a>
        </div>
      </div>
    );
  }

  if (!preview) return <div className="center muted">Checking your invitation…</div>;

  return (
    <div className="center">
      <form className="card narrow stack" onSubmit={accept}>
        <div>
          <h1>{preview.agency_name}</h1>
          <p className="muted small" style={{ margin: 0 }}>
            You have been invited to join as {ROLE_LABEL[preview.role] ?? preview.role}, using{" "}
            <strong>{preview.email}</strong>.
          </p>
        </div>

        <div className="notice small">
          If that address already has an AgencyDesk account, this adds{" "}
          {preview.agency_name} to it &mdash; your existing password keeps working and the
          one you type below is ignored.
        </div>

        <label>
          <span>Your name</span>
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} required />
        </label>
        <label>
          <span>Choose a password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </label>

        {error && <div className="error">{error}</div>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Joining…" : "Accept invitation"}
        </button>
      </form>
    </div>
  );
}
