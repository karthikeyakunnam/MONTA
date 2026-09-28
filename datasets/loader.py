"""
MONTA — Dataset Loaders
=========================
``load_golden()`` reads ``datasets/golden_projects/*.json`` (validated against
``GoldenProject``) and returns them sorted by id. ``manifest()`` returns a
content-hash manifest so reports can prove which dataset version they used.
"""

import hashlib
import json
from pathlib import Path

from datasets.schema import GoldenProject

GOLDEN_DIR = Path(__file__).parent / "golden_projects"


def load_golden(directory: Path | str = GOLDEN_DIR, *, categories: set[str] | None = None) -> list[GoldenProject]:
    projects = []
    for path in sorted(Path(directory).glob("*.json")):
        project = GoldenProject.model_validate(json.loads(path.read_text()))
        if path.stem != project.project_id:
            raise ValueError(f"{path.name}: file name must equal project_id '{project.project_id}'")
        if categories is None or project.category in categories:
            projects.append(project)
    return projects


def manifest(directory: Path | str = GOLDEN_DIR) -> dict:
    h = hashlib.sha256()
    files = sorted(Path(directory).glob("*.json"))
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return {"dataset": "golden_projects", "files": len(files), "sha256": h.hexdigest()}
