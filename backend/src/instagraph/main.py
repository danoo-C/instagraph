"""FastAPI application factory.

Run with ``uvicorn instagraph.main:app --host 127.0.0.1 --port 8000``.

* Routers are auto-discovered: every module in :mod:`instagraph.api.routes` with a
  module-level ``router: APIRouter`` is included (sorted by module name).
* Responses are serialized by FastAPI/Pydantic directly to JSON bytes (declare a
  ``response_model``/return type). ``ORJSONResponse`` is deprecated in this FastAPI
  version and slower than the native path, so it is not used as the default class.
* Host allow-list (DNS-rebinding protection) and an Origin / Sec-Fetch-Site check
  on unsafe methods (CSRF protection) run before everything else (§10).
* A strict Content-Security-Policy is added to every non-docs response (§10).
* ``frontend/dist`` (if built) is served at ``/`` with SPA fallback to ``index.html``.
"""

import importlib
import logging
import pkgutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import instagraph
import instagraph.api.routes
from instagraph.config import Settings, ensure_data_dir, get_settings, warn_if_public_host
from instagraph.errors import NotFoundError, error_response, install_error_handlers
from instagraph.store.app_settings import AppSettingsStore, set_settings_store
from instagraph.store.dataset import DatasetStore, set_store

logger = logging.getLogger("instagraph")

CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' blob: data:; "
    "media-src 'self' blob:; style-src 'self' 'unsafe-inline'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'"
)
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def split_host(value: str) -> str:
    """Hostname of a ``Host`` header / netloc, lower-cased, without port and brackets."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value[1:]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value  # bare IPv6 or no port


def _origin_allowed(origin: str, host_header: str, settings: Settings) -> bool:
    if origin in settings.cors_origins:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False  # includes the opaque "null" origin
    # Same-origin: the Origin's host[:port] equals the (already validated) Host header.
    return parts.netloc.lower() == host_header.strip().lower()


def discover_routers() -> list[tuple[str, APIRouter]]:
    """Import every module in ``instagraph.api.routes`` and return its ``router``."""
    found: list[tuple[str, APIRouter]] = []
    package = instagraph.api.routes
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            found.append((info.name, router))
    return found


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        return
    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise NotFoundError(f"Unknown API path: /{full_path}")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Installs process-wide dataset/settings stores for ``data_dir``."""
    settings = settings or get_settings()
    ensure_data_dir(settings.data_dir)
    warn_if_public_host(settings)

    store = DatasetStore(settings.data_dir)
    set_store(store)
    set_settings_store(AppSettingsStore(settings.data_dir / "settings.json"))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            ds = store.load_from_disk()
            if ds is not None:
                logger.info("Loaded dataset v%d from %s", ds.version, store.dataset_dir)
        except Exception:
            logger.exception("Failed to load the existing dataset; starting empty")
        yield

    app = FastAPI(
        title="Instagraph",
        version=instagraph.__version__,
        description="Local statistics for Instagram direct-message exports.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    install_error_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    trusted = settings.trusted_hosts

    @app.middleware("http")
    async def request_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Added after CORS → wraps it: runs before CORS and routing.
        host_header = request.headers.get("host", "")
        if split_host(host_header) not in trusted:
            return error_response("invalid_host", "Invalid Host header.", 400)
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            cross_site = request.headers.get("sec-fetch-site", "").lower() == "cross-site"
            if cross_site or (
                origin is not None and not _origin_allowed(origin, host_header, settings)
            ):
                return error_response(
                    "forbidden_origin", "Cross-origin requests are not allowed.", 403
                )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    for name, router in discover_routers():
        logger.debug("Including router from instagraph.api.routes.%s", name)
        app.include_router(router)

    _mount_frontend(app, settings.frontend_dist)
    return app


app = create_app()
