"""Builds the FastAPI application of the web interface (ADR 0013)."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from interpol_web.auth import require_login
from interpol_web.config import WebSettings
from interpol_web.routes import NoticeSearch, create_router

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


def create_app(settings: WebSettings, search: NoticeSearch) -> FastAPI:
    """Build the application; every page asks for the configured user name and password.

    ``search`` answers the notice searches; main() hands in the database queries, the
    tests a made-up one.
    """
    app = FastAPI(
        # No automatic API documentation: this is a set of pages, not an API, and the
        # documentation would only show the inside of the application to visitors.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        # The login check runs before every page.
        dependencies=[Depends(require_login(settings))],
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

    app.include_router(create_router(search, templates))
    return app
