"""
MONTA — Task Persistence & Recovery
======================================
Provides checkpoint-based persistence for long-running pipeline and render
tasks. If a worker process crashes mid-render, a new worker can detect the
incomplete checkpoint and either resume from the last safe state or restart
from the last validated stage.

Checkpoints are JSON files stored alongside the render output, so they
survive worker restarts without requiring external state stores.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from shared.exceptions import MontaError

logger = logging.getLogger("monta.task_persistence")


class TaskStage(StrEnum):
    """Ordered pipeline stages for checkpoint tracking."""
    PENDING = "pending"
    PROBING = "probing"
    ANALYZING = "analyzing"
    COMPOSING = "composing"
    STORY_DESIGN = "story_design"
    TIMELINE_GENERATION = "timeline_generation"
    RENDERING = "rendering"
    MULTI_RES = "multi_res"
    EXPORTING = "exporting"
    COMPLETE = "complete"
    FAILED = "failed"


STAGE_ORDER = list(TaskStage)


@dataclass
class TaskCheckpoint:
    """Serializable checkpoint for a pipeline or render task."""
    task_id: str
    project_id: str
    stage: str = TaskStage.PENDING.value
    started_at: float = 0.0
    updated_at: float = 0.0
    attempt: int = 1
    max_attempts: int = 3
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    is_complete: bool = False

    def advance_to(self, stage: TaskStage, artifacts: dict[str, Any] | None = None) -> None:
        """Advance the checkpoint to a new stage."""
        self.stage = stage.value
        self.updated_at = time.time()
        if artifacts:
            self.artifacts.update(artifacts)

    def mark_failed(self, error: str) -> None:
        """Mark this checkpoint as failed."""
        self.stage = TaskStage.FAILED.value
        self.error = error
        self.updated_at = time.time()

    def mark_complete(self, artifacts: dict[str, Any] | None = None) -> None:
        """Mark this checkpoint as complete."""
        self.stage = TaskStage.COMPLETE.value
        self.is_complete = True
        self.updated_at = time.time()
        if artifacts:
            self.artifacts.update(artifacts)

    @property
    def can_retry(self) -> bool:
        return self.attempt < self.max_attempts and self.stage != TaskStage.COMPLETE.value

    @property
    def elapsed_s(self) -> float:
        if self.started_at <= 0:
            return 0.0
        return time.time() - self.started_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskCheckpoint":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TaskPersistence:
    """File-based checkpoint persistence for crash recovery.

    Usage:
        persistence = TaskPersistence(checkpoint_dir)
        ckpt = persistence.create("task_123", "proj_456")
        ckpt.advance_to(TaskStage.ANALYZING, {"clips": 5})
        persistence.save(ckpt)
        # ... crash ...
        ckpt = persistence.load("task_123")  # Recovers last checkpoint
    """

    def __init__(self, checkpoint_dir: str | Path):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, task_id: str) -> Path:
        """Get the checkpoint file path for a task."""
        safe_id = task_id.replace("/", "_").replace("..", "_")
        return self.checkpoint_dir / f"{safe_id}.checkpoint.json"

    def create(
        self,
        task_id: str,
        project_id: str,
        max_attempts: int = 3,
    ) -> TaskCheckpoint:
        """Create a new checkpoint for a task."""
        ckpt = TaskCheckpoint(
            task_id=task_id,
            project_id=project_id,
            started_at=time.time(),
            updated_at=time.time(),
            max_attempts=max_attempts,
        )
        self.save(ckpt)
        return ckpt

    def save(self, checkpoint: TaskCheckpoint) -> None:
        """Persist a checkpoint to disk atomically."""
        path = self._path_for(checkpoint.task_id)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(checkpoint.to_dict(), indent=2))
            tmp_path.rename(path)
        except Exception as e:
            logger.warning("Failed to save checkpoint for %s: %s", checkpoint.task_id, e)
            # Clean up temp file
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def load(self, task_id: str) -> Optional[TaskCheckpoint]:
        """Load a checkpoint from disk, or None if it doesn't exist."""
        path = self._path_for(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return TaskCheckpoint.from_dict(data)
        except Exception as e:
            logger.warning("Failed to load checkpoint for %s: %s", task_id, e)
            return None

    def delete(self, task_id: str) -> None:
        """Remove a checkpoint file."""
        path = self._path_for(task_id)
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Failed to delete checkpoint for %s: %s", task_id, e)

    def list_incomplete(self) -> list[TaskCheckpoint]:
        """Find all incomplete checkpoints (potential recovery candidates)."""
        incomplete = []
        for path in self.checkpoint_dir.glob("*.checkpoint.json"):
            try:
                data = json.loads(path.read_text())
                ckpt = TaskCheckpoint.from_dict(data)
                if not ckpt.is_complete and ckpt.stage != TaskStage.FAILED.value:
                    incomplete.append(ckpt)
            except Exception as e:
                logger.warning("Failed to read checkpoint %s: %s", path, e)
        return incomplete

    def list_stale(self, stale_threshold_s: float = 3600.0) -> list[TaskCheckpoint]:
        """Find checkpoints that haven't been updated in a long time."""
        now = time.time()
        stale = []
        for path in self.checkpoint_dir.glob("*.checkpoint.json"):
            try:
                data = json.loads(path.read_text())
                ckpt = TaskCheckpoint.from_dict(data)
                if not ckpt.is_complete and (now - ckpt.updated_at) > stale_threshold_s:
                    stale.append(ckpt)
            except Exception:
                pass
        return stale

    def cleanup_completed(self, older_than_s: float = 86400.0) -> int:
        """Remove completed checkpoint files older than the threshold."""
        now = time.time()
        removed = 0
        for path in self.checkpoint_dir.glob("*.checkpoint.json"):
            try:
                data = json.loads(path.read_text())
                ckpt = TaskCheckpoint.from_dict(data)
                if ckpt.is_complete and (now - ckpt.updated_at) > older_than_s:
                    path.unlink()
                    removed += 1
            except Exception:
                pass
        return removed


class RecoveryManager:
    """Coordinates task recovery after crashes.

    Examines incomplete checkpoints and determines:
    - Whether the task can be resumed from its last stage
    - Whether it should be retried from the beginning
    - Whether it should be marked as permanently failed
    """

    def __init__(self, persistence: TaskPersistence):
        self.persistence = persistence

    def assess_recovery(self, task_id: str) -> dict[str, Any]:
        """Assess a task's recovery options.

        Returns:
            Dictionary with:
                - recoverable: bool
                - action: "resume" | "retry" | "fail"
                - last_stage: str
                - artifacts: dict of recovered artifacts
        """
        ckpt = self.persistence.load(task_id)
        if ckpt is None:
            return {
                "recoverable": False,
                "action": "retry",
                "last_stage": TaskStage.PENDING.value,
                "artifacts": {},
                "reason": "No checkpoint found",
            }

        if ckpt.is_complete:
            return {
                "recoverable": False,
                "action": "skip",
                "last_stage": ckpt.stage,
                "artifacts": ckpt.artifacts,
                "reason": "Task already completed",
            }

        if not ckpt.can_retry:
            return {
                "recoverable": False,
                "action": "fail",
                "last_stage": ckpt.stage,
                "artifacts": ckpt.artifacts,
                "reason": f"Max attempts ({ckpt.max_attempts}) exceeded",
            }

        # Stages that can be resumed (they produce durable artifacts)
        resumable_stages = {
            TaskStage.TIMELINE_GENERATION.value,
            TaskStage.RENDERING.value,
            TaskStage.MULTI_RES.value,
            TaskStage.EXPORTING.value,
        }

        if ckpt.stage in resumable_stages and ckpt.artifacts:
            return {
                "recoverable": True,
                "action": "resume",
                "last_stage": ckpt.stage,
                "artifacts": ckpt.artifacts,
                "reason": f"Can resume from {ckpt.stage} (attempt {ckpt.attempt + 1})",
            }

        # Earlier stages should just be retried from the beginning
        return {
            "recoverable": True,
            "action": "retry",
            "last_stage": ckpt.stage,
            "artifacts": ckpt.artifacts,
            "reason": f"Retrying from start (attempt {ckpt.attempt + 1})",
        }

    def recover_incomplete_tasks(self) -> list[dict[str, Any]]:
        """Find and assess all incomplete tasks."""
        incomplete = self.persistence.list_incomplete()
        return [
            {
                "task_id": ckpt.task_id,
                "project_id": ckpt.project_id,
                **self.assess_recovery(ckpt.task_id),
            }
            for ckpt in incomplete
        ]
