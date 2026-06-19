"""
Núcleo de transcripción — lógica compartida entre la app de escritorio y la web.
Convierte vídeos a subtítulos .srt usando Whisper AI (faster-whisper).

Este módulo no depende de ninguna interfaz concreta (ni tkinter ni FastAPI):
expone funciones puras y `transcribe_video()`, que se invoca con un callback
de progreso y un `threading.Event` de cancelación para poder integrarse en
cualquier hilo de fondo.
"""

import os

# ── SSL Fix (macOS) ──────────────────────────────────────────────────────────
# En macOS el sistema de certificados de Python puede fallar al descargar el
# modelo de Whisper desde Hugging Face. certifi trae sus propios certificados
# actualizados y los inyectamos ANTES de cualquier llamada de red.
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# ── Constantes ───────────────────────────────────────────────────────────────

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]
DEFAULT_MODEL = "medium"

# Mapeo nombre legible → código ISO 639-1 (None = auto-detección)
LANGUAGES: dict[str, str | None] = {
    "Auto-detectar": None,
    "Español": "es",
    "English": "en",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "Português": "pt",
    "日本語": "ja",
    "中文": "zh",
    "한국어": "ko",
    "Русский": "ru",
    "العربية": "ar",
}
DEFAULT_LANGUAGE = "Español"

SUPPORTED_EXTENSIONS = {".mov", ".mp4", ".mkv", ".avi", ".webm", ".m4v"}

FFMPEG_INSTALL_MSG = (
    "⚠️  ffmpeg no está instalado — Whisper no puede leer vídeos sin él.\n"
    "Instálalo con Homebrew:  brew install ffmpeg"
)


# ── Utilidades SRT ───────────────────────────────────────────────────────────

def _seconds_to_srt_timestamp(seconds: float) -> str:
    """Convierte segundos en marca de tiempo SRT: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list) -> str:
    """Convierte la lista de segmentos de faster-whisper a texto SRT."""
    blocks = []
    for i, seg in enumerate(segments, start=1):
        start = _seconds_to_srt_timestamp(seg.start)
        end = _seconds_to_srt_timestamp(seg.end)
        blocks.append(f"{i}\n{start} --> {end}\n{seg.text.strip()}")
    return "\n\n".join(blocks) + "\n"


# ── Comprobación del sistema ──────────────────────────────────────────────────

def ffmpeg_available() -> bool:
    """Devuelve True si ffmpeg está accesible en el PATH actual."""
    # En apps empaquetadas con PyInstaller el PATH puede ser reducido;
    # añadimos las rutas habituales de Homebrew manualmente.
    env_path = os.environ.get("PATH", "")
    extra = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    os.environ["PATH"] = f"{env_path}:{extra}"

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def classify_error(exc: Exception) -> str:
    """Convierte excepciones técnicas en mensajes entendibles."""
    msg = str(exc).lower()

    if "ffmpeg" in msg or "no such file" in msg:
        return (
            "ffmpeg no encontrado en el PATH.\n"
            "Instálalo con Homebrew:  brew install ffmpeg\n\n"
            "Si ya está instalado, reinicia la app."
        )
    if "no audio" in msg or "invalid data" in msg or "moov atom" in msg:
        return (
            "El archivo no tiene pista de audio o está dañado.\n"
            "Prueba a abrirlo en QuickTime Player para verificarlo."
        )
    if "ssl" in msg or "certificate" in msg:
        return (
            "Error de certificado SSL al descargar el modelo.\n"
            "La corrección automática con certifi debería haberlo evitado.\n\n"
            "Como alternativa, ejecuta desde Terminal:\n"
            "  /Applications/Python 3.x/Install Certificates.command"
        )
    if "out of memory" in msg or "oom" in msg:
        return (
            "Memoria insuficiente para el modelo seleccionado.\n"
            "Prueba con un modelo más pequeño (small o base)."
        )
    return f"Error inesperado durante la transcripción:\n\n{exc}"


# ── Transcripción ─────────────────────────────────────────────────────────────

@dataclass
class TranscriptionProgress:
    status: str
    percent: float | None = None
    remaining_seconds: float | None = None


@dataclass
class TranscriptionResult:
    srt_path: Path
    elapsed_seconds: float
    detected_language: str


class TranscriptionCancelled(Exception):
    """Señala que la transcripción se interrumpió por petición del usuario."""


def transcribe_video(
    video_path: Path,
    model_size: str,
    lang_code: str | None,
    *,
    cancel_event: threading.Event,
    on_progress: Callable[[TranscriptionProgress], None] = lambda p: None,
) -> TranscriptionResult:
    """
    Transcribe un vídeo a .srt: carga el modelo Whisper, transcribe el audio
    y escribe el resultado junto al vídeo (misma carpeta, mismo nombre base).

    Bloqueante — debe llamarse siempre desde un hilo de fondo, nunca desde el
    hilo de UI ni desde el event loop de un servidor async.

    Lanza `TranscriptionCancelled` si `cancel_event` se activa durante la
    transcripción. Cualquier otra excepción (ImportError, errores de
    faster-whisper, etc.) se propaga sin modificar para que el llamador la
    trate con `classify_error()`.
    """
    start_time = time.time()

    # Import dentro de la función para que la UI que la invoque pueda
    # mostrarse antes de cargar PyTorch/CTranslate2.
    from faster_whisper import WhisperModel

    on_progress(TranscriptionProgress(
        status=f"Cargando modelo '{model_size}' (puede tardar la primera vez)…"
    ))

    # compute_type="int8" da buena velocidad en CPU sin GPU.
    # El modelo se guarda en ~/.cache/huggingface/hub/ y no se
    # vuelve a descargar en ejecuciones posteriores.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    on_progress(TranscriptionProgress(status="Transcribiendo audio…"))

    segments_generator, info = model.transcribe(
        str(video_path),
        language=lang_code,
        beam_size=5,
    )

    total_duration = info.duration
    segment_list = []
    for seg in segments_generator:
        if cancel_event.is_set():
            raise TranscriptionCancelled()

        segment_list.append(seg)

        elapsed_real = time.time() - start_time
        remaining_seconds = None
        if elapsed_real > 0 and seg.end > 0:
            rate = seg.end / elapsed_real
            remaining_audio = total_duration - seg.end
            remaining_seconds = remaining_audio / rate if rate > 0 else None

        percent = None
        if total_duration > 0:
            percent = min(99.0, (seg.end / total_duration) * 100)

        on_progress(TranscriptionProgress(
            status="Transcribiendo audio…",
            percent=percent,
            remaining_seconds=remaining_seconds,
        ))

    srt_content = segments_to_srt(segment_list)
    srt_path = video_path.with_suffix(".srt")
    srt_path.write_text(srt_content, encoding="utf-8")

    return TranscriptionResult(
        srt_path=srt_path,
        elapsed_seconds=time.time() - start_time,
        detected_language=info.language if lang_code is None else lang_code,
    )
