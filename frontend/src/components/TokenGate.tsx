import { useState } from "react";
import { getApiBase, setApiBase, setToken } from "../api";

export function TokenGate({ onReady }: { onReady: () => void }) {
  const [token, setTok] = useState("");
  const [base, setBase] = useState(getApiBase());

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setApiBase(base.trim());
    setToken(token.trim());
    onReady();
  }

  return (
    <div className="app">
      <form className="card gate" onSubmit={submit}>
        <h1>
          entrygraph <span style={{ color: "var(--accent)" }}>Sentinel</span>
        </h1>
        <p>Enter the API token (SENTINEL_API_TOKEN) to view scans and findings.</p>
        <div className="field">
          <label>API token</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setTok(e.target.value)}
            placeholder="bearer token"
            autoFocus
          />
        </div>
        <div className="field">
          <label>API base URL (blank = same origin)</label>
          <input
            value={base}
            onChange={(e) => setBase(e.target.value)}
            placeholder="https://sentinel.example.com"
          />
        </div>
        <button className="btn primary" type="submit" style={{ width: "100%" }}>
          Connect
        </button>
      </form>
    </div>
  );
}
