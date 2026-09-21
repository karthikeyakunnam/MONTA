"""
MONTA Backend — FastAPI Entrypoint
===================================
Main application factory and route registration.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup
    print(f"🎬 MONTA Backend starting — {settings.APP_ENV}")
    # TODO: Initialize DB connection pool
    # TODO: Initialize Redis connection
    # TODO: Initialize Qdrant client
    yield
    # Shutdown
    print("🎬 MONTA Backend shutting down")
    # TODO: Close DB connections
    # TODO: Close Redis connections


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="MONTA API",
        description="AI-Powered Video Editing Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routes
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "monta-backend"}

    return app


app = create_app()
