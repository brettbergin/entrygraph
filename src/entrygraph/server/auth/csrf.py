"""CSRF protection for cookie-authenticated mutations: an Origin/Referer check.

Combined with ``SameSite=Lax`` cookies this blocks cross-site form posts and
fetches without token-synchronization machinery. Requests authenticated by an
``Authorization`` header are exempt — a cross-site page cannot attach that
header without a CORS preflight we never grant. Requests with neither Origin
nor Referer (curl, CLI clients) pass: CSRF requires a browser, and browsers
send Origin on cross-origin (and same-origin POST) requests.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class OriginCheckMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_origins: frozenset[str]) -> None:
        super().__init__(app)
        self._allowed = allowed_origins

    async def dispatch(self, request: Request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            return await call_next(request)  # header auth: not CSRF-able
        if request.url.path == "/webhook":
            return await call_next(request)  # sentinel webhook: HMAC-verified

        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer")
            origin = _origin_of(referer) if referer else None
        if origin is None:
            return await call_next(request)  # non-browser client

        allowed = self._allowed | {f"{request.url.scheme}://{request.url.netloc}".lower()}
        if origin.lower() not in allowed:
            return JSONResponse(
                {"detail": f"cross-origin request blocked (origin {origin})"}, status_code=403
            )
        return await call_next(request)
