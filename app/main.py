import asyncio
"""
TurnoMoto - Backend FastAPI

Punto de entrada. Estructura:
    - Lifespan: crea tablas al inicio, arranca lector NFC, limpia al cerrar
    - CORS: configurable via settings
    - Endpoints REST básicos: salud, clientes, servicios, citas
    - Auth: router de PIN para vistas admin (mecánico / gerencia)
    - WebSocket /ws/nfc: stream de eventos NFC al frontend

Ejecutar:
    cd ~/turnomoto/backend
    source ~/turnomoto-env/bin/activate
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload
from .config import settings
from .database import engine, Base, get_db
from .websocket_manager import manager, taller_manager
from .nfc.reader import nfc_service
from .hardware.indicators import indicators
from .auth import router as auth_router, require_role
from .routers.gerencia import router as gerencia_router
from .models import Cliente, Servicio, Cita, Moto, NfcTag
from .schemas import ClienteOut, ServicioOut, CitaOut, CitaCreate, ClienteCreate, ClienteUpdate, MotoUpdate, MotoOut
# ----- Logging -----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ----- Lifespan: gestión del ciclo de vida de la app -----
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup y shutdown del backend.

    Startup:
        1. Crear tablas SQLite si no existen
        2. Inicializar y arrancar el servicio NFC como background task
    Shutdown:
        1. Detener el servicio NFC limpiamente
    """
    logger.info("=" * 50)
    logger.info(f"Iniciando TurnoMoto backend v0.1.0")
    logger.info(f"Concesionario: {settings.concesionario_nombre}")
    logger.info("=" * 50)

    Base.metadata.create_all(bind=engine)
    logger.info("Tablas SQLite verificadas")

    await nfc_service.start()
    await indicators.start()
    logger.info("Backend listo y escuchando")

    yield  # <- la aplicación corre aquí

    logger.info("Cerrando TurnoMoto backend...")
    await indicators.stop()
    await nfc_service.stop()


# ----- App -----
app = FastAPI(
    title="TurnoMoto API",
    description=f"Backend del kiosko de agendamiento - {settings.concesionario_nombre}",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(gerencia_router)


# ===== Endpoints REST =====

@app.get("/")
async def root():
    """Información básica del servicio."""
    return {
        "service": "TurnoMoto API",
        "version": "0.1.0",
        "concesionario": settings.concesionario_nombre,
        "status": "online",
    }


@app.get("/health")
async def health():
    """Healthcheck. Útil para monitoreo y para scripts systemd."""
    return {
        "status": "ok",
        "websocket_clients": len(manager.active_connections),
    }


@app.get("/clientes/{cliente_id}", response_model=ClienteOut)
async def get_cliente(cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return cliente


@app.get("/servicios", response_model=list[ServicioOut])
async def listar_servicios(db: Session = Depends(get_db)):
    """Catálogo de servicios activos del taller."""
    return db.query(Servicio).filter(Servicio.activo.is_(True)).all()


@app.post("/citas", response_model=CitaOut, status_code=201)
async def crear_cita(cita: CitaCreate, db: Session = Depends(get_db)):
    """Crea una nueva cita en el taller."""
    # Reject appointments in the past
    from datetime import datetime as _dt
    if cita.fecha_hora < _dt.now():
        raise HTTPException(
            status_code=400,
            detail="No se puede agendar una cita en una fecha/hora pasada. Por favor seleccione otro horario."
        )

    # Check double-booking: another active appointment at the same datetime
    existing = (
        db.query(Cita)
        .filter(Cita.fecha_hora == cita.fecha_hora)
        .filter(Cita.estado.in_(["pendiente", "confirmada", "en_proceso"]))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Ese horario ya fue tomado por otro cliente. Por favor seleccione otro."
        )

    nueva = Cita(**cita.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    logger.info(f"Cita creada: id={nueva.id} cliente={nueva.cliente_id}")

    # Broadcast al canal del taller
    await taller_manager.broadcast({
        "event_type": "cita_creada",
        "cita_id": nueva.id,
        "fecha_hora": nueva.fecha_hora.isoformat(),
    })
    asyncio.create_task(indicators.on_cita_creada())

    return nueva



@app.post("/clientes", response_model=ClienteOut, status_code=201)
async def crear_cliente(data: ClienteCreate, db: Session = Depends(get_db)):
    """Registra un nuevo cliente con su moto inicial y opcionalmente asocia un llavero NFC.
    Todo en una transacción atómica."""

    if db.query(Cliente).filter(Cliente.documento == data.documento).first():
        raise HTTPException(status_code=409, detail=f"Ya existe un cliente con documento {data.documento}")

    if db.query(Moto).filter(Moto.placa == data.moto.placa).first():
        raise HTTPException(status_code=409, detail=f"La placa {data.moto.placa} ya está registrada")

    if data.nfc_uid and db.query(NfcTag).filter(NfcTag.uid == data.nfc_uid).first():
        raise HTTPException(status_code=409, detail="Ese llavero ya está asociado a otro cliente")

    cliente = Cliente(
        nombre=data.nombre,
        documento=data.documento,
        telefono=data.telefono,
        email=data.email,
    )
    db.add(cliente)
    db.flush()

    moto = Moto(
        cliente_id=cliente.id,
        placa=data.moto.placa,
        marca=data.moto.marca,
        modelo=data.moto.modelo,
        anio=data.moto.anio,
        color=data.moto.color,
        kilometraje=data.moto.kilometraje,
    )
    db.add(moto)

    if data.nfc_uid:
        tag = NfcTag(uid=data.nfc_uid, cliente_id=cliente.id)
        db.add(tag)

    db.commit()
    db.refresh(cliente)
    logger.info(f"Cliente creado: id={cliente.id} doc={cliente.documento} placa={data.moto.placa} uid={data.nfc_uid}")
    return cliente



@app.get("/clientes/{cliente_id}/citas")
async def listar_citas_cliente(cliente_id: int, db: Session = Depends(get_db)):
    """Lista TODAS las citas del cliente (para separar Próximas/Historial en el frontend),
    enriquecidas con el nombre del servicio. Antes de listar, marca como no_asistio
    cualquier cita vencida (trazabilidad). Ordena por fecha_hora descendente."""
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    marcar_vencidas(db)

    citas = (
        db.query(Cita)
        .options(joinedload(Cita.servicio), joinedload(Cita.moto))
        .filter(Cita.cliente_id == cliente_id)
        .order_by(Cita.fecha_hora.desc())
        .all()
    )

    return [
        {
            "id": c.id,
            "turno": f"T-{c.id:03d}",
            "fecha_hora": c.fecha_hora.isoformat(),
            "estado": c.estado,
            "notas": c.notas,
            "servicio": {
                "id": c.servicio.id,
                "nombre": c.servicio.nombre,
            },
            "moto": {
                "id": c.moto.id,
                "placa": c.moto.placa,
                "modelo": c.moto.modelo,
            },
        }
        for c in citas
    ]



@app.post("/citas/{cita_id}/cancelar", response_model=CitaOut)
async def cancelar_cita(cita_id: int, db: Session = Depends(get_db)):
    """Cancela una cita. Solo permitido si está en estado pendiente o confirmada.
    Citas en_proceso o completadas no pueden cancelarse desde el kiosko."""
    cita = db.query(Cita).filter(Cita.id == cita_id).first()
    if not cita:
        raise HTTPException(status_code=404, detail="Cita no encontrada")

    if cita.estado == "cancelada":
        raise HTTPException(status_code=409, detail="Esta cita ya fue cancelada previamente")

    if cita.estado == "completada":
        raise HTTPException(status_code=409, detail="No se puede cancelar una cita que ya fue completada")

    if cita.estado == "en_proceso":
        raise HTTPException(
            status_code=409,
            detail="Esta cita ya está siendo atendida por el taller. No se puede cancelar."
        )

    cita.estado = "cancelada"
    db.commit()
    db.refresh(cita)
    logger.info(f"Cita cancelada: id={cita.id} cliente_id={cita.cliente_id}")

    # Broadcast al canal del taller
    await taller_manager.broadcast({
        "event_type": "cita_cancelada",
        "cita_id": cita.id,
    })
    asyncio.create_task(indicators.on_cita_cancelada())

    return cita



@app.patch("/clientes/{cliente_id}", response_model=ClienteOut)
async def actualizar_cliente(cliente_id: int, data: ClienteUpdate, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(cliente, field, value)
    db.commit()
    db.refresh(cliente)
    logger.info(f"Cliente actualizado: id={cliente.id} fields={list(updates.keys())}")
    return cliente



@app.patch("/motos/{moto_id}", response_model=MotoOut)
async def actualizar_moto(moto_id: int, data: MotoUpdate, db: Session = Depends(get_db)):
    moto = db.query(Moto).filter(Moto.id == moto_id).first()
    if not moto:
        raise HTTPException(status_code=404, detail="Moto no encontrada")
    updates = data.model_dump(exclude_unset=True)
    if "placa" in updates and updates["placa"] != moto.placa:
        existing = db.query(Moto).filter(Moto.placa == updates["placa"]).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"La placa {updates['placa']} ya está registrada")
    for field, value in updates.items():
        setattr(moto, field, value)
    db.commit()
    db.refresh(moto)
    logger.info(f"Moto actualizada: id={moto.id} fields={list(updates.keys())}")
    return moto



@app.get("/citas/ocupados")
async def listar_horarios_ocupados(fecha: str, db: Session = Depends(get_db)):
    """Lista los horarios ocupados de una fecha específica.
    Retorna array de strings "HH:MM" — solo los que están en estado activo
    (pendiente, confirmada, en_proceso). Las canceladas y completadas no bloquean el slot."""
    from datetime import datetime
    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Use YYYY-MM-DD")

    inicio = fecha_dt.replace(hour=0, minute=0, second=0)
    fin = fecha_dt.replace(hour=23, minute=59, second=59)

    citas = (
        db.query(Cita)
        .filter(Cita.fecha_hora >= inicio)
        .filter(Cita.fecha_hora <= fin)
        .filter(Cita.estado.in_(["pendiente", "confirmada", "en_proceso"]))
        .all()
    )

    horarios = sorted({c.fecha_hora.strftime("%H:%M") for c in citas})
    return {"fecha": fecha, "ocupados": horarios}



@app.get("/citas")
async def listar_citas_del_dia(fecha: str, db: Session = Depends(get_db)):
    """Lista las citas de una fecha con datos enriquecidos (cliente, moto, servicio).
    Usado por la pantalla del mecánico para ver la agenda del día."""
    from datetime import datetime
    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Use YYYY-MM-DD")

    inicio = fecha_dt.replace(hour=0, minute=0, second=0)
    fin = fecha_dt.replace(hour=23, minute=59, second=59)

    marcar_vencidas(db)

    citas = (
        db.query(Cita)
        .options(joinedload(Cita.cliente), joinedload(Cita.moto), joinedload(Cita.servicio))
        .filter(Cita.fecha_hora >= inicio)
        .filter(Cita.fecha_hora <= fin)
        .order_by(Cita.fecha_hora.asc())
        .all()
    )

    return [
        {
            "id": c.id,
            "turno": f"T-{c.id:03d}",
            "fecha_hora": c.fecha_hora.isoformat(),
            "hora": c.fecha_hora.strftime("%H:%M"),
            "estado": c.estado,
            "notas": c.notas,
            "cliente": {
                "id": c.cliente.id,
                "nombre": c.cliente.nombre,
                "telefono": c.cliente.telefono,
            },
            "moto": {
                "id": c.moto.id,
                "placa": c.moto.placa,
                "marca": c.moto.marca,
                "modelo": c.moto.modelo,
            },
            "servicio": {
                "id": c.servicio.id,
                "nombre": c.servicio.nombre,
                "duracion_minutos": c.servicio.duracion_minutos,
            },
        }
        for c in citas
    ]



# Transiciones válidas de estado
VALID_TRANSITIONS = {
    "pendiente": {"confirmada", "en_proceso", "cancelada", "no_asistio"},
    "confirmada": {"en_proceso", "cancelada", "no_asistio"},
    "en_proceso": {"completada"},
    "completada": set(),
    "cancelada": set(),
    # no_asistio no es terminal: el mecánico puede corregir si el cliente sí vino
    "no_asistio": {"en_proceso", "completada"},
}

# Estados que "liberan" el slot y cuentan como activos/futuros
ESTADOS_ACTIVOS = ("pendiente", "confirmada", "en_proceso")


def marcar_vencidas(db: Session) -> None:
    """Actualización perezosa: cita pendiente/confirmada cuya fecha_hora ya pasó
    se persiste como no_asistio. Se llama al consultar citas. Evita un cron
    en un appliance de un solo equipo — el cálculo y la BD convergen al leer."""
    from datetime import datetime as _dt
    ahora = _dt.now()
    vencidas = (
        db.query(Cita)
        .filter(Cita.fecha_hora < ahora)
        .filter(Cita.estado.in_(["pendiente", "confirmada"]))
        .all()
    )
    if vencidas:
        for c in vencidas:
            c.estado = "no_asistio"
            logger.info(f"Cita vencida marcada no_asistio: id={c.id} fecha={c.fecha_hora.isoformat()}")
        db.commit()


@app.patch("/citas/{cita_id}/estado", response_model=CitaOut)
async def actualizar_estado_cita(
    cita_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    _session: dict = Depends(require_role("mecanico", "gerencia")),
):
    """Avanza el estado de una cita. Solo permite transiciones válidas.
    Requiere sesión admin válida (mecánico o gerencia) — protegido en backend
    como defensa en profundidad, independiente del guard del frontend.
    payload: {"estado": "en_proceso"} (uno de los estados permitidos)."""
    nuevo_estado = payload.get("estado")
    if not nuevo_estado:
        raise HTTPException(status_code=400, detail="Falta el campo 'estado' en el payload")

    cita = db.query(Cita).filter(Cita.id == cita_id).first()
    if not cita:
        raise HTTPException(status_code=404, detail="Cita no encontrada")

    estado_actual = cita.estado
    if nuevo_estado not in VALID_TRANSITIONS.get(estado_actual, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Transición inválida: no se puede pasar de '{estado_actual}' a '{nuevo_estado}'"
        )

    cita.estado = nuevo_estado
    db.commit()
    db.refresh(cita)
    logger.info(f"Cita estado actualizado: id={cita.id} {estado_actual} -> {nuevo_estado}")

    # Broadcast al canal del taller
    await taller_manager.broadcast({
        "event_type": "cita_estado_cambiado",
        "cita_id": cita.id,
        "estado_anterior": estado_actual,
        "estado_nuevo": nuevo_estado,
    })

    return cita


# ===== WebSocket para eventos NFC en tiempo real =====

@app.websocket("/ws/nfc")
async def websocket_nfc(websocket: WebSocket):
    """Stream de eventos NFC hacia el frontend.

    El frontend se conecta a ws://<host>:8000/ws/nfc y recibe un mensaje JSON
    cada vez que se detecta un tag, con el formato:

        {
            "event_type": "nfc_read",
            "timestamp": "2026-06-17T14:23:45.123456+00:00",
            "uid": "04A1B2C3D4E5F6",
            "cliente": { ... } | null,
            "lectura_id": 42
        }

    El cliente puede enviar "ping" para verificar que la conexión sigue viva.
    """
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            # Aquí se podrían procesar otros mensajes del cliente en el futuro
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        manager.disconnect(websocket)


@app.websocket("/ws/taller")
async def websocket_taller(websocket: WebSocket):
    """Stream de eventos del taller hacia la pantalla del mecánico.
    Eventos emitidos: cita_creada, cita_estado_cambiado, cita_cancelada."""
    await taller_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        taller_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket taller error: {e}")
        taller_manager.disconnect(websocket)
