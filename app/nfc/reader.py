"""
Servicio asíncrono de lectura NFC.

Evolución del test_nfc.py inicial. Diseño:
- Corre como background task del event loop de FastAPI (asyncio.create_task)
- La lectura del PN532 es BLOQUEANTE - se ejecuta en thread executor (asyncio.to_thread)
- Al detectar un UID: hace lookup en SQLite y publica evento al WebSocket manager
- Implementa anti-rebote (debounce) para evitar lecturas duplicadas del mismo tag
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import board
import busio
from adafruit_pn532.i2c import PN532_I2C

from ..config import settings
from ..database import SessionLocal
from ..models import NfcTag, Cliente, LecturaNfc
from ..schemas import NfcEventOut, ClienteOut
from ..websocket_manager import manager
from ..hardware.indicators import indicators

logger = logging.getLogger(__name__)


class NfcReaderService:
    """Servicio de lectura NFC integrado con FastAPI."""

    def __init__(self):
        self.pn532: Optional[PN532_I2C] = None
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._last_uid: Optional[bytearray] = None
        self._last_read_time: datetime = datetime.min.replace(tzinfo=timezone.utc)

    # -------------------------------------------------------
    # Inicialización hardware (bloqueante)
    # -------------------------------------------------------
    def initialize_hardware(self) -> bool:
        """Inicializa I2C y PN532. Devuelve True si todo ok."""
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.pn532 = PN532_I2C(i2c, debug=False)
            ic, ver, rev, _ = self.pn532.firmware_version
            self.pn532.SAM_configuration()
            logger.info(
                f"PN532 inicializado correctamente. "
                f"Firmware v{ver}.{rev}, IC 0x{ic:02X}"
            )
            return True
        except Exception as e:
            logger.error(f"Error al inicializar PN532: {e}")
            return False

    # -------------------------------------------------------
    # Lectura bloqueante (se ejecuta en thread executor)
    # -------------------------------------------------------
    def _read_blocking(self) -> Optional[bytearray]:
        """Una lectura del PN532. Bloqueante - llamar desde asyncio.to_thread."""
        if self.pn532 is None:
            return None
        try:
            return self.pn532.read_passive_target(timeout=0.3)
        except Exception as e:
            logger.warning(f"Error en lectura PN532: {e}")
            return None

    # -------------------------------------------------------
    # Procesamiento del UID detectado (DB lookup + construcción de evento)
    # -------------------------------------------------------
    def _process_uid(self, uid: bytearray) -> NfcEventOut:
        """Busca el UID en la BD y construye el evento. Síncrono (sesión SQLAlchemy)."""
        uid_hex = "".join(f"{b:02X}" for b in uid)
        now = datetime.now(timezone.utc)

        db = SessionLocal()
        try:
            tag = (
                db.query(NfcTag)
                .filter(NfcTag.uid == uid_hex, NfcTag.activo.is_(True))
                .first()
            )
            cliente_data: Optional[ClienteOut] = None
            resultado = "no_registrado"

            if tag:
                cliente = (
                    db.query(Cliente)
                    .filter(Cliente.id == tag.cliente_id, Cliente.activo.is_(True))
                    .first()
                )
                if cliente:
                    cliente_data = ClienteOut.model_validate(cliente)
                    resultado = "cliente_encontrado"

            # Registrar en bitácora
            lectura = LecturaNfc(
                uid=uid_hex,
                cliente_id=tag.cliente_id if tag else None,
                timestamp=now,
                resultado=resultado,
            )
            db.add(lectura)
            db.commit()
            db.refresh(lectura)

            return NfcEventOut(
                timestamp=lectura.timestamp,
                uid=uid_hex,
                cliente=cliente_data,
                lectura_id=lectura.id,
            )
        finally:
            db.close()

    # -------------------------------------------------------
    # Loop principal asíncrono
    # -------------------------------------------------------
    async def _polling_loop(self):
        """Loop infinito que sondea el NFC y publica eventos via WebSocket."""
        self.running = True
        logger.info("Loop de polling NFC iniciado")

        while self.running:
            try:
                # Lectura bloqueante movida a thread executor para no congelar el event loop
                uid = await asyncio.to_thread(self._read_blocking)

                if uid is not None:
                    now = datetime.now(timezone.utc)
                    # Anti-rebote: el mismo tag dentro de N segundos se ignora
                    is_same_tag = uid == self._last_uid
                    within_debounce = (
                        now - self._last_read_time
                    ).total_seconds() < settings.nfc_debounce_seconds

                    if is_same_tag and within_debounce:
                        await asyncio.sleep(settings.nfc_poll_interval_seconds)
                        continue

                    self._last_uid = uid
                    self._last_read_time = now

                    # Feedback físico inmediato (antes de procesar la BD para sensación instantánea)
                    asyncio.create_task(indicators.on_nfc_detected())

                    # Procesar (DB) y broadcast (WS)
                    event = await asyncio.to_thread(self._process_uid, uid)
                    await manager.broadcast(event.model_dump())

                    estado = "registrado" if event.cliente else "NO registrado"
                    logger.info(f"UID detectado: {event.uid} -> {estado}")
                else:
                    # Tag se alejó: reset para permitir re-lecturas del mismo después
                    self._last_uid = None

                await asyncio.sleep(settings.nfc_poll_interval_seconds)

            except asyncio.CancelledError:
                logger.info("Loop NFC cancelado (shutdown)")
                break
            except Exception as e:
                logger.error(f"Error inesperado en loop NFC: {e}", exc_info=True)
                await asyncio.sleep(1)  # backoff ante errores

    # -------------------------------------------------------
    # Ciclo de vida
    # -------------------------------------------------------
    async def start(self):
        """Inicializa hardware y arranca el polling como background task."""
        if not self.initialize_hardware():
            logger.error("No se pudo iniciar el servicio NFC. La API seguirá corriendo "
                         "pero no habrá detección de tags.")
            return
        self._task = asyncio.create_task(self._polling_loop())

    async def stop(self):
        """Detiene el polling de forma limpia."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Servicio NFC detenido")


# Singleton del servicio
nfc_service = NfcReaderService()
