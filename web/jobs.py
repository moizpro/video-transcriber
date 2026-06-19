"""
Gestión de trabajos de transcripción para la interfaz web.

Un único hilo de fondo procesa los trabajos en el orden en que llegan (cola
FIFO), garantizando que nunca se ejecuten dos transcripciones Whisper a la vez
en el servidor — el mismo comportamiento que la app de escritorio, extendido a
múltiples usuarios concurrentes que ahora esperan su turno en lugar de
bloquearse mutuamente.
"""

import queue
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from uuid import uuid4

from transcriber_core import (
    TranscriptionCancelled,
    TranscriptionProgress,
    classify_error,
    transcribe_video,
)


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    status: JobStatus
    created_at: float
    video_path: Path
    model_size: str
    lang_code: str | None
    owner_session_id: str
    srt_path: Path | None = None
    status_text: str = "En cola…"
    percent: float | None = None
    remaining_seconds: float | None = None
    error_message: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finished_at: float | None = None

    def public_state(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "status_text": self.status_text,
            "percent": self.percent,
            "remaining_seconds": self.remaining_seconds,
            "error_message": self.error_message,
            "download_url": f"/api/jobs/{self.id}/download" if self.status == JobStatus.DONE else None,
        }


class JobManager:
    """
    Cola FIFO + un único hilo worker: garantiza una transcripción a la vez en
    todo el servidor. Un `threading.Lock` protege todas las lecturas/escrituras
    de cada `Job`, ya que el worker las actualiza mientras las peticiones HTTP
    de polling las leen concurrentemente.
    """

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self._worker_thread.start()

    def submit(
        self,
        *,
        video_path: Path,
        model_size: str,
        lang_code: str | None,
        owner_session_id: str,
    ) -> Job:
        job = Job(
            id=uuid4().hex,
            status=JobStatus.QUEUED,
            created_at=time.time(),
            video_path=video_path,
            model_size=model_size,
            lang_code=lang_code,
            owner_session_id=owner_session_id,
        )
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job

    def get_state(self, job_id: str, owner_session_id: str) -> dict | None:
        """Snapshot de un job para exponer por la API, o None si no existe/no es suyo."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_session_id != owner_session_id:
                return None
            return job.public_state()

    def get_owned_job(self, job_id: str, owner_session_id: str) -> Job | None:
        """Job "vivo" para operaciones internas (cancelar, descargar)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.owner_session_id != owner_session_id:
            return None
        return job

    def cancel(self, job_id: str, owner_session_id: str) -> bool:
        job = self.get_owned_job(job_id, owner_session_id)
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
            return False
        job.cancel_event.set()
        return True

    def purge_expired(self, ttl_seconds: int) -> None:
        """Borra del disco y de memoria los jobs terminados hace más de `ttl_seconds`."""
        now = time.time()
        with self._lock:
            expired = [
                job for job in self._jobs.values()
                if job.finished_at is not None and (now - job.finished_at) > ttl_seconds
            ]
        for job in expired:
            shutil.rmtree(job.video_path.parent, ignore_errors=True)
            with self._lock:
                self._jobs.pop(job.id, None)

    # ── Internos ──────────────────────────────────────────────────────────────

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)

    def _run_worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self._jobs.get(job_id)
            if job is None:
                continue

            if job.cancel_event.is_set():
                self._update(job_id, status=JobStatus.CANCELLED, finished_at=time.time())
                continue

            self._update(job_id, status=JobStatus.PROCESSING, status_text="Preparando modelo Whisper…")

            def on_progress(p: TranscriptionProgress, _job_id=job_id) -> None:
                self._update(_job_id, status_text=p.status, percent=p.percent, remaining_seconds=p.remaining_seconds)

            try:
                result = transcribe_video(
                    job.video_path,
                    job.model_size,
                    job.lang_code,
                    cancel_event=job.cancel_event,
                    on_progress=on_progress,
                )
                self._update(
                    job_id,
                    status=JobStatus.DONE,
                    srt_path=result.srt_path,
                    status_text="Completado",
                    percent=100.0,
                    remaining_seconds=0,
                )
            except TranscriptionCancelled:
                self._update(job_id, status=JobStatus.CANCELLED, status_text="Transcripción cancelada")
            except ImportError:
                self._update(
                    job_id,
                    status=JobStatus.ERROR,
                    status_text="Error",
                    error_message="faster-whisper no está instalado en el servidor.",
                )
            except Exception as exc:
                self._update(job_id, status=JobStatus.ERROR, status_text="Error", error_message=classify_error(exc))
            finally:
                job.video_path.unlink(missing_ok=True)
                self._update(job_id, finished_at=time.time())
