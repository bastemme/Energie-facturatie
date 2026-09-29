import os

os.environ.setdefault("ER_ENVIRONMENT", "test")
os.environ.setdefault("ER_DATABASE_URL", "sqlite://")
# Run the suite against PostgreSQL with: ER_TEST_DATABASE_URL=postgresql+psycopg://... pytest
TEST_DB_URL = os.environ.get("ER_TEST_DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from app import db as db_module  # noqa: E402
from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ER_STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ER_DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db():
    db_module.init_engine(TEST_DB_URL)
    db_module.Base.metadata.drop_all(db_module.get_engine())
    db_module.create_all()
    session = db_module.session_factory()()
    try:
        yield session
    finally:
        session.close()
        db_module.Base.metadata.drop_all(db_module.get_engine())


@pytest.fixture
def client_factory(db):
    from decimal import Decimal

    from app.models import Client

    def make(name="Testbedrijf B.V.", fee="15"):
        c = Client(company_name=name, success_fee_percentage=Decimal(fee), consent_given=True)
        db.add(c)
        db.flush()
        return c

    return make
