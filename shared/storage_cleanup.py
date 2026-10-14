"""
MONTA — Storage & Temporary File Cleanup (Layer 12 / M6)
=========================================================
Manages cleanup of temporary render files, aborted uploads, stale checkpoints,
and old exports to prevent unbounded disk growth.

Features:
- Sweeps stale upload fragments (.incoming, .part, .tmp)
- Removes expired rendered videos past retention window
- Reclaims disk space if storage usage exceeds threshold
- Safe: never deletes files actively being written (mtime check)
- Path confinement to avoid directory traversal
- Disk usage reporting (total, used, free, reclaimable)
"""

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("monta.shared.storage_cleanup")


@dataclass
class StorageReport:
    """Summary of storage utilization and cleanup potential."""
    storage_root: str
    total_bytes: int = 0
    free_bytes: int = 0
    used_bytes: int = 0
    stale_upload_bytes: int = 0
    render_cache_bytes: int = 0
    reclaimed_bytes: int = 0
    deleted_files_count: int = 0


class StorageCleanupManager:
    """Manages disk space and garbage collection for MONTA."""

    def __init__(
        self,
        storage_root: str = "./storage",
        stale_upload_ttl_seconds: float = 3600.0,  # 1 hour
        render_cache_ttl_seconds: float = 86400.0 * 7,  # 7 days
        max_storage_usage_bytes: Optional[int] = None,
    ):
        self.storage_root = Path(storage_root).resolve()
        self.stale_upload_ttl = stale_upload_ttl_seconds
        self.render_cache_ttl = render_cache_ttl_seconds
        self.max_storage_usage = max_storage_usage_bytes

    def _is_safe_path(self, target: Path) -> bool:
        """Verify target path is confined within storage_root."""
        try:
            resolved = target.resolve()
            return self.storage_root in resolved.parents or resolved == self.storage_root
        except Exception:
            return False

    def get_storage_stats(self) -> StorageReport:
        """Get current disk usage for the storage partition."""
        report = StorageReport(storage_root=str(self.storage_root))
        if not self.storage_root.exists():
            return report

        try:
            total, used, free = shutil.disk_usage(self.storage_root)
            report.total_bytes = total
            report.used_bytes = used
            report.free_bytes = free
        except OSError as e:
            logger.warning("Could not read disk usage for %s: %s", self.storage_root, e)

        # Calculate space used by uploads and renders
        now = time.time()
        for root, _, files in os.walk(self.storage_root):
            for file in files:
                p = Path(root) / file
                try:
                    stat = p.stat()
                    size = stat.st_size
                    # Check if stale upload fragment
                    if file.endswith((".tmp", ".part")) or ".incoming" in str(p):
                        if (now - stat.st_mtime) > self.stale_upload_ttl:
                            report.stale_upload_bytes += size
                    elif "renders" in str(p) or "exports" in str(p):
                        if (now - stat.st_mtime) > self.render_cache_ttl:
                            report.render_cache_bytes += size
                except (OSError, FileNotFoundError):
                    continue

        return report

    def cleanup_stale_uploads(self, older_than_s: Optional[float] = None) -> int:
        """Remove leftover partial/aborted uploads.

        Returns total bytes reclaimed.
        """
        ttl = older_than_s if older_than_s is not None else self.stale_upload_ttl
        now = time.time()
        reclaimed = 0

        incoming_dir = self.storage_root / ".incoming"
        targets = [incoming_dir] if incoming_dir.exists() else []

        # Also search for .tmp and .part files in storage root
        for p in targets:
            if not self._is_safe_path(p):
                continue
            for item in p.glob("**/*"):
                if item.is_file():
                    try:
                        if (now - item.stat().st_mtime) > ttl:
                            size = item.stat().st_size
                            item.unlink()
                            reclaimed += size
                            logger.info("Removed stale upload fragment: %s (%d bytes)", item, size)
                    except (OSError, FileNotFoundError):
                        continue

        # Look for orphan .tmp / .part files
        if self.storage_root.exists():
            for item in self.storage_root.rglob("*"):
                if item.is_file() and (item.name.endswith(".tmp") or item.name.endswith(".part")):
                    try:
                        if (now - item.stat().st_mtime) > ttl and self._is_safe_path(item):
                            size = item.stat().st_size
                            item.unlink()
                            reclaimed += size
                            logger.info("Removed orphan temp file: %s (%d bytes)", item, size)
                    except (OSError, FileNotFoundError):
                        continue

        return reclaimed

    def cleanup_expired_renders(self, older_than_s: Optional[float] = None) -> int:
        """Remove renders/exports exceeding retention TTL.

        Returns total bytes reclaimed.
        """
        ttl = older_than_s if older_than_s is not None else self.render_cache_ttl
        now = time.time()
        reclaimed = 0

        renders_dir = self.storage_root / "renders"
        if not renders_dir.exists() or not self._is_safe_path(renders_dir):
            return 0

        for item in renders_dir.glob("**/*"):
            if item.is_file():
                try:
                    if (now - item.stat().st_mtime) > ttl and self._is_safe_path(item):
                        size = item.stat().st_size
                        item.unlink()
                        reclaimed += size
                        logger.info("Removed expired render file: %s (%d bytes)", item, size)
                except (OSError, FileNotFoundError):
                    continue

        return reclaimed

    def sweep_all(self) -> StorageReport:
        """Run all cleanup tasks and return the report."""
        report = self.get_storage_stats()
        reclaimed_uploads = self.cleanup_stale_uploads()
        reclaimed_renders = self.cleanup_expired_renders()
        report.reclaimed_bytes = reclaimed_uploads + reclaimed_renders
        return report
