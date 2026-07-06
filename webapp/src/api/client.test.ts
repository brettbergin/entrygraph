import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, AUTH_EXPIRED_EVENT, qs, request } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("request", () => {
  it("returns parsed JSON on success and sends credentials", async () => {
    const spy = mockFetch(200, { ok: true });
    await expect(request("/api/v1/healthz")).resolves.toEqual({ ok: true });
    expect(spy).toHaveBeenCalledWith(
      "/api/v1/healthz",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("dispatches the auth event and throws on 401", async () => {
    mockFetch(401, { detail: "not authenticated" });
    const handler = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, handler);
    await expect(request("/api/v1/me")).rejects.toThrow(ApiError);
    expect(handler).toHaveBeenCalledOnce();
    window.removeEventListener(AUTH_EXPIRED_EVENT, handler);
  });

  it("surfaces the server's error detail", async () => {
    mockFetch(404, { detail: "repo not found" });
    await expect(request("/api/v1/repos/9")).rejects.toThrow("repo not found");
  });
});

describe("qs", () => {
  it("serializes defined params and skips empty ones", () => {
    expect(qs({ a: "x", b: 2, c: true, d: undefined, e: "" })).toBe("?a=x&b=2&c=true");
    expect(qs({})).toBe("");
  });
});
