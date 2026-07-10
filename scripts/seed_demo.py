"""Siembra datos de demostración realistas para el dashboard de gerencia.

- Preserva clientes con llavero NFC (demo en vivo) y les añade historial.
- Clientes demo marcados con email @demo.local (idempotente: re-ejecutable).
- Ausentismo realista 8-15%, estacionalidad L-S, tendencia creciente.
- Histórico ~6 semanas + unas pocas citas próximas (agenda del mecánico).

Uso:
    python scripts/seed_demo.py           # limpia citas + sub-siembra demo
    python scripts/seed_demo.py --clean   # solo limpia (vuelve a cero)
"""
import sys, os, argparse, random, string
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import SessionLocal
from app.models import Cliente, Moto, Servicio, Cita

random.seed(42)
HOY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
INICIO = HOY - timedelta(days=42)
FIN = HOY + timedelta(days=8)

NOMBRES = [
    "Carlos Andrés Gómez", "María Fernanda Ruiz", "Diego Alejandro Torres",
    "Luisa Cardona", "Andrés Felipe Marín", "Paola Restrepo", "Julián Ospina",
    "Natalia Vélez", "Sebastián Ramírez", "Daniela Quintero", "Mateo Herrera",
]
MODELOS = ["GN125", "Gixxer 150", "GSX-R150", "V-Strom 250", "DR200", "Best 125", "AX4 115", "EN125"]
HORAS = [8, 9, 10, 11, 12, 14, 15, 16]
PESO_HORA = [3, 3, 3, 2, 2, 1, 2, 1]
PESO_SERVICIO = {
    "aceite": 35, "10.000": 18, "20.000": 12, "pastillas": 15, "cadena": 12, "diagn": 8,
}


def peso_servicio(nombre):
    n = nombre.lower()
    for k, v in PESO_SERVICIO.items():
        if k in n:
            return v
    return 10


def gen_placa(usadas):
    while True:
        p = "".join(random.choices(string.ascii_uppercase, k=3)) + f"{random.randint(0,99):02d}" + random.choice(string.ascii_uppercase)
        if p not in usadas:
            usadas.add(p)
            return p


def gen_doc(usados):
    while True:
        d = str(random.randint(10_000_000, 1_099_999_999))
        if d not in usados:
            usados.add(d)
            return d


def limpiar(db):
    n_citas = db.query(Cita).delete()
    demo = db.query(Cliente).filter(Cliente.email.like("%@demo.local")).all()
    n_cli = len(demo)
    for c in demo:
        db.delete(c)  # cascade: motos + nfc_tags
    db.commit()
    print(f"  Limpieza: {n_citas} citas borradas, {n_cli} clientes demo eliminados")


def sembrar(db):
    servicios = db.query(Servicio).filter(Servicio.activo == True).all()
    if not servicios:
        print("  ERROR: no hay servicios en el catálogo."); return
    serv_pesos = [peso_servicio(s.nombre) for s in servicios]

    docs = {c.documento for c in db.query(Cliente).all()}
    placas = {m.placa for m in db.query(Moto).all()}

    demo_clientes = []
    for i, nombre in enumerate(NOMBRES):
        reg = (HOY - timedelta(days=random.randint(3, 8))) if i < 3 else (INICIO - timedelta(days=random.randint(0, 25)))
        c = Cliente(
            nombre=nombre, documento=gen_doc(docs),
            telefono=f"3{random.randint(0,29):02d}{random.randint(1000000,9999999)}",
            email=f"{nombre.split()[0].lower()}{i}@demo.local",
            fecha_registro=reg, activo=True,
        )
        db.add(c); db.flush()
        m = Moto(cliente_id=c.id, placa=gen_placa(placas), marca="Suzuki",
                 modelo=random.choice(MODELOS), anio=random.randint(2018, 2025),
                 color=random.choice(["Negro", "Rojo", "Azul", "Blanco"]),
                 kilometraje=random.randint(3000, 45000))
        db.add(m); db.flush()
        demo_clientes.append((c, m))

    protegidos = db.query(Cliente).filter(
        Cliente.nfc_tags.any(), ~Cliente.email.like("%@demo.local")
    ).all()
    pool = [(c, c.motos[0]) for c in protegidos if c.motos] + demo_clientes
    if not pool:
        print("  ERROR: no hay clientes con moto para sembrar."); return

    pesos_cli = [random.choice([1, 1, 1, 2, 3]) for _ in pool]

    total = 0
    dia = INICIO
    while dia <= FIN:
        wd = dia.weekday()  # 0=lun .. 6=dom
        if wd == 6:
            dia += timedelta(days=1); continue
        semana = (dia - INICIO).days // 7
        base = {0: 2, 1: 2, 2: 2, 3: 3, 4: 3, 5: 4}[wd]
        if dia > HOY:
            n = random.randint(0, 2)  # pocas próximas
        else:
            n = max(1, round(base * (1 + 0.06 * semana) * random.uniform(0.6, 1.2)))
        horas_dia = random.sample(list(zip(HORAS, PESO_HORA)), min(n, len(HORAS)))
        for hora, _ in horas_dia:
            cli, moto = random.choices(pool, weights=pesos_cli, k=1)[0]
            serv = random.choices(servicios, weights=serv_pesos, k=1)[0]
            fh = dia.replace(hour=hora)
            if fh.date() < HOY.date():
                r = random.random()
                estado = "no_asistio" if r < 0.11 else "cancelada" if r < 0.19 else "completada"
            elif fh.date() == HOY.date():
                estado = random.choice(["completada", "en_proceso", "pendiente", "completada"])
            else:
                estado = "pendiente"
            db.add(Cita(cliente_id=cli.id, moto_id=moto.id, servicio_id=serv.id,
                        fecha_hora=fh, estado=estado, fecha_creacion=fh - timedelta(days=random.randint(1, 5))))
            total += 1
        dia += timedelta(days=1)
    db.commit()
    print(f"  Sembradas {total} citas · {len(demo_clientes)} clientes demo · {len(protegidos)} clientes NFC con historial")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="solo limpiar, sin sembrar")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        print("═══ SEED DEMO TurnoMoto ═══")
        limpiar(db)
        if not args.clean:
            sembrar(db)
        print("\n═══ Verificación: resumen resultante (mes actual) ═══")
        from app.routers.gerencia import construir_resumen
        import json
        ini_mes = HOY.replace(day=1)
        r = construir_resumen(db, ini_mes, datetime.now())
        print(json.dumps(r["kpis"], indent=2, ensure_ascii=False))
        print("Estados:", r["estados"])
        print("Top:", [(s["nombre"], s["total"]) for s in r["top_servicios"]])
    finally:
        db.close()


if __name__ == "__main__":
    main()
