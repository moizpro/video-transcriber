"""
Autenticación de la web: una única contraseña compartida (sin cuentas de
usuario) protegida por una sesión firmada y un límite de intentos por IP.

Cada sesión autenticada recibe un `session_id` propio, usado en `jobs.py` para
que ninguna persona pueda ver, cancelar o descargar el trabajo de otra aunque
todas compartan la misma contraseña.
"""

import hmac
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request

from . import config

router = APIRouter()

_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    with _login_attempts_lock:
        attempts = [
            t for t in _login_attempts.get(ip, [])
            if now - t < config.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        ]
        _login_attempts[ip] = attempts
        if len(attempts) >= config.LOGIN_RATE_LIMIT_ATTEMPTS:
            raise HTTPException(429, "Demasiados intentos. Inténtalo de nuevo en unos minutos.")


def _record_failed_attempt(ip: str) -> None:
    with _login_attempts_lock:
        _login_attempts.setdefault(ip, []).append(time.time())


@router.post("/api/login")
async def login(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    _check_rate_limit(ip)

    if not hmac.compare_digest(password.encode(), config.TRANSCRIBER_PASSWORD.encode()):
        _record_failed_attempt(ip)
        raise HTTPException(401, "Contraseña incorrecta.")

    request.session["authenticated"] = True
    request.session["session_id"] = uuid4().hex
    return {"ok": True}


@router.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


def require_auth(request: Request) -> str:
    """Dependencia de FastAPI: exige sesión autenticada y devuelve su session_id."""
    session_id = request.session.get("session_id")
    if not request.session.get("authenticated") or not session_id:
        raise HTTPException(401, "No autenticado.")
    return session_id
