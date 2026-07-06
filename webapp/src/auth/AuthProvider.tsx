// Session bootstrap: fetch /api/v1/me before rendering routes. Dev mode
// (auth_disabled) renders with a synthetic local user and never shows login.
// A 401 — at bootstrap or mid-session via the client's auth event — routes to
// /login preserving the deep link.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Spinner } from "@primer/react";
import { useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { AUTH_EXPIRED_EVENT, ApiError } from "../api/client";
import { api } from "../api/queries";
import type { Me } from "../api/types";

interface AuthState {
  me: Me | null;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({ me: null, logout: async () => {} });

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [pending, setPending] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          const next = location.pathname + location.search;
          navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
        }
      })
      .finally(() => {
        if (!cancelled) setPending(false);
      });
    return () => {
      cancelled = true;
    };
    // bootstrap once; mid-session expiry is handled by the auth event below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onExpired = () => {
      setMe(null);
      queryClient.clear();
      const next = window.location.pathname + window.location.search;
      navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, [navigate, queryClient]);

  const logout = async () => {
    try {
      await api.logout();
    } finally {
      setMe(null);
      queryClient.clear();
      window.location.href = "/login";
    }
  };

  if (pending) {
    return (
      <div className="login">
        <Spinner size="large" />
      </div>
    );
  }
  return <AuthContext.Provider value={{ me, logout }}>{children}</AuthContext.Provider>;
}
