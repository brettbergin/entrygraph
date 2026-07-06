import { Button, Heading, Text } from "@primer/react";
import { useSearchParams } from "react-router";

export function LoginPage() {
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/";
  const login = () => {
    window.location.href = `/auth/login?next=${encodeURIComponent(next)}`;
  };
  return (
    <div className="login">
      <div className="card">
        <Heading as="h1" style={{ fontSize: 24, marginBottom: 4 }}>
          entrygraph
        </Heading>
        <Text as="p" className="muted" style={{ marginBottom: 16 }}>
          Query your codebase like a graph.
        </Text>
        <Button variant="primary" size="large" block onClick={login}>
          Sign in with SSO
        </Button>
      </div>
    </div>
  );
}
