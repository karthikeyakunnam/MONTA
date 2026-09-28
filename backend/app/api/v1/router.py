"""MONTA API v1 — Route Registration."""

from fastapi import APIRouter

from app.api.v1 import audio, export, health, metrics, projects, prompts, render, timeline, upload

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["observability"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
api_router.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
api_router.include_router(audio.router, prefix="/audio", tags=["audio"])
api_router.include_router(render.router, prefix="/render", tags=["render"])
api_router.include_router(export.router, prefix="/export", tags=["export"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["observability"])
