"""Builds the FastAPI application of the web interface (ADR 0013)."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from interpol_web.auth import require_login
from interpol_web.config import WebSettings
from interpol_web.queries import MATCH_SIMILARITY_THRESHOLD
from interpol_web.routes import NoticeReader, PhotoSource, create_router

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
# Shown on the pages that explain what a possible match is.
templates.env.globals["match_threshold"] = MATCH_SIMILARITY_THRESHOLD


def create_app(settings: WebSettings, reader: NoticeReader, photos: PhotoSource) -> FastAPI:
    """Build the application; every page asks for the configured user name and password.

    ``reader`` answers the notice searches and detail pages and ``photos`` hands out the
    stored photos; main() hands in the database and the store, the tests made-up ones.
    """
    app = FastAPI(
        # No automatic API documentation: this is a set of pages, not an API, and the
        # documentation would only show the inside of the application to visitors.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # CSS and scripts. Mounted files skip the login check; they hold no personal data.
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.middleware("http")
    async def no_store(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Tell the browser never to keep a copy of a page: pages hold personal data."""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        """For Docker's health check: no login, no data, no database call."""
        return "ok"

    # The login check runs before every page and photo of the router.
    app.include_router(
        create_router(reader, photos, templates),
        dependencies=[Depends(require_login(settings))],
    )
    return app
