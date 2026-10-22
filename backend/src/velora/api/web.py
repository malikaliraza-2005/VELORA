"""Serving the staff dashboard (P9-T1, AD-33) and the security headers on every response.

The dashboard is plain files from the repository's `web/` folder (no build step), served from
the API's own origin, so the session cookie (SameSite=Strict, HttpOnly) and the CSRF check
need no cross-origin exceptions.

- `/` and `/login` return the app shell. The login page reads the magic-link token from the
  URL fragment, which never reaches the server, and posts it to `/api/auth/login`.
- `/assets/*`, `/sw.js` and `/manifest.webmanifest` are the app's files.

**Headers on every response:** a Content Security Policy that allows only this origin's
scripts, styles, images and connections, with no inline script and Trusted Types required
(so script can't write HTML strings into the page); no framing; no referrer; and
`Cache-Control: no-store` on the API, so no order or customer data is cached by the browser.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "require-trusted-types-for 'script'",
        # One policy, which only allows the service worker's URL (web/assets/js/main.js).
        "trusted-types velora",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

SHELL_ROUTES = ("/", "/login")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            # The files have no content hashes in their names: always revalidate.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


def default_web_dir() -> Path | None:
    """The repository's `web/` folder, when running from a checkout."""
    candidate = Path(__file__).resolve().parents[4] / "web"
    return candidate if (candidate / "index.html").is_file() else None


def mount_dashboard(app: FastAPI, web_dir: Path) -> None:
    index = web_dir / "index.html"

    async def shell() -> FileResponse:
        return FileResponse(index, media_type="text/html")

    for path in SHELL_ROUTES:
        app.add_api_route(path, shell, methods=["GET"], include_in_schema=False)

    async def service_worker() -> FileResponse:
        return FileResponse(web_dir / "sw.js", media_type="text/javascript")

    async def manifest() -> FileResponse:
        return FileResponse(
            web_dir / "manifest.webmanifest", media_type="application/manifest+json"
        )

    app.add_api_route("/sw.js", service_worker, methods=["GET"], include_in_schema=False)
    app.add_api_route("/manifest.webmanifest", manifest, methods=["GET"], include_in_schema=False)
    app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")
