"""
Modelos ORM del dominio TurnoMoto.

Tablas:
    clientes        - personas registradas en el sistema
    nfc_tags        - llaveros NFC asociados a clientes (1 cliente puede tener varios)
    motos           - motocicletas Suzuki de cada cliente
    servicios       - catálogo de servicios del taller
    citas           - agendamientos de servicio
    lecturas_nfc    - bitácora de todas las lecturas (auditoría + analítica)
"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Helper para timestamps timezone-aware en UTC."""
    return datetime.now(timezone.utc)


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    documento: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    telefono: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    nfc_tags: Mapped[list["NfcTag"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )
    motos: Mapped[list["Moto"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )
    citas: Mapped[list["Cita"]] = relationship(back_populates="cliente")


class NfcTag(Base):
    """Llavero NFC asociado a un cliente. Modelado como tabla separada porque un cliente
    puede tener varios llaveros (familia, reposición por pérdida, etc.)."""
    __tablename__ = "nfc_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False)
    fecha_asociacion: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    cliente: Mapped["Cliente"] = relationship(back_populates="nfc_tags")


class Moto(Base):
    __tablename__ = "motos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False)
    placa: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    marca: Mapped[str] = mapped_column(String(50), default="Suzuki")
    modelo: Mapped[str] = mapped_column(String(50), nullable=False)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str | None] = mapped_column(String(30), nullable=True)
    kilometraje: Mapped[int | None] = mapped_column(Integer, nullable=True)

    cliente: Mapped["Cliente"] = relationship(back_populates="motos")
    citas: Mapped[list["Cita"]] = relationship(back_populates="moto")


class Servicio(Base):
    """Catálogo de servicios del taller."""
    __tablename__ = "servicios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    duracion_minutos: Mapped[int] = mapped_column(Integer, default=60)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    citas: Mapped[list["Cita"]] = relationship(back_populates="servicio")


class Cita(Base):
    """Agendamiento de servicio en el taller."""
    __tablename__ = "citas"

    # Estados válidos: pendiente, confirmada, en_proceso, completada, cancelada
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False)
    moto_id: Mapped[int] = mapped_column(ForeignKey("motos.id"), nullable=False)
    servicio_id: Mapped[int] = mapped_column(ForeignKey("servicios.id"), nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cliente: Mapped["Cliente"] = relationship(back_populates="citas")
    moto: Mapped["Moto"] = relationship(back_populates="citas")
    servicio: Mapped["Servicio"] = relationship(back_populates="citas")


class LecturaNfc(Base):
    """Bitácora de todas las lecturas NFC. Útil para auditoría, debugging y analítica."""
    __tablename__ = "lecturas_nfc"

    # Resultados: cliente_encontrado, no_registrado, error
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    cliente_id: Mapped[int | None] = mapped_column(
        ForeignKey("clientes.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    resultado: Mapped[str] = mapped_column(String(30))
