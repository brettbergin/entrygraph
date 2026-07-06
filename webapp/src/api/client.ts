// Fetch wrapper: same-origin cookies, JSON errors, 401 -> auth event.
// The SPA never holds tokens; the backend session cookie is the credential.

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

export const AUTH_EXPIRED_EVENT = "eg:auth-expired";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
    throw new ApiError(401, "not authenticated");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // non-JSON error body: keep the status line
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function qs(params: Record<string, string | number | boolean | undefined>): string {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") out.set(k, String(v));
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}
