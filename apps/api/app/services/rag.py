"""Chunking, indexing and hybrid retrieval. Every query is filtered by tenant_id in code and SQL."""
import hashlib
import math
import re
from collections import Counter

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Document, DocumentChunk
from .embeddings import embed_texts, tokens

CHUNK_CHARS = 1200
OVERLAP = 150
MAX_CHUNKS_PER_DOC = 400
RRF_K = 60


def chunk_text(body: str) -> list[str]:
    """Paragraph-aware chunks with small overlap."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > CHUNK_CHARS:  # split oversized paragraphs
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - OVERLAP:]
        if len(cur) + len(p) + 2 <= CHUNK_CHARS:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            chunks.append(cur)
            cur = (cur[-OVERLAP:] + "\n\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks[:MAX_CHUNKS_PER_DOC]


def _is_pg(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def index_document(db: Session, tenant_id: str, user_id: str, title: str, body: str,
                   filename: str | None = None) -> Document:
    chunks = chunk_text(body)
    if not chunks:
        raise ValueError("Document has no text content")
    vectors, mode = embed_texts(chunks)  # may raise; nothing is stored in that case
    doc = Document(tenant_id=tenant_id, title=title[:300], filename=filename,
                   sha256=hashlib.sha256(body.encode()).hexdigest(), chunk_count=len(chunks),
                   embedding_mode=mode, created_by=user_id)
    db.add(doc)
    db.flush()
    for i, (c, v) in enumerate(zip(chunks, vectors)):
        row = DocumentChunk(tenant_id=tenant_id, document_id=doc.id, chunk_index=i, content=c, embedding=v)
        db.add(row)
        db.flush()
        if _is_pg(db):
            db.execute(text("UPDATE document_chunks SET embedding_vec = CAST(:v AS vector) "
                            "WHERE id = :id AND tenant_id = :t"),
                       {"v": "[" + ",".join(f"{x:.6f}" for x in v) + "]", "id": row.id, "t": tenant_id})
    return doc


def delete_document(db: Session, tenant_id: str, doc_id: str) -> bool:
    doc = db.get(Document, doc_id)
    if doc is None or doc.tenant_id != tenant_id:
        return False
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id,
                                           DocumentChunk.tenant_id == tenant_id))
    db.delete(doc)
    return True


def _keyword_rank(query: str, rows: list[DocumentChunk]) -> list[str]:
    """BM25-style scoring over the tenant's chunks."""
    q = set(tokens(query))
    if not q or not rows:
        return []
    docs = {r.id: Counter(tokens(r.content)) for r in rows}
    n = len(rows)
    avg = sum(sum(c.values()) for c in docs.values()) / n or 1
    df = {t: sum(1 for c in docs.values() if t in c) for t in q}
    scores = {}
    for rid, c in docs.items():
        ln, s = sum(c.values()), 0.0
        for t in q:
            if c.get(t):
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * c[t] * 2.2 / (c[t] + 1.2 * (0.25 + 0.75 * ln / avg))
        if s > 0:
            scores[rid] = s
    return [k for k, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


def _vector_rank(db: Session, tenant_id: str, qvec: list[float], rows: list[DocumentChunk],
                 limit: int) -> list[str]:
    if _is_pg(db):  # pgvector: cosine distance, tenant filter in SQL
        res = db.execute(text(
            "SELECT id FROM document_chunks WHERE tenant_id = :t AND embedding_vec IS NOT NULL "
            "ORDER BY embedding_vec <=> CAST(:q AS vector) LIMIT :n"),
            {"t": tenant_id, "q": "[" + ",".join(f"{x:.6f}" for x in qvec) + "]", "n": limit})
        return [r[0] for r in res]
    if not rows:
        return []
    m = np.array([r.embedding for r in rows], dtype=float)
    q = np.array(qvec, dtype=float)
    sims = m @ q / ((np.linalg.norm(m, axis=1) * np.linalg.norm(q)) + 1e-12)
    order = np.argsort(-sims)[:limit]
    return [rows[i].id for i in order if sims[i] > 0]


def search(db: Session, tenant_id: str, query: str, k: int = 5) -> list[dict]:
    """Hybrid retrieval: keyword + vector, fused with reciprocal rank fusion. Tenant-scoped."""
    k = max(1, min(k, 20))
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.tenant_id == tenant_id))}
    if not docs:
        return []
    modes = {d.embedding_mode for d in docs.values()}
    current_mode = embed_texts(["x"])[1] if len(modes) else None
    rows = list(db.scalars(select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id)))
    # Only compare vectors produced by the same embedding mode as the query.
    vec_rows = [r for r in rows if docs[r.document_id].embedding_mode == current_mode]
    qvecs, _ = embed_texts([query])
    kw = _keyword_rank(query, rows)
    vec = _vector_rank(db, tenant_id, qvecs[0], vec_rows, 50)
    if _is_pg(db):
        ok = {r.id for r in vec_rows}
        vec = [i for i in vec if i in ok]
    fused: dict[str, float] = {}
    for ranking in (kw, vec):
        for pos, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)
    by_id = {r.id: r for r in rows}
    top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
    return [{"chunk_id": cid, "document_id": by_id[cid].document_id,
             "title": docs[by_id[cid].document_id].title, "chunk_index": by_id[cid].chunk_index,
             "content": by_id[cid].content, "score": round(sc, 5)} for cid, sc in top]
