#!/usr/bin/env python3
"""
Video Transcriber — macOS Desktop App
Convierte vídeos a subtítulos .srt usando Whisper AI (faster-whisper).
"""

import os
import sys

# ── SSL Fix (macOS) ──────────────────────────────────────────────────────────
# En macOS el sistema de certificados de Python puede fallar al descargar el
# modelo de Whisper desde Hugging Face. certifi trae sus propios certificados
# actualizados y los inyectamos ANTES de cualquier llamada de red.
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import threading
import subprocess
import time
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog, messagebox


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


# ── Aplicación principal ──────────────────────────────────────────────────────

class TranscriberApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Video Transcriber")
        self.geometry("640x520")
        self.resizable(False, False)

        # customtkinter respeta el modo claro/oscuro del sistema
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self._video_path: Path | None = None
        self._is_processing = False
        self._start_time: float = 0.0
        self._remaining_seconds: float | None = None
        self._cancel_event = threading.Event()

        self._build_ui()
        # La comprobación de ffmpeg se hace tras construir la UI,
        # así el mensaje de error ya tiene dónde mostrarse.
        self.after(100, self._check_ffmpeg_on_startup)

    # ── Construcción de la interfaz ──────────────────────────────────────────

    def _build_ui(self):
        # Cabecera
        ctk.CTkLabel(
            self,
            text="Video Transcriber",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(pady=(28, 4))

        ctk.CTkLabel(
            self,
            text="Convierte vídeos a subtítulos .srt con Whisper AI",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        ).pack(pady=(0, 22))

        # ── Selección de archivo ─────────────────────────────────────────────
        file_frame = ctk.CTkFrame(self)
        file_frame.pack(fill="x", padx=28, pady=(0, 14))

        ctk.CTkLabel(
            file_frame, text="Archivo de vídeo:", anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).pack(fill="x", padx=16, pady=(14, 6))

        row = ctk.CTkFrame(file_frame, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))

        self._file_label = ctk.CTkLabel(
            row,
            text="Ningún archivo seleccionado",
            text_color="gray",
            anchor="w",
            wraplength=430,
        )
        self._file_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            row,
            text="Seleccionar vídeo",
            width=160,
            command=self._select_file,
        ).pack(side="right")

        # ── Opciones: modelo + idioma ─────────────────────────────────────────
        opts_frame = ctk.CTkFrame(self)
        opts_frame.pack(fill="x", padx=28, pady=(0, 14))
        opts_frame.columnconfigure((0, 1), weight=1)

        # Columna izquierda — modelo
        ctk.CTkLabel(
            opts_frame, text="Modelo Whisper:", anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=(14, 6), sticky="w")

        self._model_var = ctk.StringVar(value=DEFAULT_MODEL)
        ctk.CTkOptionMenu(
            opts_frame,
            values=WHISPER_MODELS,
            variable=self._model_var,
            width=170,
        ).grid(row=1, column=0, padx=16, pady=(0, 14), sticky="w")

        # Columna derecha — idioma
        ctk.CTkLabel(
            opts_frame, text="Idioma:", anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=1, padx=16, pady=(14, 6), sticky="w")

        self._lang_var = ctk.StringVar(value=DEFAULT_LANGUAGE)
        ctk.CTkOptionMenu(
            opts_frame,
            values=list(LANGUAGES.keys()),
            variable=self._lang_var,
            width=170,
        ).grid(row=1, column=1, padx=16, pady=(0, 14), sticky="w")

        # ── Botón principal ───────────────────────────────────────────────────
        self._transcribe_btn = ctk.CTkButton(
            self,
            text="Transcribir",
            height=46,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._start_transcription,
        )
        self._transcribe_btn.pack(fill="x", padx=28, pady=(4, 6))

        self._cancel_btn = ctk.CTkButton(
            self,
            text="Cancelar",
            height=36,
            font=ctk.CTkFont(size=14),
            fg_color="#c0392b",
            hover_color="#a93226",
            command=self._cancel_transcription,
        )
        self._cancel_btn.pack(fill="x", padx=28, pady=(0, 8))
        self._cancel_btn.pack_forget()

        # Enter como atajo de teclado
        self.bind("<Return>", lambda _: self._start_transcription())

        # ── Estado / progreso ─────────────────────────────────────────────────
        self._status_label = ctk.CTkLabel(
            self, text="", text_color="gray", font=ctk.CTkFont(size=12)
        )
        self._status_label.pack(pady=(0, 8))

        self._progress = ctk.CTkProgressBar(self, mode="indeterminate")
        # Se muestra solo durante el procesamiento
        self._progress.pack(fill="x", padx=28)
        self._progress.pack_forget()

        self._timer_label = ctk.CTkLabel(
            self, text="", text_color="gray", font=ctk.CTkFont(size=11)
        )
        self._timer_label.pack(pady=(6, 0))

        # Aviso de ffmpeg (oculto por defecto)
        self._ffmpeg_warn = ctk.CTkLabel(
            self,
            text="",
            text_color="#e05252",
            font=ctk.CTkFont(size=11),
            wraplength=580,
            justify="center",
        )
        self._ffmpeg_warn.pack(pady=(10, 0))

    # ── Comprobaciones de arranque ────────────────────────────────────────────

    def _check_ffmpeg_on_startup(self):
        if not ffmpeg_available():
            self._ffmpeg_warn.configure(text=FFMPEG_INSTALL_MSG)
            self._transcribe_btn.configure(state="disabled")

    # ── Selección de archivo ──────────────────────────────────────────────────

    def _select_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar archivo de vídeo",
            filetypes=[
                ("Archivos de vídeo", "*.mov *.mp4 *.mkv *.avi *.webm *.m4v"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not path:
            return

        p = Path(path)
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            messagebox.showwarning(
                "Formato no habitual",
                f"La extensión '{p.suffix}' puede no ser compatible.\n"
                "Extensiones recomendadas: .mov .mp4 .mkv .avi .webm .m4v",
            )

        self._video_path = p
        display = str(p) if len(str(p)) <= 72 else f"…{str(p)[-69:]}"
        self._file_label.configure(text=display, text_color=("black", "white"))
        self._clear_status()

    # ── Lanzamiento de la transcripción ──────────────────────────────────────

    def _start_transcription(self):
        if self._is_processing:
            return

        if self._video_path is None:
            messagebox.showwarning("Sin archivo", "Selecciona un archivo de vídeo primero.")
            return

        if not self._video_path.exists():
            messagebox.showerror(
                "Archivo no encontrado",
                f"No se puede leer el archivo:\n{self._video_path}",
            )
            return

        if not ffmpeg_available():
            return  # El aviso ya está visible en la UI

        self._is_processing = True
        self._remaining_seconds = None
        self._cancel_event.clear()
        self._transcribe_btn.configure(state="disabled", text="Procesando…")
        self._cancel_btn.pack(fill="x", padx=28, pady=(0, 8))
        self._progress.pack(fill="x", padx=28)
        self._progress.start()
        self._start_time = time.time()
        self._set_status("Preparando modelo Whisper…")
        self._update_timer()

        # Usamos un hilo daemon para no bloquear el loop principal de Tk
        thread = threading.Thread(target=self._transcription_worker, daemon=True)
        thread.start()

    # ── Worker (hilo secundario) ──────────────────────────────────────────────

    def _transcription_worker(self):
        """
        Todo el trabajo pesado ocurre aquí, fuera del hilo principal de la UI.
        Nunca llamamos a widgets de Tk directamente desde este hilo:
        usamos self.after(0, callback) para despachar al hilo principal.
        """
        try:
            # Import dentro del worker para que el splash de la app
            # aparezca antes de cargar PyTorch/CTranslate2.
            from faster_whisper import WhisperModel

            model_size = self._model_var.get()
            lang_code = LANGUAGES[self._lang_var.get()]  # None = auto

            self._set_status(f"Cargando modelo '{model_size}' (puede tardar la primera vez)…")

            # compute_type="int8" da buena velocidad en CPU sin GPU.
            # El modelo se guarda en ~/.cache/huggingface/hub/ y no se
            # vuelve a descargar en ejecuciones posteriores.
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

            self._set_status("Transcribiendo audio…")

            segments_generator, info = model.transcribe(
                str(self._video_path),
                language=lang_code,
                beam_size=5,
            )

            total_duration = info.duration
            segment_list = []
            for seg in segments_generator:
                if self._cancel_event.is_set():
                    self.after(0, self._on_cancelled)
                    return
                segment_list.append(seg)
                elapsed_real = time.time() - self._start_time
                if elapsed_real > 0 and seg.end > 0:
                    rate = seg.end / elapsed_real
                    remaining_audio = total_duration - seg.end
                    self._remaining_seconds = remaining_audio / rate if rate > 0 else None

            srt_content = segments_to_srt(segment_list)

            srt_path = self._video_path.with_suffix(".srt")
            srt_path.write_text(srt_content, encoding="utf-8")

            elapsed = time.time() - self._start_time
            detected_lang = info.language if lang_code is None else lang_code
            self.after(0, self._on_success, srt_path, elapsed, detected_lang)

        except ImportError:
            self.after(0, self._on_error,
                "faster-whisper no está instalado.\n"
                "Ejecuta: pip install faster-whisper"
            )
        except Exception as exc:
            self.after(0, self._on_error, self._classify_error(exc))

    def _classify_error(self, exc: Exception) -> str:
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

    # ── Callbacks del hilo principal ──────────────────────────────────────────

    def _on_success(self, srt_path: Path, elapsed: float, detected_lang: str):
        self._finish_processing()

        m, s = divmod(int(elapsed), 60)
        time_str = f"{m}m {s}s" if m else f"{s}s"
        self._status_label.configure(
            text=f"✓  Completado en {time_str}  ·  Idioma detectado: {detected_lang}",
            text_color="#2ecc71",
        )
        self._timer_label.configure(text=str(srt_path))

        answer = messagebox.askquestion(
            "Transcripción completada",
            f"Archivo guardado en:\n{srt_path}\n\n¿Abrir la carpeta en Finder?",
            icon="info",
        )
        if answer == "yes":
            # -R revela el archivo concreto dentro del Finder
            subprocess.run(["open", "-R", str(srt_path)])

    def _on_error(self, message: str):
        self._finish_processing()
        self._status_label.configure(
            text="✗  Error durante la transcripción", text_color="#e05252"
        )
        messagebox.showerror("Error", message)

    def _cancel_transcription(self):
        self._cancel_event.set()
        self._cancel_btn.configure(state="disabled", text="Cancelando…")

    def _on_cancelled(self):
        self._finish_processing()
        self._status_label.configure(text="Transcripción cancelada", text_color="gray")
        self._timer_label.configure(text="")

    def _finish_processing(self):
        self._is_processing = False
        self._progress.stop()
        self._progress.pack_forget()
        self._cancel_btn.pack_forget()
        self._cancel_btn.configure(state="normal", text="Cancelar")
        self._transcribe_btn.configure(state="normal", text="Transcribir")

    # ── Helpers de UI (seguros para llamar desde cualquier hilo via after) ────

    def _set_status(self, text: str):
        self.after(0, lambda: self._status_label.configure(text=text, text_color="gray"))

    def _clear_status(self):
        self._status_label.configure(text="", text_color="gray")
        self._timer_label.configure(text="")

    def _update_timer(self):
        """Actualiza el contador de tiempo restante cada segundo mientras procesa."""
        if not self._is_processing:
            return
        if self._remaining_seconds is not None:
            remaining = max(0, int(self._remaining_seconds))
            m, s = divmod(remaining, 60)
            self._timer_label.configure(text=f"Tiempo restante: {m:02d}:{s:02d}")
            if self._remaining_seconds > 0:
                self._remaining_seconds -= 1
        else:
            self._timer_label.configure(text="Calculando tiempo restante…")
        self.after(1000, self._update_timer)


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TranscriberApp()
    app.mainloop()
