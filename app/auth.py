"""
Autenticación simple por PIN para vistas admin del kiosko.
No es un sistema multiusuario — es un gate de sesión para un único
appliance físico. Tokens en memoria, sin persistencia entre restarts
del backend (comportamiento deseado: reinicio = todas las sesiones admin caen).

NOTA: pydantic-settings (usado en config.py) parsea el .env dentro del objeto
Settings, pero NO lo inyecta en os.environ. Como este módulo lee los PINs
directo con os.getenv (deliberado, para no acoplar secretos a la clase Settings
general), necesita cargar el .env explícitamente con load_dotenv().
"""
import os
import time
import secrets
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

load_dotenv()

router = APIRouter(prefix="/auth", tags=["auth"])

PIN_MECANICO = os.getenv("PIN_MECANICO")
PIN_GERENCIA = os.getenv("PIN_GERENCIA")

if not PIN_MECANICO or not PIN_GERENCIA:
    raise RuntimeError(
        "PIN_MECANICO y PIN_GERENCIA deben estar definidos en .env"
    )

TOKEN_TTL_SECONDS = 15 * 60
MAX_ATTEMPTS = 3
LOCKOUT_SECONDS = 30

Role = Literal["mecanico", "gerencia"]

_sessions: dict[str, dict] = {}
_failed = {"count": 0, "locked_until": 0.0}


class PinRequest(BaseModel):
    pin: str


class AuthResponse(BaseModel):
    token: str
    role: Role
    expires_in: int


def _check_lockout() -> None:
    now = time.time()
    if _failed["locked_until"] > now:
        remaining = int(_failed["locked_until"] - now)
        raise HTTPException(
            status_code=429, detail=f"Demasiados intentos. Espera {remaining}s"
        )


def _register_failure() -> None:
    _failed["count"] += 1
    if _failed["count"] >= MAX_ATTEMPTS:
        _failed["locked_until"] = time.time() + LOCKOUT_SECONDS
        _failed["count"] = 0


def _register_success() -> None:
    _failed["count"] = 0
    _failed["locked_until"] = 0.0


@router.post("/admin", response_model=AuthResponse)
def login_admin(body: PinRequest) -> AuthResponse:
    _check_lockout()

    if body.pin == PIN_GERENCIA:
        role: Role = "gerencia"
    elif body.pin == PIN_MECANICO:
        role = "mecanico"
    else:
        _register_failure()
        raise HTTPException(status_code=401, detail="PIN incorrecto")

    _register_success()
    token = secrets.token_urlsafe(24)
    _sessions[token] = {"role": role, "expires_at": time.time() + TOKEN_TTL_SECONDS}
    return AuthResponse(token=token, role=role, expires_in=TOKEN_TTL_SECONDS)


def get_session(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ")
    session = _sessions.get(token)
    if not session or session["expires_at"] < time.time():
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="Sesión expirada")
    return session


def require_role(*roles: Role):
    def _dep(session: dict = Depends(get_session)) -> dict:
        if session["role"] not in roles:
            raise HTTPException(status_code=403, detail="Permiso insuficiente")
        return session
    return _dep
