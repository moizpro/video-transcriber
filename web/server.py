"""
Video Transcriber — Interfaz Web
App FastAPI: login con contraseña compartida, subida de vídeo, cola de
transcripción (un trabajo a la vez) y descarga del .srt resultante.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from transcriber_core import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    LANGUAGES,
    SUPPORTED_EXTENSIONS,
    WHISPER_MODELS,
)

from . import auth, config
from .auth import require_auth
from .jobs import JobManager

STATIC_DIR = Path(__file__).parent / "static"
CLEANUP_INTERVAL_SECONDS = 300


async def _periodic_cleanup(job_manager: JobManager) -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        job_manager.purge_expired(config.JOB_TTL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.job_manager = JobManager()
    cleanup_task = asyncio.create_task(_periodic_cleanup(app.state.job_manager))
    try:
        yield
    finally:
        cleanup_task.cancel()


app = FastAPI(title="Video Transcriber", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    https_only=config.SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=config.SESSION_MAX_AGE_SECONDS,
)
app.include_router(auth.router)


# ── Modelos de petición/respuesta ─────────────────────────────────────────────

class ConfigResponse(BaseModel):
    models: list[str]
    default_model: str
    languages: dict[str, str | None]
    default_language: str
    extensions: list[str]
    max_upload_mb: int


class JobCreateResponse(BaseModel):
    id: str
    status: str


class JobStateResponse(BaseModel):
    id: str
    status: str
    status_text: str
    percent: float | None
    remaining_seconds: float | None
    error_message: str | None
    download_url: str | None


# ── Rutas ──────────────────────────────────────────────────────────────────────

@app.get("/api/config", response_model=ConfigResponse)
async def get_config(session_id: str = Depends(require_auth)):
    return ConfigResponse(
        models=WHISPER_MODELS,
        default_model=DEFAULT_MODEL,
        languages=LANGUAGES,
        default_language=DEFAULT_LANGUAGE,
        extensions=sorted(SUPPORTED_EXTENSIONS),
        max_upload_mb=config.MAX_UPLOAD_BYTES // (1024 * 1024),
    )


@app.post("/api/jobs", response_model=JobCreateResponse, status_code=201)
async def create_job(
    request: Request,
    file: UploadFile,
    model: str = Form(DEFAULT_MODEL),
    language: str = Form(DEFAULT_LANGUAGE),
    session_id: str = Depends(require_auth),
):
    if model not in WHISPER_MODELS:
        raise HTTPException(400, "Modelo no válido.")
    if language not in LANGUAGES:
        raise HTTPException(400, "Idioma no válido.")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Extensión '{suffix}' no soportada.")

    job_dir = config.UPLOAD_DIR / uuid4().hex
    job_dir.mkdir(parents=True)
    dest_path = job_dir / f"source{suffix}"

    total = 0
    try:
        with dest_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Archivo demasiado grande.")
                out.write(chunk)
        if total == 0:
            raise HTTPException(400, "El archivo está vacío.")
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        job_dir.rmdir()
        raise

    job_manager: JobManager = request.app.state.job_manager
    job = job_manager.submit(
        video_path=dest_path,
        model_size=model,
        lang_code=LANGUAGES[language],
        owner_session_id=session_id,
    )
    return JobCreateResponse(id=job.id, status=job.status.value)


@app.get("/api/jobs/{job_id}", response_model=JobStateResponse)
async def get_job(job_id: str, request: Request, session_id: str = Depends(require_auth)):
    job_manager: JobManager = request.app.state.job_manager
    state = job_manager.get_state(job_id, session_id)
    if state is None:
        raise HTTPException(404, "Trabajo no encontrado.")
    return JobStateResponse(**state)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, session_id: str = Depends(require_auth)):
    job_manager: JobManager = request.app.state.job_manager
    if not job_manager.cancel(job_id, session_id):
        raise HTTPException(409, "El trabajo ya ha terminado o no existe.")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str, request: Request, session_id: str = Depends(require_auth)):
    job_manager: JobManager = request.app.state.job_manager
    job = job_manager.get_owned_job(job_id, session_id)
    if job is None or job.srt_path is None or not job.srt_path.exists():
        raise HTTPException(404, "Subtítulo no disponible.")
    return FileResponse(job.srt_path, filename=job.srt_path.name, media_type="text/plain")


# Estáticos del frontend, montados al final para no eclipsar las rutas /api/*.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
