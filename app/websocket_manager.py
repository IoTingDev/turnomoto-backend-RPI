"""Gestor de conexiones WebSocket. Mantiene la lista de clientes activos y hace broadcast."""
import json
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Mantiene las conexiones WebSocket activas y emite eventos a todas ellas.

    Aunque el kiosko en producción tendrá un solo frontend conectado, esta clase
    soporta múltiples clientes (útil para el dashboard remoto que viene en la
    versión cloud, o para tener una pantalla de monitoreo paralela en el taller).
    """

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            f"Cliente WebSocket conectado. Total activos: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(
            f"Cliente WebSocket desconectado. Total activos: {len(self.active_connections)}"
        )

    async def broadcast(self, message: dict):
        """Envía un mensaje JSON a todos los clientes conectados.
        Las conexiones que fallen se descartan automáticamente."""
        if not self.active_connections:
            return

        # default=str maneja datetime, Decimal, UUID, etc. de forma segura
        payload = json.dumps(message, default=str)

        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(payload)
            except Exception as e:
                logger.warning(f"Error al enviar a cliente WebSocket: {e}")
                dead_connections.append(connection)

        for conn in dead_connections:
            self.disconnect(conn)


# Singleton compartido por toda la app
manager = ConnectionManager()



# ===== Manager separado para eventos del taller (Fase 5) =====

class TallerManager:
    """Manager de WebSocket para difundir eventos del taller al cliente del mecánico.
    Independiente del manager de NFC porque los flujos son distintos."""

    def __init__(self):
        self.active_connections: list = []

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Envía un mensaje JSON a todos los clientes conectados."""
        import json
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_text(json.dumps(message, default=str))
            except Exception:
                dead.append(ws)
        # Limpiar conexiones muertas
        for ws in dead:
            self.disconnect(ws)


taller_manager = TallerManager()
