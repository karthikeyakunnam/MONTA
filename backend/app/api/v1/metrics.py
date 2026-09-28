"""
MONTA API — Metrics
=====================
Prometheus exposition of the Layer 3–7 metric catalog (``shared/observability/catalog.py``).
Mount behind the internal network only; it is unauthenticated by design for scrapers.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

import shared.observability.catalog  # noqa: F401  (registers the catalog)
from shared.observability.metrics import REGISTRY

router = APIRouter()


@router.get("", response_class=PlainTextResponse)
async def metrics() -> str:
    return REGISTRY.render_prometheus()
