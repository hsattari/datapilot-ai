import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="dp-test-")
os.environ.update(
    DATABASE_URL="sqlite:///:memory:",
    LOCAL_STORAGE_PATH=f"{_tmp}/raw",
    ANALYTICS_PATH=f"{_tmp}/analytics",
    JWT_SECRET="test-secret",
    AZURE_OPENAI_ENDPOINT="", AZURE_OPENAI_API_KEY="", AZURE_OPENAI_DEPLOYMENT="",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.ingestion import run_pending  # noqa: E402

DATA = Path(__file__).resolve().parents[3] / "data" / "synthetic"


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
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
