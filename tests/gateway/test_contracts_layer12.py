"""
Whole-codebase contracts for Layers 1–2.

These do not exercise a request path; they assert properties of the code itself that no
single functional test can guard, because the failure only appears in the *next* place
someone writes the same mistake.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from shared.observability.logging import RESERVED_LOG_KEYS, JsonFormatter, _install_reserved_key_guard

SOURCE_DIRS = ["backend/app", "services", "shared", "workers", "orchestration"]


def python_files() -> list[Path]:
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        files.extend(p for p in (REPO_ROOT / directory).rglob("*.py") if "__pycache__" not in p.parts)
    return files


def test_no_log_call_uses_a_reserved_logrecord_key() -> None:
    """
    ``extra={"filename": ...}`` makes ``Logger.makeRecord`` raise, which aborts the request
    the log line was reporting on — and only once logging is configured at INFO, so it hides
    from quiet tests and appears in production.

    The runtime guard in `configure_logging` renames such keys rather than crashing, but a
    silently renamed field is still a field nobody will find in the logs. This test catches
    it at the source.
    """
    offenders: list[str] = []
    for path in python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and key.value in RESERVED_LOG_KEYS:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} extra={key.value!r}")
    assert offenders == [], "reserved LogRecord keys in extra=: " + "; ".join(offenders)


def test_the_logging_guard_keeps_the_line_instead_of_raising(caplog: pytest.LogCaptureFixture) -> None:
    """Belt and braces: even with a colliding key, the record is emitted and readable."""
    # The guard is installed directly rather than through `configure_logging`, which replaces
    # the root handlers and would evict caplog's own handler.
    _install_reserved_key_guard()
    logger = logging.getLogger("monta.test.guard")
    with caplog.at_level(logging.INFO):
        logger.info("upload done", extra={"filename": "clip.mp4", "clip_id": "c1"})

    record = next(r for r in caplog.records if r.getMessage() == "upload done")
    assert record.filename_ == "clip.mp4"        # renamed, not dropped
    assert record.clip_id == "c1"
    assert record.filename.endswith(".py")        # the real LogRecord field survived
    payload = json.loads(JsonFormatter().format(record))
    assert payload["filename_"] == "clip.mp4"
    assert payload["message"] == "upload done"


def test_the_backend_never_imports_worker_code() -> None:
    """
    The API submits jobs by task name and must not import the worker.

    Importing it would pull ffmpeg, torch and the whole model stack into the web process,
    turning a 200 MB API container into a multi-gigabyte one and coupling API deploys to
    worker deploys.
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "backend" / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in {"workers", "orchestration", "torch", "cv2"}:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {name}")
    assert offenders == [], "backend imports worker/model code: " + "; ".join(offenders)


def test_the_media_gateway_is_framework_free() -> None:
    """
    Layer 2's library half must stay a plain library.

    It is used by the API *and* the worker, so a FastAPI, SQLAlchemy or Celery import here
    would drag a web framework into the worker and make the validation rules untestable
    without a request.
    """
    forbidden = {"fastapi", "starlette", "sqlalchemy", "celery", "redis", "app"}
    offenders: list[str] = []
    for path in (REPO_ROOT / "services" / "media_gateway").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                if name.split(".")[0] in forbidden:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {name}")
    assert offenders == [], "media_gateway imports a framework: " + "; ".join(offenders)


def test_no_todo_or_stub_markers_in_layer_1_and_2_sources() -> None:
    """
    Layers 1 and 2 are meant to be finished code.

    Scans the Python *and* the frontend TypeScript for the markers that mean "not
    implemented", so an unfinished path cannot be mistaken for a working one.
    """
    markers = ("TODO", "FIXME", "NotImplementedError", "XXX:")
    # Scoped to the Layer 1/2 surface. The render, timeline, export and audio modules belong
    # to Layers 8-11 and are still scaffolds; listing them here would assert something this
    # work never claimed.
    roots = [
        REPO_ROOT / "services" / "media_gateway",
        REPO_ROOT / "frontend" / "web" / "src",
        REPO_ROOT / "backend" / "app" / "services",
        REPO_ROOT / "backend" / "app" / "api" / "websockets",
        REPO_ROOT / "backend" / "app" / "models",
        REPO_ROOT / "backend" / "app" / "schemas",
        REPO_ROOT / "backend" / "app" / "db",
    ]
    layer12_api = {"upload.py", "projects.py", "health.py", "router.py"}
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".css"} or "__pycache__" in path.parts:
                continue
            if path.name in {"render_service.py", "audio_service.py", "timeline_service.py"}:
                continue        # Layers 8-11, not in this scope
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for marker in markers:
                    if marker in line:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} {line.strip()[:70]}")

    api_dir = REPO_ROOT / "backend" / "app" / "api" / "v1"
    for name in sorted(layer12_api):
        path = api_dir / name
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for marker in markers:
                if marker in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} {line.strip()[:70]}")

    assert offenders == [], "unfinished markers: " + "; ".join(offenders)


def test_the_orm_and_the_migration_agree_on_every_status_value() -> None:
    """
    Statuses are CHECK constraints in the migration and enums in the ORM.

    They are written in two files, so they can drift — and the symptom of drift is an
    IntegrityError at runtime for a state the application considers legal.
    """
    import importlib.util

    from app.models import ClipStatus, JobState, ProjectStatus

    spec = importlib.util.spec_from_file_location(
        "migration_0003", REPO_ROOT / "backend" / "alembic" / "versions" / "0003_core_tables.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert set(migration.PROJECT_STATUSES) == {s.value for s in ProjectStatus}
    assert set(migration.CLIP_STATUSES) == {s.value for s in ClipStatus}
    assert set(migration.JOB_STATES) == {s.value for s in JobState}


def test_the_frontend_and_the_backend_agree_on_the_stage_vocabulary() -> None:
    """
    The dashboard's status rail is driven by the backend's `Stage` enum.

    A stage the UI does not know about renders as a missing step, so the two lists are
    checked against each other rather than trusted to stay in step.
    """
    from app.services.progress import Stage

    source = (REPO_ROOT / "frontend" / "web" / "src" / "lib" / "api.ts").read_text()
    line = next(line for line in source.splitlines() if line.startswith("export type Stage"))
    frontend_stages = {part.strip().strip("';") for part in line.split("=", 1)[1].split("|")}
    assert frontend_stages == {s.value for s in Stage}


def test_the_frontend_knows_every_validation_code_the_backend_can_emit() -> None:
    """
    Every rejection must be explainable in the UI.

    A code with no entry in `ISSUE_HINTS` still shows the server's sentence, but loses the
    "what to do about it" line — which is the part that stops a support ticket.
    """
    from services.media_gateway import ValidationCode

    hints = (REPO_ROOT / "frontend" / "web" / "src" / "lib" / "status.ts").read_text()
    missing = [code.value for code in ValidationCode if f"{code.value}:" not in hints]
    assert missing == [], f"validation codes with no UI hint: {missing}"
