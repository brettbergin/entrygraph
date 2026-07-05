import { useState } from "react";
import { Button, FormControl, Heading, TextInput } from "@primer/react";
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
    <div className="app" style={{ maxWidth: 440 }}>
      <form className="card" style={{ padding: 28, marginTop: "12vh" }} onSubmit={submit}>
        <Heading as="h1" className="mb1" style={{ fontSize: 24 }}>
          entrygraph <span className="accent">Sentinel</span>
        </Heading>
        <p className="muted mb3">
          Enter the API token (SENTINEL_API_TOKEN) to view scans and findings.
        </p>
        <div className="field">
          <FormControl>
            <FormControl.Label>API token</FormControl.Label>
            <TextInput
              type="password"
              block
              value={token}
              onChange={(e) => setTok(e.target.value)}
              placeholder="bearer token"
              autoFocus
            />
          </FormControl>
        </div>
        <div className="field">
          <FormControl>
            <FormControl.Label>API base URL</FormControl.Label>
            <FormControl.Caption>Blank = same origin</FormControl.Caption>
            <TextInput
              block
              value={base}
              onChange={(e) => setBase(e.target.value)}
              placeholder="https://sentinel.example.com"
            />
          </FormControl>
        </div>
        <Button type="submit" variant="primary" block>
          Connect
        </Button>
      </form>
    </div>
  );
}
