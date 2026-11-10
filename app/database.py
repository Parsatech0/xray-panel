"""SQLAlchemy database engine and session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


# `check_same_thread=False` is required for SQLite with FastAPI's async-ish
# request handling in a single thread. `future=True` enables 2.0 style.
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Yield a database session and close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables defined on `Base`."""
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_columns()


def _migrate_sqlite_columns():
    """Add columns introduced after the first release to existing databases.

    `create_all` only creates missing tables, never missing columns, so this
    inspects the SQLite schema and ALTERs old databases. Idempotent — safe to
    run on every startup.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    client_columns = {c["name"] for c in inspector.get_columns("clients")}
    wanted = {
        # (column, DDL) — Phase 4/6 additions
        "disabled_reason": "VARCHAR(16) DEFAULT '' NOT NULL",
        "sub_token": "VARCHAR(48) DEFAULT ''",
    }
    missing = {col: ddl for col, ddl in wanted.items() if col not in client_columns}
    if not missing:
        return
    with engine.connect() as conn:
        for col, ddl in missing.items():
            conn.execute(text(f"ALTER TABLE clients ADD COLUMN {col} {ddl}"))
        conn.commit()
