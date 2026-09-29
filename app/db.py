"""Database engine and session management."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # enforce FK constraints on SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    settings = get_settings()
    url = url or settings.database_url
    if url.startswith("sqlite:///./"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    _engine = make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def create_all() -> None:
    import app.models  # noqa: F401  (register models)

    engine = get_engine()
    Base.metadata.create_all(engine)
    if add_missing_columns(engine):
        _backfill_defaults(engine)


def _backfill_defaults(engine: Engine) -> None:
    """Give rows that predate a new column its default value (dialect-neutral Core updates)."""
    from sqlalchemy import update

    from app.domain.enums import LeadStage
    from app.models import Prospect

    table = Prospect.__table__
    with engine.begin() as conn:
        conn.execute(update(table).where(table.c.stage.is_(None)).values(stage=LeadStage.NEW.value))
        conn.execute(update(table).where(table.c.do_not_contact.is_(None)).values(do_not_contact=False))


def add_missing_columns(engine: Engine) -> list[str]:
    """Additive schema upgrade: add new nullable columns to existing tables.

    Keeps existing (demo) databases working when a model gains a column. It never drops, renames or changes
    columns; anything beyond adding a nullable column needs a real migration (see docs/operations.md).
    """
    from sqlalchemy import inspect, text

    added = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present or not column.nullable or column.primary_key:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                # names come from our own metadata, never from user input
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))  # noqa: S608
                added.append(f"{table.name}.{column.name}")
    return added


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
