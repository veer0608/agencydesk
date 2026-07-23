import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { useRole } from "../auth";

/**
 * Cross-project search.
 *
 * Worth opening during a review: log in as the agency, search "margin", see the
 * internal task. Log in as the client, search the same word, get nothing. Same
 * endpoint, same tables, same SQL.
 */
export default function Search() {
  const { isClient } = useRole();
  const [term, setTerm] = useState("");
  const [submitted, setSubmitted] = useState("");

  const results = useQuery({
    queryKey: ["search", submitted],
    queryFn: () => api.search(submitted),
    enabled: submitted.length >= 2,
  });

  return (
    <>
      <h1>Search</h1>
      <p className="muted small">Tasks, comments and filenames across every project you can see.</p>

      <form
        className="row"
        style={{ margin: "14px 0", maxWidth: 520 }}
        onSubmit={(event) => {
          event.preventDefault();
          setSubmitted(term.trim());
        }}
      >
        <input
          placeholder="Try: moodboard, margin, supplier…"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
        <button className="primary" type="submit">
          Search
        </button>
      </form>

      {submitted.length >= 2 && results.data?.length === 0 && (
        <p className="muted">
          No matches for “{submitted}”.
          {isClient && " Anything your agency is keeping internal will not appear here."}
        </p>
      )}

      {results.data?.map((hit, index) => (
        <div className="card" key={`${hit.kind}-${hit.task_id}-${index}`} style={{ marginBottom: 8 }}>
          <div className="between">
            <div>
              <div>{hit.title}</div>
              <div className="muted small">
                {hit.kind} in {hit.project_name}
              </div>
            </div>
            <span className={`badge ${hit.visibility}`}>{hit.visibility}</span>
          </div>
          {hit.snippet && <p className="small muted" style={{ marginBottom: 0 }}>{hit.snippet}</p>}
        </div>
      ))}
    </>
  );
}
