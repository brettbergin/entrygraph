import { Button, Flash, FormControl, Heading, Label, Select, TextInput } from "@primer/react";
import { KeyIcon } from "@primer/octicons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, keys } from "../../api/queries";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";

export function SettingsPage() {
  const { me } = useAuth();
  return (
    <>
      <Heading as="h1" style={{ fontSize: 28, marginBottom: 16 }}>
        Settings
      </Heading>

      <div className="card" style={{ padding: 20, marginBottom: 20, maxWidth: 560 }}>
        <b>Profile</b>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted fs0" style={{ width: 90 }}>
            USER
          </span>
          <span>{me?.user.name}</span>
        </div>
        <div className="row" style={{ marginTop: 4 }}>
          <span className="muted fs0" style={{ width: 90 }}>
            ROLE
          </span>
          <Label variant={me?.user.role === "admin" ? "accent" : "secondary"}>
            {me?.user.role}
          </Label>
        </div>
        <div className="row" style={{ marginTop: 4 }}>
          <span className="muted fs0" style={{ width: 90 }}>
            AUTH
          </span>
          <span className="muted fs0">
            {me?.auth_disabled ? "disabled (local dev mode)" : "Authentik SSO"}
          </span>
        </div>
      </div>

      {me?.auth_disabled ? (
        <div className="card" style={{ padding: 20, maxWidth: 560 }}>
          <b>API keys</b>
          <p className="muted fs0">
            API keys are available when signed in with SSO. In local dev mode the server has no
            authentication, so a bearer key isn't needed.
          </p>
        </div>
      ) : (
        <ApiKeysSection />
      )}
    </>
  );
}

function ApiKeysSection() {
  const queryClient = useQueryClient();
  const { data, isPending, error } = useQuery({ queryKey: keys.apiKeys, queryFn: api.apiKeys });
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [freshToken, setFreshToken] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.createApiKey({ name, role }),
    onSuccess: (r) => {
      setFreshToken(r.token);
      setName("");
      void queryClient.invalidateQueries({ queryKey: keys.apiKeys });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeApiKey(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apiKeys }),
  });

  return (
    <div className="card" style={{ padding: 20, maxWidth: 640 }}>
      <div className="row">
        <KeyIcon />
        <b>API keys</b>
      </div>
      <p className="muted fs0">
        Use a key as a bearer token for programmatic access (CLI/CI):{" "}
        <code className="mono">Authorization: Bearer egk_…</code>. A key can't exceed your own role.
      </p>

      {freshToken && (
        <Flash variant="success" style={{ marginBottom: 12 }}>
          Copy your new key now — it won't be shown again:
          <pre className="mono fs0" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
            {freshToken}
          </pre>
        </Flash>
      )}

      <div className="row wrap" style={{ alignItems: "end", marginBottom: 16 }}>
        <FormControl>
          <FormControl.Label>Name</FormControl.Label>
          <TextInput
            placeholder="ci-pipeline"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormControl>
        <FormControl>
          <FormControl.Label>Role</FormControl.Label>
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            <Select.Option value="viewer">viewer</Select.Option>
            <Select.Option value="admin">admin</Select.Option>
          </Select>
        </FormControl>
        <Button variant="primary" disabled={!name || create.isPending} onClick={() => create.mutate()}>
          Create key
        </Button>
      </div>
      {create.error && <ErrorFlash message={String(create.error)} />}

      {isPending ? (
        <Loading />
      ) : error ? (
        <ErrorFlash message={String(error)} />
      ) : data.length === 0 ? (
        <EmptyState title="No API keys" body="Create one above for CLI or CI access." />
      ) : (
        <div className="card">
          {data.map((k, i) => (
            <div
              key={k.id}
              className="row"
              style={{ padding: "10px 14px", borderTop: i ? "1px solid var(--border)" : undefined }}
            >
              <span style={{ fontWeight: 600 }}>{k.name}</span>
              <Label size="small">{k.role}</Label>
              <span className="mono fs0 muted">{k.prefix}…</span>
              <span className="spacer" />
              <span className="muted fs0">
                {k.last_used_at ? `used ${k.last_used_at.slice(0, 10)}` : "never used"}
              </span>
              <Button
                size="small"
                variant="danger"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(k.id)}
              >
                Revoke
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
