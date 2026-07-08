"""Setup del engine SQLAlchemy y sesión. SQLite con WAL + foreign keys habilitadas."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """Base class para todos los modelos ORM."""
    pass


# check_same_thread=False permite que múltiples threads de FastAPI compartan la conexión.
# La concurrencia real se controla con SessionLocal por request.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Habilita foreign keys y WAL mode en cada nueva conexión SQLite.

    - foreign_keys=ON: SQLite las trae apagadas por default, increíble pero cierto.
    - journal_mode=WAL: permite lecturas concurrentes mientras se escribe, y es más
      resiliente a cortes de luz que el modo journal por default.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")  # buen balance durabilidad/rendimiento
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency injection para FastAPI: provee una sesión por request y la cierra al final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
