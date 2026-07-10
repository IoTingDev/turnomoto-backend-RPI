"""Endpoints de Business Intelligence para gerencia.

Un solo endpoint (/gerencia/resumen) devuelve el snapshot completo del dashboard:
KPIs con delta vs periodo anterior, citas por día por estado, estados agregados
y top de servicios. La lógica pura vive en construir_resumen() para ser testeable
sin HTTP ni auth.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Cita, Cliente, Servicio
from ..auth import require_role

router = APIRouter(prefix="/gerencia", tags=["gerencia"])

ESTADOS_TODOS = ("completada", "pendiente", "confirmada", "en_proceso", "cancelada", "no_asistio")


def _parse(fecha_str, default):
    if not fecha_str:
        return default
    try:
        return datetime.fromisoformat(fecha_str)
    except ValueError:
        return datetime.strptime(fecha_str, "%Y-%m-%d")


def _conteos_por_estado(db, d0, d1):
    rows = db.execute(
        select(Cita.estado, func.count())
        .where(Cita.fecha_hora >= d0, Cita.fecha_hora <= d1)
        .group_by(Cita.estado)
    ).all()
    return {estado: n for estado, n in rows}


def _cumplimiento(c):
    comp, na = c.get("completada", 0), c.get("no_asistio", 0)
    base = comp + na
    return round(comp / base * 100) if base else None


def _ausentismo(c):
    comp, na = c.get("completada", 0), c.get("no_asistio", 0)
    base = comp + na
    return round(na / base * 100) if base else None


def construir_resumen(db: Session, desde: datetime, hasta: datetime) -> dict:
    dias = (hasta.date() - desde.date()).days + 1
    desde_prev = desde - timedelta(days=dias)
    hasta_prev = desde - timedelta(seconds=1)

    cur = _conteos_por_estado(db, desde, hasta)
    prev = _conteos_por_estado(db, desde_prev, hasta_prev)
    total_cur, total_prev = sum(cur.values()), sum(prev.values())

    cump_cur, cump_prev = _cumplimiento(cur), _cumplimiento(prev)
    aus_cur, aus_prev = _ausentismo(cur), _ausentismo(prev)

    def d_pct(c, p):
        return round((c - p) / p * 100) if p else None

    def d_pts(c, p):
        return (c - p) if (c is not None and p is not None) else None

    ids_cur = db.execute(
        select(Cita.cliente_id).where(Cita.fecha_hora >= desde, Cita.fecha_hora <= hasta).distinct()
    ).scalars().all()
    total_clientes = len(ids_cur)
    nuevos = 0
    if ids_cur:
        nuevos = db.execute(
            select(func.count()).select_from(Cliente).where(
                Cliente.id.in_(ids_cur),
                Cliente.fecha_registro >= desde,
                Cliente.fecha_registro <= hasta,
            )
        ).scalar_one()
    recurrentes = total_clientes - nuevos

    rows = db.execute(
        select(func.date(Cita.fecha_hora), Cita.estado, func.count())
        .where(Cita.fecha_hora >= desde, Cita.fecha_hora <= hasta)
        .group_by(func.date(Cita.fecha_hora), Cita.estado)
        .order_by(func.date(Cita.fecha_hora))
    ).all()
    por_dia = {}
    for fecha, estado, n in rows:
        d = por_dia.setdefault(fecha, {"fecha": fecha, "completada": 0, "pendiente": 0, "cancelada": 0, "no_asistio": 0})
        if estado in ("pendiente", "confirmada", "en_proceso"):
            d["pendiente"] += n
        elif estado in d:
            d[estado] += n

    top = db.execute(
        select(Servicio.nombre, func.count(Cita.id))
        .join(Cita, Cita.servicio_id == Servicio.id)
        .where(Cita.fecha_hora >= desde, Cita.fecha_hora <= hasta)
        .group_by(Servicio.nombre)
        .order_by(func.count(Cita.id).desc())
        .limit(6)
    ).all()

    return {
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "dias": dias},
        "kpis": {
            "citas_total": {"valor": total_cur, "delta_pct": d_pct(total_cur, total_prev)},
            "cumplimiento_pct": {"valor": cump_cur, "delta_pts": d_pts(cump_cur, cump_prev)},
            "ausentismo_pct": {"valor": aus_cur, "delta_pts": d_pts(aus_cur, aus_prev)},
            "clientes": {"total": total_clientes, "nuevos": nuevos, "recurrentes": recurrentes},
        },
        "citas_por_dia": list(por_dia.values()),
        "estados": {e: cur.get(e, 0) for e in ESTADOS_TODOS if cur.get(e, 0)},
        "top_servicios": [{"nombre": nombre, "total": n} for nombre, n in top],
    }


@router.get("/resumen")
def resumen_gerencia(
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    db: Session = Depends(get_db),
    _session: dict = Depends(require_role("gerencia")),
):
    from ..main import marcar_vencidas
    marcar_vencidas(db)
    hoy = datetime.now()
    ini_mes = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return construir_resumen(db, _parse(desde, ini_mes), _parse(hasta, hoy))
