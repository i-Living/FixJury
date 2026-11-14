"""The API application (PLAN §2, §9).

Read-only by construction: the router has GET routes only, and every route gets
its session from the maker the caller supplies, so the app holds no global state.
"""

from __future__ import annotations

from fastapi import FastAPI

from fixjury import __version__
from fixjury.api.routes import build_router
from fixjury.db import SessionMaker


def create_app(*, maker: SessionMaker) -> FastAPI:
    """Build the API the CLI serves and the tests exercise."""

    app = FastAPI(title="FixJury API", version=__version__)
    app.include_router(build_router(maker), prefix="/api/v1")
    return app
