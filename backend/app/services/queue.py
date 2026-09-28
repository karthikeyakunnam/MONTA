"""
MONTA — Pipeline Queue
========================
The backend submits work **by task name** through a Celery producer. It never
imports ``workers`` or the pipeline, so the API image stays free of numpy,
FFmpeg and model dependencies, and a worker deploy cannot break the API.

Submission is synchronous but bounded: ``send_task`` runs in a thread with a
timeout, because a broker that accepts TCP but never answers would otherwise
hang the request. A failure raises ``QueueUnavailable``, which the API maps to
503 with the job left in ``submit_failed`` for retry.
"""

import asyncio
import logging
from dataclasses import dataclass

from celery import Celery

from shared.exceptions import MontaError
from shared.observability import catalog as m

logger = logging.getLogger("monta.queue")


class QueueUnavailable(MontaError):
    """The broker did not accept the job."""


@dataclass
class SubmittedJob:
    task_id: str
    queued: bool


class PipelineQueue:
    def __init__(self, *, broker_url: str, result_backend: str, task_name: str, timeout_s: float = 5.0,
                 enabled: bool = True):
        self.broker_url = broker_url
        self.result_backend = result_backend
        self.task_name = task_name
        self.timeout_s = timeout_s
        self.enabled = enabled
        self._app: Celery | None = None

    def app(self) -> Celery:
        if self._app is None:
            self._app = Celery("monta-producer", broker=self.broker_url, backend=self.result_backend)
            self._app.conf.update(
                broker_connection_retry_on_startup=True,
                broker_transport_options={"socket_timeout": self.timeout_s, "socket_connect_timeout": self.timeout_s},
                task_acks_late=True,
                task_reject_on_worker_lost=True,
            )
        return self._app

    async def submit_pipeline(self, *, job_id: str, project_id: str, trace_id: str | None = None) -> SubmittedJob:
        """Hand one pipeline job to the broker. Raises ``QueueUnavailable`` on any failure."""
        if not self.enabled:
            raise QueueUnavailable("the job queue is disabled on this deployment (QUEUE_ENABLED=false)")
        payload = {"job_id": job_id, "project_id": project_id, "trace_id": trace_id}
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.app().send_task, self.task_name, kwargs=payload, task_id=job_id),
                self.timeout_s,
            )
        except asyncio.TimeoutError as e:
            m.JOBS_SUBMITTED.inc(kind="pipeline", outcome="timeout")
            raise QueueUnavailable(f"the job queue did not respond within {self.timeout_s:.0f}s") from e
        except Exception as e:
            m.JOBS_SUBMITTED.inc(kind="pipeline", outcome="error")
            raise QueueUnavailable(f"the job queue rejected the job: {e}") from e
        m.JOBS_SUBMITTED.inc(kind="pipeline", outcome="queued")
        logger.info("pipeline job queued", extra={"job_id": job_id, "project_id": project_id, "task_id": result.id})
        return SubmittedJob(task_id=result.id, queued=True)

    async def ping(self) -> bool:
        """True when the broker answers. Used by /health so the UI can warn before an upload."""
        if not self.enabled:
            return False
        try:
            def check() -> bool:
                with self.app().connection_for_write() as conn:
                    conn.ensure_connection(max_retries=0, timeout=self.timeout_s)
                    return True

            return await asyncio.wait_for(asyncio.to_thread(check), self.timeout_s)
        except Exception:
            return False
