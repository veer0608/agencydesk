import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Role } from "../api";

/**
 * Invitations.
 *
 * The one thing worth watching here: hit "Invite" twice with the same address
 * and the row count does not move. The second call updates the existing pending
 * invite and rotates its token.
 */
export default function Team() {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("agency_member");
  const [clientId, setClientId] = useState("");
  const [lastLink, setLastLink] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const invites = useQuery({ queryKey: ["invites"], queryFn: api.invites });
  const clients = useQuery({ queryKey: ["clients"], queryFn: api.clients });

  const send = useMutation({
    mutationFn: () =>
      api.createInvite({
        email,
        role,
        client_id: role === "client_user" ? clientId || null : null,
      }),
    onSuccess: (invite) => {
      setError(null);
      setLastLink(invite.invite_url);
      setNote(
        invite.resent
          ? "Existing invitation reused — same row, new token. The previous link no longer works."
          : "New invitation created.",
      );
      void invites.refetch();
    },
    onError: (caught) =>
      setError(caught instanceof Error ? caught.message : "Could not send that invite"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeInvite(id),
    onSuccess: () => void invites.refetch(),
  });

  if (invites.isError) return <p className="error">Only agency admins can manage invitations.</p>;

  return (
    <>
      <h1>Team &amp; invitations</h1>
      <p className="muted small">
        There is no mail server in this build, so the invite link is handed straight back
        to you.
      </p>

      <div className="card" style={{ margin: "14px 0" }}>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input
            placeholder="person@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ flex: 2, minWidth: 220 }}
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            style={{ width: "auto" }}
          >
            <option value="agency_member">Team member</option>
            <option value="agency_admin">Agency admin</option>
            <option value="client_user">Client contact</option>
          </select>
          {role === "client_user" && (
            <select
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              style={{ width: "auto" }}
            >
              <option value="">Choose a client…</option>
              {clients.data?.map((client) => (
                <option key={client.id} value={client.id}>
                  {client.name}
                </option>
              ))}
            </select>
          )}
          <button
            className="primary"
            disabled={!email.trim() || send.isPending}
            onClick={() => send.mutate()}
          >
            Invite
          </button>
        </div>

        {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}
        {note && (
          <div className="notice" style={{ marginTop: 10 }}>
            <div>{note}</div>
            {lastLink && (
              <div className="small" style={{ marginTop: 6 }}>
                <code>{lastLink}</code>
              </div>
            )}
          </div>
        )}
      </div>

      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Role</th>
            <th>Status</th>
            <th>Expires</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {invites.data?.map((invite) => (
            <tr key={invite.id}>
              <td>{invite.email}</td>
              <td className="muted">{invite.role.replace("agency_", "")}</td>
              <td>
                <span className={`badge ${invite.status === "pending" ? "internal" : "client"}`}>
                  {invite.status}
                </span>
              </td>
              <td className="muted small">{new Date(invite.expires_at).toLocaleDateString()}</td>
              <td style={{ textAlign: "right" }}>
                {invite.status === "pending" && (
                  <button className="danger" onClick={() => revoke.mutate(invite.id)}>
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
