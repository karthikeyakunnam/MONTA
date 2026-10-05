"""
MONTA — FFmpeg Process Executor
================================
Asynchronously executes compiled FFmpeg commands with process group sandboxing,
real-time progress parsing, timeout enforcement, cancellation safety, and orphan prevention.
"""

import asyncio
import logging
import os
import re
import signal
import time
from pathlib import Path
from typing import Callable, Optional

from services.edit_executor.compiler import FFmpegCommand
from shared.exceptions import MontaError
from shared.observability import catalog as m
from shared.observability.tracing import span

logger = logging.getLogger("monta.ffmpeg.executor")

TIME_PATTERN = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")


class FFmpegExecutionError(MontaError):
    def __init__(self, message: str, returncode: int, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class FFmpegTimeoutError(MontaError):
    pass


class FFmpegCancelledError(MontaError):
    pass


async def terminate_process_group(proc: asyncio.subprocess.Process, grace_s: float = 2.0) -> None:
    """Terminates the whole process group with SIGTERM escalating to SIGKILL."""
    if proc.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            await asyncio.shield(asyncio.wait_for(proc.wait(), grace_s))
            return
        except asyncio.TimeoutError:
            continue
    if proc.returncode is None:
        await asyncio.shield(proc.wait())


class FFmpegExecutor:
    """Executes FFmpeg commands with strict lifecycle controls and progress tracking."""

    def __init__(self, timeout_s: float = 300.0):
        self.timeout_s = timeout_s

    async def execute(
        self,
        command: FFmpegCommand,
        on_progress: Optional[Callable[[float, float], None]] = None,
    ) -> float:
        """
        Runs the command.
        Returns total execution time in seconds.
        Calls on_progress(percent: float, elapsed_s: float) if provided.
        """
        argv = command.to_list()
        out_path = Path(command.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with span("ffmpeg.render", encoder=command.encoder_used, duration_s=round(command.expected_duration_s, 2)) as sp:
            started = time.perf_counter()
            proc = None
            stderr_chunks: list[str] = []

            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )

                async def read_stderr():
                    while True:
                        line_bytes = await proc.stderr.readline()
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if line:
                            stderr_chunks.append(line)
                            if len(stderr_chunks) > 50:
                                stderr_chunks.pop(0)

                            # Parse time progress
                            match = TIME_PATTERN.search(line)
                            if match and on_progress and command.expected_duration_s > 0:
                                h, m_val, s = match.groups()
                                current_s = int(h) * 3600 + int(m_val) * 60 + float(s)
                                pct = min(99.0, (current_s / command.expected_duration_s) * 100.0)
                                elapsed = time.perf_counter() - started
                                on_progress(round(pct, 1), round(elapsed, 2))

                await asyncio.wait_for(
                    asyncio.gather(proc.wait(), read_stderr()),
                    timeout=self.timeout_s,
                )

            except asyncio.TimeoutError as e:
                if proc is not None:
                    await terminate_process_group(proc)
                self._cleanup_file(out_path)
                sp.set(outcome="timeout")
                raise FFmpegTimeoutError(f"FFmpeg execution timed out after {self.timeout_s}s") from e

            except asyncio.CancelledError:
                if proc is not None:
                    await terminate_process_group(proc)
                self._cleanup_file(out_path)
                sp.set(outcome="cancelled")
                raise FFmpegCancelledError("FFmpeg execution was cancelled")

            except Exception as e:
                if proc is not None:
                    await terminate_process_group(proc)
                self._cleanup_file(out_path)
                sp.set(outcome="error")
                raise

            total_elapsed = time.perf_counter() - started

            if proc.returncode != 0:
                self._cleanup_file(out_path)
                sp.set(outcome="failed", returncode=proc.returncode)
                err_text = "\n".join(stderr_chunks[-10:])
                raise FFmpegExecutionError(
                    f"FFmpeg exited with code {proc.returncode}:\n{err_text}",
                    returncode=proc.returncode,
                    stderr=err_text,
                )

            if on_progress:
                on_progress(100.0, round(total_elapsed, 2))

            sp.set(outcome="ok", elapsed_s=round(total_elapsed, 2))
            return total_elapsed

    def _cleanup_file(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.warning("failed to cleanup partial output %s: %s", path, e)
