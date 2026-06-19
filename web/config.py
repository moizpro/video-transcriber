"""
Configuración del servidor web, leída de variables de entorno.

Centraliza todo el acceso a `os.environ` en un solo sitio. `TRANSCRIBER_PASSWORD`
y `SECRET_KEY` son obligatorias: una app pública nunca debe arrancar sin
contraseña ni clave de firma de sesión, así que su ausencia aborta el arranque
en vez de degradar silenciosamente a un modo inseguro.
"""

import os
from pathlib import Path


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno obligatoria '{name}'. "
            f"Consulta .env.example para configurarla."
        )
    return value


TRANSCRIBER_PASSWORD: str = _require_env("TRANSCRIBER_PASSWORD")
SECRET_KEY: str = _require_env("SECRET_KEY")

MAX_UPLOAD_BYTES: int = int(os.environ.get("MAX_UPLOAD_MB", "2048")) * 1024 * 1024
JOB_TTL_SECONDS: int = int(os.environ.get("JOB_TTL_MINUTES", "60")) * 60
UPLOAD_DIR: Path = Path(os.environ.get("UPLOAD_DIR", "/tmp/transcriber_jobs"))

LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 300

# Cookies de sesión solo por HTTPS salvo que se indique lo contrario
# (desactívalo en desarrollo local con SESSION_HTTPS_ONLY=false).
SESSION_HTTPS_ONLY: bool = os.environ.get("SESSION_HTTPS_ONLY", "true").lower() != "false"
SESSION_MAX_AGE_SECONDS: int = 7 * 24 * 3600  # 7 días

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
