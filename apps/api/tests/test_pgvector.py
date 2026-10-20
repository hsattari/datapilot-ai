"""PostgreSQL-only: proves the pgvector path (not the portable fallback) is what runs.
Run with TEST_DATABASE_URL=postgresql+psycopg2://... ; skipped on SQLite."""
import os

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.services import rag
from app.services.embeddings import embed_texts

from .conftest import register

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
                                reason="needs PostgreSQL + pgvector")


def add(client, h, title, body):
    assert client.post("/api/v1/knowledge/documents", json={"title": title, "text": body}, headers=h).status_code == 201


def test_vector_column_populated_and_hnsw_index_present(client):
    h, _ = register(client, "V1", "v1@v.test")
    add(client, h, "Doc", "Gross profit is net sales minus cost of goods sold.\n\nRefunds are negative sales.")
    with SessionLocal() as db:
        n, filled = db.execute(text("SELECT count(*), count(embedding_vec) FROM document_chunks")).one()
        assert n >= 1 and n == filled  # every chunk also stored as a pgvector value
        idx = db.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_document_chunks_embedding_hnsw'")).scalar()
        assert idx and "hnsw" in idx and "vector_cosine_ops" in idx


def test_sql_vector_ranking_is_tenant_scoped(client):
    h1, t1 = register(client, "A", "a@v.test")
    h2, t2 = register(client, "B", "b@v.test")
    add(client, h1, "Safety", "Forklift drivers wear high visibility vests in the warehouse.")
    add(client, h1, "Finance", "Gross profit equals net sales minus cost of goods sold.")
    add(client, h2, "Other tenant", "Gross profit equals net sales minus cost of goods sold at tenant B.")
    qv = embed_texts(["how is gross profit calculated"])[0][0]
    vec = "[" + ",".join(f"{x:.6f}" for x in qv) + "]"
    with SessionLocal() as db:
        rows = db.execute(text("SELECT c.tenant_id, d.title FROM document_chunks c JOIN documents d ON d.id=c.document_id "
                               "WHERE c.tenant_id=:t ORDER BY c.embedding_vec <=> CAST(:q AS vector) LIMIT 5"),
                          {"t": t1, "q": vec}).all()
        assert {r[0] for r in rows} == {t1} and rows[0][1] == "Finance"
        # and the real search() goes through that same SQL path for PostgreSQL
        assert rag._is_pg(db)
        hits = rag.search(db, t1, "how is gross profit calculated")
        assert hits and hits[0]["title"] == "Finance" and all(h["title"] != "Other tenant" for h in hits)


def test_delete_cascades_vectors(client):
    h, _ = register(client, "D", "d@v.test")
    add(client, h, "Tmp", "Temporary note about targets.")
    doc = client.get("/api/v1/knowledge/documents", headers=h).json()[0]["id"]
    assert client.delete(f"/api/v1/knowledge/documents/{doc}", headers=h).status_code == 204
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM document_chunks")).scalar() == 0
