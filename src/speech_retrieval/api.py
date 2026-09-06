from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .analysis import InvalidAnalysisError, UnsupportedAnalysisError
from .catalogue import CatalogueError, canonical_language, load_catalogue_directory
from .channels import (
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelRepository,
    ChannelRepositoryError,
)
from .contracts import (
    ChannelCreate,
    ChannelRecord,
    ChannelUpdate,
    Clip,
    CorpusStatistics,
    CorpusStatus,
    ErrorResponse,
    SearchResponse,
    SuggestionsResponse,
)
from .search import Corpus, IncompatibleIndexError, SearchError
from .settings import Settings

logger = logging.getLogger("uvicorn.access")

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (400, 401, 404, 409, 413, 422, 503)
}


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def _error(request: Request, error: ServiceError) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"code": error.code, "message": error.message},
        "request_id": _request_id(request),
    }
    if error.details is not None:
        body["error"]["details"] = error.details
    headers = {"X-Request-ID": body["request_id"]}
    if error.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(body, status_code=error.status, headers=headers)


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_app(settings: Settings) -> FastAPI:
    if (
        settings.enable_channel_mutations
        and not settings.operator_token
        and not _is_loopback(settings.host)
    ):
        raise ValueError("non-loopback channel management requires an operator token")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.corpus = Corpus(settings)
        app.state.channels = ChannelRepository(settings.catalogue_dir)
        try:
            yield
        finally:
            app.state.corpus.close()

    app = FastAPI(
        title="Spoken Usage Retrieval API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        responses=ERROR_RESPONSES,
    )
    app.state.settings = settings
    bearer = HTTPBearer(auto_error=False)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if supplied.isascii() and 1 <= len(supplied) <= 128 else str(uuid.uuid4())
        )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.max_json_body_bytes:
                    return _error(
                        request,
                        ServiceError(413, "payload_too_large", "Request body is too large"),
                    )
            except ValueError:
                return _error(
                    request,
                    ServiceError(400, "invalid_content_length", "Invalid Content-Length header"),
                )
        if request.method in {"POST", "PATCH"} and "application/json" in request.headers.get(
            "content-type", ""
        ):
            body = await request.body()
            if len(body) > settings.max_json_body_bytes:
                return _error(
                    request,
                    ServiceError(413, "payload_too_large", "Request body is too large"),
                )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            json.dumps(
                {
                    "event": "request_complete",
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        )
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError) -> JSONResponse:
        return _error(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error(
            request,
            ServiceError(
                422,
                "validation_error",
                "Request validation failed",
                jsonable_encoder(error.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        message = str(error.detail) if error.detail else "HTTP request failed"
        return _error(request, ServiceError(error.status_code, "http_error", message))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _error_value: Exception) -> JSONResponse:
        logger.exception("Unhandled service error", extra={"request_id": _request_id(request)})
        return _error(request, ServiceError(500, "internal_error", "Unexpected service error"))

    def corpus(request: Request) -> Corpus:
        return request.app.state.corpus

    def channels(request: Request) -> ChannelRepository:
        return request.app.state.channels

    def management(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if not settings.enable_channel_mutations:
            raise ServiceError(404, "management_disabled", "Channel management is disabled")
        if settings.operator_token:
            valid = credentials is not None and hmac.compare_digest(
                credentials.credentials, settings.operator_token
            )
            if not valid:
                raise ServiceError(401, "invalid_operator_token", "Valid operator token required")

    def translate_domain_error(error: Exception) -> ServiceError:
        if isinstance(error, UnsupportedAnalysisError):
            return ServiceError(400, "unsupported_analysis", str(error))
        if isinstance(error, (SearchError, CatalogueError, ChannelRepositoryError, ValueError)):
            return ServiceError(400, "invalid_request", str(error))
        if isinstance(error, (FileNotFoundError, IncompatibleIndexError, InvalidAnalysisError)):
            return ServiceError(503, "corpus_unavailable", str(error))
        return ServiceError(500, "internal_error", "Unexpected service error")

    @app.get(
        "/api/v1/search",
        response_model=SearchResponse,
    )
    def search(
        language: str = Query(examples=["es"]),
        q: str = Query(min_length=1, max_length=settings.max_query_length, examples=["la verdad"]),
        match_mode: str = "auto",
        order: str = "ranked",
        limit: int = Query(20, ge=1, le=settings.max_search_limit),
        seed: int | None = None,
        value: Corpus = Depends(corpus),
    ) -> SearchResponse:
        try:
            return value.search(
                q,
                source_language=language,
                match_mode=match_mode,
                order=order,
                limit=limit,
                seed=seed,
            )
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.get("/api/v1/suggestions", response_model=SuggestionsResponse)
    def suggestions(
        language: str = Query(examples=["es"]),
        limit: int = Query(12, ge=1, le=settings.max_suggestion_limit),
        value: Corpus = Depends(corpus),
    ) -> SuggestionsResponse:
        try:
            source_language = canonical_language(language)
            return SuggestionsResponse(
                source_language=source_language,
                suggestions=value.suggestions(source_language=source_language, limit=limit),
            )
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.get("/api/v1/clips/{segment_id}", response_model=Clip)
    def clip(segment_id: str, value: Corpus = Depends(corpus)) -> Clip:
        try:
            return value.clip(segment_id)
        except KeyError as error:
            raise ServiceError(404, "segment_not_found", "Segment was not found") from error
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.get("/api/v1/channels", response_model=list[ChannelRecord])
    def list_channels(
        language: str | None = None,
        repository: ChannelRepository = Depends(channels),
    ) -> list[ChannelRecord]:
        try:
            return repository.list(language)
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.post("/api/v1/channels", response_model=ChannelRecord, status_code=201)
    def add_channel(
        body: ChannelCreate,
        _allowed: None = Depends(management),
        repository: ChannelRepository = Depends(channels),
    ) -> ChannelRecord:
        try:
            return repository.add(body)
        except ChannelConflictError as error:
            raise ServiceError(409, "channel_conflict", str(error)) from error
        except ChannelNotFoundError as error:
            raise ServiceError(404, "catalogue_not_found", str(error)) from error
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.patch("/api/v1/channels/{language}/{channel_id}", response_model=ChannelRecord)
    def update_channel(
        language: str,
        channel_id: str,
        body: ChannelUpdate,
        _allowed: None = Depends(management),
        repository: ChannelRepository = Depends(channels),
    ) -> ChannelRecord:
        try:
            return repository.update(language, channel_id, body)
        except ChannelNotFoundError as error:
            raise ServiceError(404, "channel_not_found", str(error)) from error
        except Exception as error:
            raise translate_domain_error(error) from error

    def set_channel_enabled(
        language: str,
        channel_id: str,
        enabled: bool,
        repository: ChannelRepository,
    ) -> ChannelRecord:
        try:
            return repository.set_enabled(language, channel_id, enabled)
        except ChannelNotFoundError as error:
            raise ServiceError(404, "channel_not_found", str(error)) from error
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.post("/api/v1/channels/{language}/{channel_id}/enable", response_model=ChannelRecord)
    def enable_channel(
        language: str,
        channel_id: str,
        _allowed: None = Depends(management),
        repository: ChannelRepository = Depends(channels),
    ) -> ChannelRecord:
        return set_channel_enabled(language, channel_id, True, repository)

    @app.post("/api/v1/channels/{language}/{channel_id}/disable", response_model=ChannelRecord)
    def disable_channel(
        language: str,
        channel_id: str,
        _allowed: None = Depends(management),
        repository: ChannelRepository = Depends(channels),
    ) -> ChannelRecord:
        return set_channel_enabled(language, channel_id, False, repository)

    @app.get("/api/v1/statistics", response_model=CorpusStatistics)
    def statistics(value: Corpus = Depends(corpus)) -> CorpusStatistics:
        try:
            return value.statistics()
        except Exception as error:
            raise translate_domain_error(error) from error

    @app.get("/api/v1/status", response_model=CorpusStatus)
    def status(value: Corpus = Depends(corpus)) -> CorpusStatus:
        try:
            value.check_ready()
            return value.status()
        except Exception as error:
            try:
                catalogues = load_catalogue_directory(settings.catalogue_dir)
            except Exception:
                catalogues = ()
            return CorpusStatus(
                ready=False,
                error=str(error),
                package_version=__version__,
                database_schema_version=None,
                built_at=None,
                max_ngram=None,
                analyzer_selection=None,
                analyzer_id=None,
                configured_languages=sorted(item.language for item in catalogues),
                enabled_languages=sorted(
                    item.language for item in catalogues if item.enabled_channels
                ),
                indexed_languages=[],
                languages=[],
                videos=0,
                segments=0,
                occurrences=0,
                caption_kinds={},
                channel_mutations_enabled=settings.enable_channel_mutations,
            )

    @app.get("/api/v1/health/live")
    def live() -> dict[str, Any]:
        return {"status": "live", "package_version": __version__}

    @app.get("/api/v1/health/ready")
    def ready(value: Corpus = Depends(corpus)) -> dict[str, Any]:
        try:
            value.check_ready()
            result = value.status()
            return {"status": "ready", "built_at": result.built_at}
        except Exception as error:
            raise ServiceError(503, "not_ready", str(error)) from error

    if settings.web_dist is not None:
        dist = settings.web_dist.resolve()
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
                raise ServiceError(404, "frontend_not_found", "Frontend build not found")
            return FileResponse(index)

    return app
