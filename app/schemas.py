"""Schemas Pydantic para validación de request/response y eventos WebSocket."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict


# ===== Schemas de salida (response) =====

class MotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    placa: str
    marca: str
    modelo: str
    anio: int
    color: str | None
    kilometraje: int | None


class ClienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    documento: str
    telefono: str
    email: str | None
    motos: list[MotoOut] = []


class ServicioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str | None
    duracion_minutos: int


class CitaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_id: int
    moto_id: int
    servicio_id: int
    fecha_hora: datetime
    estado: str
    notas: str | None
    fecha_creacion: datetime


# ===== Schemas de entrada (request) =====

class CitaCreate(BaseModel):
    cliente_id: int
    moto_id: int
    servicio_id: int
    fecha_hora: datetime
    notas: str | None = None



class MotoCreate(BaseModel):
    placa: str
    marca: str = "Suzuki"
    modelo: str
    anio: int
    color: str | None = None
    kilometraje: int | None = 0


class ClienteCreate(BaseModel):
    nombre: str
    documento: str
    telefono: str
    email: str | None = None
    nfc_uid: str | None = None
    moto: MotoCreate



class ClienteUpdate(BaseModel):
    """Actualización parcial de cliente. documento, fecha_registro y activo son inmutables."""
    nombre: str | None = None
    telefono: str | None = None
    email: str | None = None


class MotoUpdate(BaseModel):
    """Actualización parcial de moto. cliente_id y marca son inmutables."""
    placa: str | None = None
    modelo: str | None = None
    anio: int | None = None
    color: str | None = None
    kilometraje: int | None = None


# ===== Eventos WebSocket =====

class NfcEventOut(BaseModel):
    """Evento que se emite al frontend cada vez que se detecta un tag NFC.

    Si cliente == None, el frontend debe iniciar el flujo de "tag no registrado"
    (asociar este UID a un cliente existente o crear un cliente nuevo).
    """
    event_type: str = "nfc_read"
    timestamp: datetime
    uid: str
    cliente: ClienteOut | None = None
    lectura_id: int
