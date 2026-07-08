"""
Inicialización de la base de datos con datos seed.

Crea las tablas (si no existen) e inserta datos de prueba:
    - Catálogo de servicios típicos de un concesionario Suzuki
    - Un cliente de prueba
    - Un llavero NFC asociado (usando uno de los UIDs detectados en tu test inicial)
    - Una moto del cliente

Ejecutar (desde ~/turnomoto/backend):
    source ~/turnomoto-env/bin/activate
    python -m scripts.init_db
"""
import sys
from pathlib import Path

# Permite importar `app.*` corriendo este script desde backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import engine, SessionLocal, Base
from app.models import Cliente, NfcTag, Moto, Servicio


def init():
    print("=" * 50)
    print("TurnoMoto - Inicialización de base de datos")
    print("=" * 50)

    print("\n[1/2] Creando tablas...")
    Base.metadata.create_all(bind=engine)
    print("      OK Tablas creadas/verificadas")

    db = SessionLocal()
    try:
        if db.query(Cliente).count() > 0:
            print("\n[2/2] Ya hay datos en la BD. Saltando seed.")
            print("      (Si quieres re-seedear, borra primero data/turnomoto.db)")
            return

        print("\n[2/2] Insertando datos seed...")

        # --- Catálogo de servicios ---
        servicios = [
            Servicio(
                nombre="Cambio de aceite y filtro",
                descripcion="Aceite sintético + filtro original Suzuki",
                duracion_minutos=45,
            ),
            Servicio(
                nombre="Revisión 10.000 km",
                descripcion="Mantenimiento preventivo programado",
                duracion_minutos=120,
            ),
            Servicio(
                nombre="Revisión 20.000 km",
                descripcion="Mantenimiento mayor",
                duracion_minutos=180,
            ),
            Servicio(
                nombre="Cambio de pastillas de freno",
                duracion_minutos=60,
            ),
            Servicio(
                nombre="Ajuste de cadena",
                duracion_minutos=30,
            ),
            Servicio(
                nombre="Diagnóstico general",
                descripcion="Inspección de seguridad y diagnóstico",
                duracion_minutos=45,
            ),
        ]
        db.add_all(servicios)
        print(f"      OK {len(servicios)} servicios insertados")

        # --- Cliente de prueba ---
        cliente = Cliente(
            nombre="Juan Pérez Ramírez",
            documento="1144567890",
            telefono="3001234567",
            email="juan.perez@example.com",
        )
        db.add(cliente)
        db.flush()  # forzar asignación de id

        # --- Llavero NFC asociado ---
        # Usa uno de los UIDs reales que detectaste en la prueba inicial.
        # Cámbialo si quieres usar otro de tus tags.
        tag = NfcTag(uid="1D196863640000", cliente_id=cliente.id)
        db.add(tag)

        # --- Moto del cliente ---
        moto = Moto(
            cliente_id=cliente.id,
            placa="ABC12D",
            modelo="GSX-R150",
            anio=2024,
            color="Negro",
            kilometraje=8500,
        )
        db.add(moto)

        db.commit()

        print("      OK Cliente de prueba creado")
        print()
        print("-" * 50)
        print(f"  Cliente:  {cliente.nombre} (id={cliente.id})")
        print(f"  Doc:      {cliente.documento}")
        print(f"  UID:      {tag.uid}")
        print(f"  Moto:     {moto.modelo} placa {moto.placa}")
        print("-" * 50)
        print("\nSeed completado. La BD está lista.")
    finally:
        db.close()


if __name__ == "__main__":
    init()
