"""IndicatorService — control de LEDs físicos (verde + azul) para feedback
visual del kiosko. Diseñado defensivamente: si gpiozero no está disponible
(desarrollo fuera de la Pi), el servicio simplemente loggea y no crashea.
Todas las animaciones corren como tasks asyncio para no bloquear el event loop."""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Pinout (BCM numbering)
BLUE_PIN = 17   # Pin físico 11
GREEN_PIN = 27  # Pin físico 13

try:
    from gpiozero import LED
    _GPIO_AVAILABLE = True
except Exception as e:
    LED = None  # type: ignore
    _GPIO_AVAILABLE = False
    logger.warning(f"gpiozero no disponible — IndicatorService correrá en modo no-op: {e}")


class IndicatorService:
    """Servicio idempotente y resiliente. Cada método público es no-op si
    el GPIO no está disponible (sin lanzar excepciones)."""

    def __init__(self) -> None:
        self.blue: Optional["LED"] = None
        self.green: Optional["LED"] = None
        self._current_task: Optional[asyncio.Task] = None

    @property
    def available(self) -> bool:
        return self.blue is not None and self.green is not None

    async def start(self) -> None:
        """Inicializa los LEDs y entra en estado idle (azul ON, verde OFF)."""
        if not _GPIO_AVAILABLE:
            logger.warning("IndicatorService: GPIO no disponible, sin indicadores")
            return
        try:
            self.blue = LED(BLUE_PIN)
            self.green = LED(GREEN_PIN)
            self.blue.on()
            self.green.off()
            logger.info(f"IndicatorService iniciado: BLUE=GPIO{BLUE_PIN}, GREEN=GPIO{GREEN_PIN}")
        except Exception as e:
            logger.warning(f"IndicatorService start error: {e}")
            self.blue = None
            self.green = None

    async def stop(self) -> None:
        """Cancela animaciones, apaga LEDs y libera los pines."""
        await self._cancel_current()
        for led in (self.blue, self.green):
            if led is not None:
                try:
                    led.off()
                    led.close()
                except Exception:
                    pass
        self.blue = None
        self.green = None
        logger.info("IndicatorService detenido")

    # ===== Eventos públicos (llamados desde main.py y reader.py) =====

    async def on_nfc_detected(self) -> None:
        """Apaga azul y parpadea verde 3 veces, luego vuelve a idle."""
        if not self.available:
            return
        await self._cancel_current()
        self._current_task = asyncio.create_task(self._pulse_nfc())

    async def on_cita_creada(self) -> None:
        """Verde 2 segundos."""
        if not self.available:
            return
        await self._cancel_current()
        self._current_task = asyncio.create_task(self._flash_green(2.0))

    async def on_cita_cancelada(self) -> None:
        """Verde 0.5 segundos."""
        if not self.available:
            return
        await self._cancel_current()
        self._current_task = asyncio.create_task(self._flash_green(0.5))

    async def on_estado_cambiado(self) -> None:
        """Reservado: actualmente no usado (decisión deliberada en Sesión 1).
        El LED comunica al cliente del kiosko, no al mecánico que ya ve
        feedback en su propia pantalla. Se mantiene el método por API completa."""
        return

    # ===== Internos =====

    async def _cancel_current(self) -> None:
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except (asyncio.CancelledError, Exception):
                pass
        self._current_task = None

    def _set_idle(self) -> None:
        if not self.available:
            return
        try:
            self.green.off()
            self.blue.on()
        except Exception as e:
            logger.warning(f"_set_idle error: {e}")

    async def _pulse_nfc(self) -> None:
        """Feedback al detectar NFC: apaga azul, verde parpadea 3 veces simétrico,
        vuelve a idle. ~1.2s total — coincide con el tiempo de lookup + transición."""
        try:
            assert self.blue is not None and self.green is not None
            self.blue.off()
            for _ in range(3):
                self.green.on()
                await asyncio.sleep(0.20)
                self.green.off()
                await asyncio.sleep(0.20)
            self._set_idle()
        except asyncio.CancelledError:
            self._set_idle()
            raise
        except Exception as e:
            logger.warning(f"_pulse_nfc error: {e}")
            self._set_idle()

    async def _flash_green(self, duration: float) -> None:
        """Apaga azul, enciende verde por 'duration' segundos, vuelve a idle."""
        try:
            assert self.blue is not None and self.green is not None
            self.blue.off()
            self.green.on()
            await asyncio.sleep(duration)
            self._set_idle()
        except asyncio.CancelledError:
            self._set_idle()
            raise
        except Exception as e:
            logger.warning(f"_flash_green error: {e}")
            self._set_idle()


# Singleton
indicators = IndicatorService()
