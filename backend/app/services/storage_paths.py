"""Path checks for downloadable files stored by MONTA's local render pipeline."""

from pathlib import Path


def resolve_stored_mp4(path: str | Path | None, storage_root: str | Path) -> Path | None:
    """Return an existing MP4 beneath the configured storage root, or ``None``."""
    if not path:
        return None
    root = Path(storage_root).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() != ".mp4":
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size < 1024:
            return None
    except OSError:
        return None
    return candidate
