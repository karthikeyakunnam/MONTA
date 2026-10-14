"""
MONTA — Task Persistence & Recovery Test Suite
=================================================
Tests checkpoint creation, saving, loading, recovery assessment,
and stale/incomplete detection.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from shared.task_persistence import (
    RecoveryManager,
    TaskCheckpoint,
    TaskPersistence,
    TaskStage,
)


class TestTaskCheckpoint(unittest.TestCase):
    """Test TaskCheckpoint dataclass logic."""

    def test_create_default(self):
        ckpt = TaskCheckpoint(task_id="task_1", project_id="proj_1")
        self.assertEqual(ckpt.stage, TaskStage.PENDING.value)
        self.assertFalse(ckpt.is_complete)
        self.assertEqual(ckpt.attempt, 1)

    def test_advance_to(self):
        ckpt = TaskCheckpoint(task_id="task_1", project_id="proj_1")
        ckpt.advance_to(TaskStage.ANALYZING, {"clips": 5})
        self.assertEqual(ckpt.stage, TaskStage.ANALYZING.value)
        self.assertEqual(ckpt.artifacts["clips"], 5)
        self.assertGreater(ckpt.updated_at, 0)

    def test_advance_preserves_previous_artifacts(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1")
        ckpt.advance_to(TaskStage.PROBING, {"probe_count": 3})
        ckpt.advance_to(TaskStage.ANALYZING, {"analyzed": 2})
        self.assertEqual(ckpt.artifacts["probe_count"], 3)
        self.assertEqual(ckpt.artifacts["analyzed"], 2)

    def test_mark_failed(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1")
        ckpt.mark_failed("OOM killed")
        self.assertEqual(ckpt.stage, TaskStage.FAILED.value)
        self.assertEqual(ckpt.error, "OOM killed")

    def test_mark_complete(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1")
        ckpt.mark_complete({"output_path": "/tmp/final.mp4"})
        self.assertTrue(ckpt.is_complete)
        self.assertEqual(ckpt.stage, TaskStage.COMPLETE.value)
        self.assertEqual(ckpt.artifacts["output_path"], "/tmp/final.mp4")

    def test_can_retry(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1", max_attempts=3, attempt=1)
        self.assertTrue(ckpt.can_retry)
        ckpt.attempt = 3
        self.assertFalse(ckpt.can_retry)

    def test_can_retry_false_when_complete(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1", attempt=1)
        ckpt.mark_complete()
        self.assertFalse(ckpt.can_retry)

    def test_serialization_roundtrip(self):
        ckpt = TaskCheckpoint(
            task_id="t1", project_id="p1",
            stage=TaskStage.RENDERING.value,
            started_at=1000.0, updated_at=2000.0,
            attempt=2, max_attempts=3,
            artifacts={"timeline": "serialized"},
        )
        data = ckpt.to_dict()
        restored = TaskCheckpoint.from_dict(data)
        self.assertEqual(restored.task_id, "t1")
        self.assertEqual(restored.stage, TaskStage.RENDERING.value)
        self.assertEqual(restored.attempt, 2)
        self.assertEqual(restored.artifacts["timeline"], "serialized")

    def test_elapsed_s(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1", started_at=time.time() - 10)
        self.assertGreaterEqual(ckpt.elapsed_s, 9.0)

    def test_elapsed_s_not_started(self):
        ckpt = TaskCheckpoint(task_id="t1", project_id="p1")
        self.assertEqual(ckpt.elapsed_s, 0.0)


class TestTaskPersistence(unittest.TestCase):
    """Test file-based checkpoint persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.persistence = TaskPersistence(self.tmpdir)

    def test_create_and_load(self):
        ckpt = self.persistence.create("task_abc", "proj_xyz")
        loaded = self.persistence.load("task_abc")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.task_id, "task_abc")
        self.assertEqual(loaded.project_id, "proj_xyz")

    def test_save_and_load(self):
        ckpt = self.persistence.create("t1", "p1")
        ckpt.advance_to(TaskStage.RENDERING, {"timeline_id": "tl_123"})
        self.persistence.save(ckpt)
        loaded = self.persistence.load("t1")
        self.assertEqual(loaded.stage, TaskStage.RENDERING.value)
        self.assertEqual(loaded.artifacts["timeline_id"], "tl_123")

    def test_load_nonexistent(self):
        loaded = self.persistence.load("nonexistent")
        self.assertIsNone(loaded)

    def test_delete(self):
        self.persistence.create("t1", "p1")
        self.persistence.delete("t1")
        self.assertIsNone(self.persistence.load("t1"))

    def test_delete_nonexistent_is_safe(self):
        self.persistence.delete("nonexistent")  # Should not raise

    def test_list_incomplete(self):
        c1 = self.persistence.create("t1", "p1")
        c1.advance_to(TaskStage.ANALYZING)
        self.persistence.save(c1)

        c2 = self.persistence.create("t2", "p2")
        c2.mark_complete()
        self.persistence.save(c2)

        c3 = self.persistence.create("t3", "p3")
        c3.mark_failed("error")
        self.persistence.save(c3)

        incomplete = self.persistence.list_incomplete()
        task_ids = {c.task_id for c in incomplete}
        self.assertIn("t1", task_ids)
        self.assertNotIn("t2", task_ids)  # Complete
        self.assertNotIn("t3", task_ids)  # Failed

    def test_list_stale(self):
        c1 = self.persistence.create("t1", "p1")
        c1.updated_at = time.time() - 7200  # 2 hours ago
        self.persistence.save(c1)

        c2 = self.persistence.create("t2", "p2")
        # c2 just created, should not be stale

        stale = self.persistence.list_stale(stale_threshold_s=3600)
        task_ids = {c.task_id for c in stale}
        self.assertIn("t1", task_ids)
        self.assertNotIn("t2", task_ids)

    def test_cleanup_completed(self):
        c1 = self.persistence.create("t1", "p1")
        c1.mark_complete()
        c1.updated_at = time.time() - 100000  # Old
        self.persistence.save(c1)

        c2 = self.persistence.create("t2", "p2")
        c2.mark_complete()
        # c2 is recent

        removed = self.persistence.cleanup_completed(older_than_s=86400)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.persistence.load("t1"))
        self.assertIsNotNone(self.persistence.load("t2"))

    def test_atomic_save_creates_file(self):
        ckpt = TaskCheckpoint(task_id="atomic_test", project_id="p1")
        self.persistence.save(ckpt)
        path = self.persistence._path_for("atomic_test")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["task_id"], "atomic_test")

    def test_path_sanitization(self):
        """Task IDs with path traversal characters should be sanitized."""
        path = self.persistence._path_for("../../../etc/passwd")
        self.assertNotIn("..", str(path.name))


class TestRecoveryManager(unittest.TestCase):
    """Test RecoveryManager assessment logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.persistence = TaskPersistence(self.tmpdir)
        self.recovery = RecoveryManager(self.persistence)

    def test_no_checkpoint_returns_retry(self):
        result = self.recovery.assess_recovery("nonexistent")
        self.assertFalse(result["recoverable"])
        self.assertEqual(result["action"], "retry")

    def test_complete_returns_skip(self):
        ckpt = self.persistence.create("t1", "p1")
        ckpt.mark_complete({"output": "/tmp/done.mp4"})
        self.persistence.save(ckpt)
        result = self.recovery.assess_recovery("t1")
        self.assertEqual(result["action"], "skip")

    def test_rendering_stage_is_resumable(self):
        ckpt = self.persistence.create("t1", "p1")
        ckpt.advance_to(TaskStage.RENDERING, {"timeline_path": "/tmp/tl.json"})
        self.persistence.save(ckpt)
        result = self.recovery.assess_recovery("t1")
        self.assertTrue(result["recoverable"])
        self.assertEqual(result["action"], "resume")

    def test_early_stage_triggers_retry(self):
        ckpt = self.persistence.create("t1", "p1")
        ckpt.advance_to(TaskStage.PROBING)
        self.persistence.save(ckpt)
        result = self.recovery.assess_recovery("t1")
        self.assertTrue(result["recoverable"])
        self.assertEqual(result["action"], "retry")

    def test_max_attempts_exceeded(self):
        ckpt = self.persistence.create("t1", "p1", max_attempts=2)
        ckpt.attempt = 2
        ckpt.advance_to(TaskStage.RENDERING, {"timeline": "data"})
        self.persistence.save(ckpt)
        result = self.recovery.assess_recovery("t1")
        self.assertFalse(result["recoverable"])
        self.assertEqual(result["action"], "fail")

    def test_recover_incomplete_tasks(self):
        c1 = self.persistence.create("t1", "p1")
        c1.advance_to(TaskStage.RENDERING, {"timeline": "data"})
        self.persistence.save(c1)

        c2 = self.persistence.create("t2", "p2")
        c2.advance_to(TaskStage.ANALYZING)
        self.persistence.save(c2)

        results = self.recovery.recover_incomplete_tasks()
        self.assertEqual(len(results), 2)
        actions = {r["task_id"]: r["action"] for r in results}
        self.assertEqual(actions["t1"], "resume")
        self.assertEqual(actions["t2"], "retry")


if __name__ == "__main__":
    unittest.main()
