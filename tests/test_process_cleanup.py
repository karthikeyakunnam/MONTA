"""Critical bug #5 — no orphan media processes after timeout or cancellation (full process tree)."""

import asyncio
import os
import shutil
import uuid

import pytest

from orchestration.agents.intelligence.media import FFmpegMediaBackend, MediaToolError, check_input_path

pytestmark = pytest.mark.skipif(not shutil.which("sh") or not shutil.which("pgrep"), reason="needs sh and pgrep")


def _alive(marker: str) -> int:
    return int(os.popen(f"pgrep -f 'sleep {marker}' | wc -l").read().strip() or 0)


def _tree_cmd(marker: str) -> list[str]:
    # a parent shell with two grandchildren — the tree ffmpeg filters/demuxers can create
    return ["sh", "-c", f"sleep {marker} & sleep {marker} & wait"]


async def test_timeout_kills_whole_tree():
    marker = f"{30 + uuid.uuid4().int % 1000}.{uuid.uuid4().int % 997}"
    with pytest.raises(MediaToolError) as info:
        await FFmpegMediaBackend(timeout_s=0.3)._run(_tree_cmd(marker))
    assert info.value.retryable
    await asyncio.sleep(0.3)
    assert _alive(marker) == 0


async def test_cancellation_kills_whole_tree():
    marker = f"{30 + uuid.uuid4().int % 1000}.{uuid.uuid4().int % 997}"
    task = asyncio.create_task(FFmpegMediaBackend(timeout_s=60)._run(_tree_cmd(marker)))
    await asyncio.sleep(0.3)
    assert _alive(marker) >= 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert _alive(marker) == 0


async def test_director_timeout_leaves_no_processes():
    from orchestration.agents.director import AgentRegistry, ExecutionContext, TaskGraphExecutor
    from shared.contracts.director import RetryPolicy, TaskKind, TaskSpec
    from tests.test_director import _plan, pack

    marker = f"{30 + uuid.uuid4().int % 1000}.{uuid.uuid4().int % 997}"

    async def media_task(task, ctx):
        return await FFmpegMediaBackend(timeout_s=60)._run(_tree_cmd(marker))

    reg = AgentRegistry()
    reg.register("a", media_task)
    task = TaskSpec(task_id="clip", kind=TaskKind.ANALYZE_CLIP, agent="a", critical=False, timeout_s=0.3,
                    retry=RetryPolicy(max_attempts=1), rationale="t")
    plan = _plan([task])
    await TaskGraphExecutor(reg).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    await asyncio.sleep(0.3)
    assert _alive(marker) == 0


def test_input_sandbox(tmp_path):
    real = tmp_path / "a.mp4"
    real.write_bytes(b"x")
    link = tmp_path / "link.mp4"
    link.symlink_to(real)
    with pytest.raises(MediaToolError, match="non-file"):
        check_input_path("http://evil/x.mp4", ())
    with pytest.raises(MediaToolError, match="non-file"):
        check_input_path("concat:a|b", ())
    with pytest.raises(MediaToolError, match="symlink"):
        check_input_path(str(link), ())
    with pytest.raises(MediaToolError, match="outside"):
        check_input_path(str(real), (tmp_path / "other",))
    assert check_input_path(str(real), (tmp_path,)) == real.resolve()
