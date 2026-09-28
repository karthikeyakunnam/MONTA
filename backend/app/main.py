"""
MONTA Backend — FastAPI Entrypoint
===================================
Startup verifies the dependencies the upload path needs and logs what is missing
instead of failing silently at the first upload. Every request runs inside a
correlation context (trace id, request id, hashed user) and returns
``X-Request-ID``, so a UI error report can be traced through API, queue and worker.
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.api.websockets import progress as progress_ws
from app.config import settings
from app.db.session import dispose_engine, get_engine
from app.dependencies import get_gateway, get_progress_bus, get_queue, get_storage
from shared.exceptions import MontaError
from shared.observability.context import correlation, new_trace
from shared.observability.logging import configure_logging
from shared.observability.tracing import span

logger = logging.getLogger("monta.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("DEBUG" if settings.DEBUG else "INFO")
    storage = get_storage()
    gateway = get_gateway()
    queue = get_queue()
    checks = {
        "database_url": settings.DATABASE_URL.split("@")[-1],
        "storage_root": str(storage.root),
        "ffprobe": gateway.extractor.available(),
        "queue_enabled": queue.enabled,
    }
    logger.info("monta backend starting", extra={"env": settings.APP_ENV, **checks})
    if not checks["ffprobe"]:
        logger.error("ffprobe not found: uploads will be rejected until it is installed",
                     extra={"ffprobe_path": settings.FFPROBE_PATH})
    removed = storage.sweep_incoming()
    if removed:
        logger.info("cleared abandoned upload temp files", extra={"files": removed})
    try:
        yield
    finally:
        await get_progress_bus().close()
        await dispose_engine()
        logger.info("monta backend stopped")


app = FastAPI(
    title="MONTA API",
    version="0.2.0",
    description="Upload, validate and edit video with MONTA (Layers 1–7 wired end to end).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def correlate(request: Request, call_next):
    """Bind one trace per request; echo the request id so clients can quote it in bug reports."""
    incoming = request.headers.get("x-request-id")
    user = request.headers.get("x-monta-user") or settings.DEFAULT_USER_ID
    with new_trace(request_id=incoming, user_id=user), span("http.request", method=request.method,
                                                            path=request.url.path) as sp:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled request error", extra={"path": request.url.path})
            raise
        sp.set(status_code=response.status_code, duration_ms=round((time.perf_counter() - started) * 1000, 2))
        ids = correlation()
        response.headers["X-Request-ID"] = ids.get("request_id", ids.get("trace_id", ""))
        logger.info("request", extra={"method": request.method, "path": request.url.path,
                                      "status": response.status_code,
                                      "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
        return response


@app.exception_handler(MontaError)
async def monta_error_handler(request: Request, exc: MontaError) -> JSONResponse:
    """Domain errors that reached the transport become 400s with the message the layer wrote."""
    logger.warning("domain error", extra={"path": request.url.path, "error": f"{type(exc).__name__}: {exc}"})
    return JSONResponse(status_code=400, content={"detail": str(exc), "error": type(exc).__name__,
                                                  "request_id": correlation().get("request_id", "")})


app.include_router(api_router, prefix="/api/v1")
app.include_router(progress_ws.router, prefix="/api/v1")


@app.get("/")
async def root() -> dict:
    return {"name": settings.APP_NAME, "version": app.version, "docs": "/docs", "health": "/api/v1/health"}


async def database_ready() -> bool:
    """Used by deployment probes that must not create a request scope."""
    from sqlalchemy import text

    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
