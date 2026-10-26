import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="dp-test-")
# Set TEST_DATABASE_URL=postgresql+psycopg2://... to run the whole suite on a real PostgreSQL (+pgvector);
# the schema is then built from the Alembic migrations so migration-only objects (pgvector) are exercised.
_PG = os.environ.get("TEST_DATABASE_URL", "")
os.environ.update(
    DATABASE_URL=_PG or "sqlite:///:memory:",
    LOCAL_STORAGE_PATH=f"{_tmp}/raw",
    ANALYTICS_PATH=f"{_tmp}/analytics",
    JWT_SECRET="test-secret",
    AUTO_MODEL_DEFAULT="false",   # existing tests exercise the manual approval path; test_automodel.py turns it on
    AZURE_OPENAI_ENDPOINT="", AZURE_OPENAI_API_KEY="", AZURE_OPENAI_DEPLOYMENT="",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.ingestion import run_pending  # noqa: E402

DATA = Path(__file__).resolve().parents[3] / "data" / "synthetic"


def _reset_schema():
    if _PG:
        from alembic import command
        from alembic.config import Config

        with engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        command.upgrade(cfg, "head")
    else:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


@pytest.fixture()
def client():
    from app.services import ratelimit
    ratelimit.reset()  # rate-limit counters are per process: start every test clean
    _reset_schema()
    return TestClient(app)


def register(client, org, email):
    r = client.post("/api/v1/tenants", json={"organisation": org, "email": email, "password": "correct-horse-1"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["tenant_id"]


def ingest_and_model(client, headers, csv_name="orders_reference.csv", data=None):
    sid = client.post("/api/v1/data-sources", json={"name": "Sales"}, headers=headers).json()["id"]
    content = data if data is not None else (DATA / csv_name).read_bytes()
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=headers,
                    files={"file": (csv_name, content, "text/csv")})
    assert r.status_code == 202, r.text
    with SessionLocal() as db:
        run_pending(db)
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}", headers=headers).json()
    assert job["status"] == "succeeded", job
    ds = client.get("/api/v1/datasets", headers=headers).json()[0]
    prof = client.get(f"/api/v1/datasets/{ds['id']}/profile", headers=headers).json()["profile"]
    mapping = {k: v["column"] for k, v in prof["suggested_mapping"].items()}
    return ds["id"], mapping
