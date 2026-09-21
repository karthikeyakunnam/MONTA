"""MONTA — Utility Functions."""

import uuid
import os


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    uid = uuid.uuid4().hex[:12]
    return f"{prefix}_{uid}" if prefix else uid


def ensure_dir(path: str):
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def get_file_extension(filename: str) -> str:
    """Get file extension in lowercase."""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
