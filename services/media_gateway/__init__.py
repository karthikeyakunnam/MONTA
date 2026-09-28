"""
MONTA — Media Gateway (Layer 2)
=================================
Validate, probe, hash and store creator uploads. Pure library: no FastAPI, no
database, no queue — the backend wires those around it.

    from services.media_gateway import MediaGateway, LocalMediaStorage, MetadataExtractor

Rules live in ``config.py`` (``GatewayLimits``), rejections in
``validation.py`` (``ValidationCode``/``FileRejected``), probing in
``metadata.py`` (ffprobe only), bytes in ``storage.py``, and the ordered
pipeline in ``upload.py``.
"""

from services.media_gateway.config import DEFAULT_LIMITS, GatewayLimits, ResolutionClass
from services.media_gateway.metadata import AudioStreamInfo, MediaMetadata, MediaProbeError, MetadataExtractor
from services.media_gateway.storage import LocalMediaStorage, StoredFile
from services.media_gateway.upload import DuplicateMatch, IngestResult, MediaGateway
from services.media_gateway.validation import (
    ClipQuota,
    FileRejected,
    ValidationCode,
    ValidationIssue,
    sanitize_filename,
    validate_identifier,
)

__all__ = [
    "AudioStreamInfo", "ClipQuota", "DEFAULT_LIMITS", "DuplicateMatch", "FileRejected", "GatewayLimits",
    "IngestResult", "LocalMediaStorage", "MediaGateway", "MediaMetadata", "MediaProbeError", "MetadataExtractor",
    "ResolutionClass", "StoredFile", "ValidationCode", "ValidationIssue", "sanitize_filename", "validate_identifier",
]
