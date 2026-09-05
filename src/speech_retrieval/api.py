from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .search import Corpus, SearchError


def create_app(
    *, data_dir: str | Path = "data", web_dist: str | Path | None = "web/dist"
) -> FastAPI:
    corpus = Corpus(data_dir)
    app = FastAPI(title="Native Speech Retrieval", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/search")
    def search(q: str = "", limit: int = Query(20, ge=1, le=50)) -> dict:
        try:
            return corpus.search(q, limit)
        except SearchError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/suggestions")
    def suggestions(limit: int = Query(12, ge=1, le=30)) -> dict:
        try:
            return {"suggestions": corpus.suggestions(limit)}
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/status")
    def status() -> dict:
        try:
            return corpus.status()
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    if web_dist is not None:
        dist = Path(web_dist).resolve()
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend(full_path: str) -> FileResponse:
            target = (dist / full_path).resolve()
            if full_path and target.is_relative_to(dist) and target.is_file():
                return FileResponse(target)
            index = dist / "index.html"
            if not index.exists():
                raise HTTPException(status_code=404, detail="Frontend build not found")
            return FileResponse(index)

    return app
