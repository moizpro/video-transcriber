#!/usr/bin/env python3
"""
Video Transcriber — macOS Desktop App
Convierte vídeos a subtítulos .srt usando Whisper AI (faster-whisper).
"""

import subprocess
import threading
import time
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog, messagebox

from transcriber_core import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    FFMPEG_INSTALL_MSG,
    LANGUAGES,
    SUPPORTED_EXTENSIONS,
    WHISPER_MODELS,
    TranscriptionCancelled,
    TranscriptionProgress,
    classify_error,
    ffmpeg_available,
    transcribe_video,
)


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
            def on_progress(p: TranscriptionProgress):
                self._remaining_seconds = p.remaining_seconds
                self._set_status(p.status)

            model_size = self._model_var.get()
            lang_code = LANGUAGES[self._lang_var.get()]  # None = auto

            result = transcribe_video(
                self._video_path,
                model_size,
                lang_code,
                cancel_event=self._cancel_event,
                on_progress=on_progress,
            )
            self.after(
                0, self._on_success,
                result.srt_path, result.elapsed_seconds, result.detected_language,
            )

        except TranscriptionCancelled:
            self.after(0, self._on_cancelled)
        except ImportError:
            self.after(0, self._on_error,
                "faster-whisper no está instalado.\n"
                "Ejecuta: pip install faster-whisper"
            )
        except Exception as exc:
            self.after(0, self._on_error, classify_error(exc))

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
