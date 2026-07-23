import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";

/**
 * Create a client, and a project for that client.
 *
 * Admin-only, and the client dropdown only ever contains this agency's clients —
 * but that is a convenience, not the control. Posting another tenant's client id
 * by hand returns 404: the clients policy hid the row, and the composite foreign
 * key `projects(client_id, agency_id) -> clients(id, agency_id)` would reject the
 * insert even if it had not.
 */
export default function NewProject({ onCreated }: { onCreated: (id: string) => void }) {
  const queryClient = useQueryClient();
  const clients = useQuery({ queryKey: ["clients"], queryFn: api.clients });

  const [clientId, setClientId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [newClient, setNewClient] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Something went wrong");

  const addClient = useMutation({
    mutationFn: () => api.createClient({ name: newClient.trim() }),
    onSuccess: (client) => {
      setError(null);
      setNewClient("");
      setClientId(client.id);
      void clients.refetch();
    },
    onError: fail,
  });

  const create = useMutation({
    mutationFn: () => api.createProject({ client_id: clientId, name, description }),
    onSuccess: (project) => {
      setError(null);
      setName("");
      setDescription("");
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      onCreated(project.id);
    },
    onError: fail,
  });

  if (clients.isError) return <p className="error">Only agency admins can create projects.</p>;

  return (
    <>
      <h1>New project</h1>
      <p className="muted small">Projects belong to a client. Start by picking one.</p>

      {error && <div className="error" style={{ margin: "12px 0" }}>{error}</div>}

      <div className="card" style={{ margin: "14px 0", maxWidth: 620 }}>
        <label>
          <span>Client</span>
          <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">Choose a client…</option>
            {clients.data?.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name} ({client.project_count} project
                {client.project_count === 1 ? "" : "s"})
              </option>
            ))}
          </select>
        </label>

        <div className="row" style={{ marginBottom: 16 }}>
          <input
            placeholder="…or add a new client"
            value={newClient}
            onChange={(e) => setNewClient(e.target.value)}
          />
          <button
            disabled={!newClient.trim() || addClient.isPending}
            onClick={() => addClient.mutate()}
          >
            Add client
          </button>
        </div>

        <label>
          <span>Project name</span>
          <input
            placeholder="Harbor Foods Rebrand"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          <span>Description</span>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <button
          className="primary"
          disabled={!clientId || !name.trim() || create.isPending}
          onClick={() => create.mutate()}
        >
          Create project
        </button>
      </div>

      <h2>Clients</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Contact</th>
            <th>Projects</th>
          </tr>
        </thead>
        <tbody>
          {clients.data?.map((client) => (
            <tr key={client.id}>
              <td>{client.name}</td>
              <td className="muted">{client.contact_email ?? "—"}</td>
              <td className="muted">{client.project_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
