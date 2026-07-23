import { useState } from "react";

import { api, type MembershipOption } from "../api";
import { useAuth } from "../auth";

const ROLE_LABEL: Record<string, string> = {
  agency_admin: "Agency admin",
  agency_member: "Team member",
  client_user: "Client contact",
};

/**
 * Login, then -- only when it is genuinely ambiguous -- the agency picker.
 *
 * Somebody who belongs to one agency never sees step two. Somebody who is a
 * client at one agency and staff at another always does, because there is no
 * sensible way to choose for them.
 */
export default function Login() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("ada@northwind.test");
  const [password, setPassword] = useState("password123");
  const [options, setOptions] = useState<MembershipOption[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const response = await api.login(email, password);
      if (response.access_token) {
        await signIn(response.access_token);
      } else {
        setOptions(response.memberships);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  };

  const choose = async (membership: MembershipOption) => {
    setBusy(true);
    try {
      const chosen = await api.selectAgency(email, password, membership.membership_id);
      await signIn(chosen.access_token);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open that agency");
      setBusy(false);
    }
  };

  if (options) {
    return (
      <div className="center">
        <div className="card narrow stack">
          <div>
            <h1>Choose an agency</h1>
            <p className="muted small" style={{ margin: 0 }}>
              This email is known at more than one agency. A session belongs to exactly
              one of them &mdash; you can switch later without signing in again.
            </p>
          </div>
          {options.map((option) => (
            <button
              key={option.membership_id}
              className="task"
              onClick={() => void choose(option)}
              disabled={busy}
            >
              <div className="title">{option.agency_name}</div>
              <div className="meta">
                <span className={`badge ${option.role === "client_user" ? "client" : ""}`}>
                  {ROLE_LABEL[option.role]}
                </span>
                {option.client_name && <span>on behalf of {option.client_name}</span>}
              </div>
            </button>
          ))}
          {error && <div className="error">{error}</div>}
          <button className="link" onClick={() => setOptions(null)}>
            Use a different email
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="center">
      <form className="card narrow stack" onSubmit={submit}>
        <div>
          <h1>AgencyDesk</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Client &amp; project management for agencies.
          </p>
        </div>

        <label>
          <span>Email</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </label>
        <label>
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error && <div className="error">{error}</div>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <details>
          <summary className="muted small" style={{ cursor: "pointer" }}>
            Demo accounts (all use <code>password123</code>)
          </summary>
          <table className="small" style={{ marginTop: 10 }}>
            <tbody>
              {[
                ["ada@northwind.test", "Northwind admin — sees everything"],
                ["ben@northwind.test", "Northwind member — Rebrand only"],
                ["cleo@northwind.test", "Northwind member — Catalog only"],
                ["mia@harborfoods.test", "Client at Northwind AND admin at Bluepeak"],
                ["raj@bluepeak.test", "Bluepeak admin — the other tenant"],
              ].map(([account, description]) => (
                <tr key={account}>
                  <td>
                    <button
                      className="link"
                      type="button"
                      onClick={() => {
                        setEmail(account);
                        setPassword("password123");
                      }}
                    >
                      {account}
                    </button>
                  </td>
                  <td className="muted">{description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </form>
    </div>
  );
}
