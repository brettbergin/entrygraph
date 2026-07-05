// Thin typed client over the Sentinel REST API. The bearer token and (optional)
// API base URL live in localStorage; every request carries the token. A 401
// throws `AuthError` so the app can bounce back to the token screen.

import type { Finding, Installation, Policy, Repo, Scan, Suppression } from "./types";

const TOKEN_KEY = "sentinel.token";
const BASE_KEY = "sentinel.apiBase";

export class AuthError extends Error {}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}
export function getApiBase(): string {
  // default: same origin (the API is mounted at /api by the service app)
  return localStorage.getItem(BASE_KEY) ?? "";
}
export function setApiBase(base: string): void {
  localStorage.setItem(BASE_KEY, base.replace(/\/$/, ""));
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${getApiBase()}/api${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 401 || res.status === 503) {
    throw new AuthError(res.status === 503 ? "API disabled (no token configured)" : "Unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const repoBase = (installationId: number, fullName: string) =>
  `/installations/${installationId}/repos/${fullName}`;

export const api = {
  installations: () =>
    request<{ installations: Installation[] }>("/installations").then((r) => r.installations),

  repos: (installationId: number) =>
    request<{ repos: Repo[] }>(`/installations/${installationId}/repos`).then((r) => r.repos),

  scans: (installationId: number, fullName: string) =>
    request<{ scans: Scan[] }>(`${repoBase(installationId, fullName)}/scans`).then((r) => r.scans),

  scanFindings: (installationId: number, fullName: string, scanId: number, status?: string) =>
    request<{ findings: Finding[] }>(
      `${repoBase(installationId, fullName)}/scans/${scanId}/findings` +
        (status ? `?status=${status}` : ""),
    ).then((r) => r.findings),

  suppressions: (installationId: number, fullName: string) =>
    request<{ suppressions: Suppression[] }>(
      `${repoBase(installationId, fullName)}/suppressions`,
    ).then((r) => r.suppressions),

  addSuppression: (
    installationId: number,
    fullName: string,
    body: { fingerprint: string; reason?: string; created_by?: string },
  ) =>
    request(`${repoBase(installationId, fullName)}/suppressions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeSuppression: (installationId: number, fullName: string, fingerprint: string) =>
    request(`${repoBase(installationId, fullName)}/suppressions/${fingerprint}`, {
      method: "DELETE",
    }),

  policy: (installationId: number, fullName: string) =>
    request<Policy>(`${repoBase(installationId, fullName)}/policy`),

  setPolicy: (installationId: number, fullName: string, body: Partial<Policy>) =>
    request(`${repoBase(installationId, fullName)}/policy`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
