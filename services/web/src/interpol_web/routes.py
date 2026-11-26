"""The pages of the web interface (ADR 0013).

A page reads its filters from the URL, asks the reader for the data and fills a
template. The pages know the database only through the ``NoticeReader`` protocol, so
the tests can hand in a made-up reader without a database.
"""

import math
from typing import Annotated, Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from interpol_web.filters import MAX_PAGE, PAGE_SIZE, NoticeFilters
from interpol_web.queries import NoticeDetail, SearchResult


class NoticeReader(Protocol):
    """Anything that can search and read notices; ``NoticeQueries`` in production."""

    def search(self, filters: NoticeFilters) -> SearchResult: ...

    def notice(self, notice_id: int) -> NoticeDetail | None: ...


def create_router(reader: NoticeReader, templates: Jinja2Templates) -> APIRouter:
    """Build the pages around the given reader and templates."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request, filters: Annotated[NoticeFilters, Query()]) -> HTMLResponse:
        """The notice list with the filter sidebar."""
        result = reader.search(filters)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "filters": filters,
                "result": result,
                "page_count": page_count(result.total),
                # The template builds the links of the page numbers with this.
                "page_url": lambda page: page_url(filters, page),
            },
        )

    @router.get("/notices/{notice_id}", response_class=HTMLResponse)
    def notice_page(
        request: Request, notice_id: Annotated[int, Path(gt=0, lt=2**63)]
    ) -> HTMLResponse:
        """One notice: its details, warrants, photos with face boxes and possible matches."""
        notice = reader.notice(notice_id)
        if notice is None:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(request, "notice.html", {"notice": notice})

    return router


def page_count(total: int) -> int:
    """How many pages the notices fill; at least one, at most MAX_PAGE."""
    return min(max(math.ceil(total / PAGE_SIZE), 1), MAX_PAGE)


def page_url(filters: NoticeFilters, page: int) -> str:
    """The URL of another page with the same filters, e.g. "/?sex=F&page=3".

    Filters left at their default value are not written, so the URLs stay short.
    """
    values = filters.model_dump(exclude_defaults=True, exclude={"page"})
    if page != 1:
        values["page"] = page
    query = urlencode({name: _url_value(value) for name, value in values.items()}, doseq=True)
    return f"/?{query}" if query else "/"


def _url_value(value: object) -> object:
    """True becomes "true", the form of a ticked box the filters accept back."""
    if value is True:
        return "true"
    return value
