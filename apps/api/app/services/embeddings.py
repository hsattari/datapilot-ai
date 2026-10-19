"""Embeddings: Azure OpenAI when configured, otherwise a deterministic local hashing fallback.

The fallback is lexical (hashed bag-of-words), NOT semantic. It exists so development and tests work
without Azure; a document's embedding_mode records which one produced its vectors, and the two are
never mixed in one search.
"""
import hashlib
import math
import re

from ..config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")


def azure_configured() -> bool:
    s = get_settings()
    return bool(s.azure_openai_endpoint and s.azure_openai_api_key and s.azure_openai_embedding_deployment)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _hash_embed(text: str, dims: int) -> list[float]:
    v = [0.0] * dims
    for t in tokens(text):
        h = int.from_bytes(hashlib.sha256(t.encode()).digest()[:8], "big")
        v[h % dims] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Return (vectors, mode). Raises on Azure failure rather than silently degrading."""
    s = get_settings()
    if not azure_configured():
        return [_hash_embed(t, s.embedding_dimensions) for t in texts], "local_hash"
    from openai import AzureOpenAI

    client = AzureOpenAI(azure_endpoint=s.azure_openai_endpoint, api_key=s.azure_openai_api_key,
                         api_version=s.azure_openai_api_version)
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):  # batch
        r = client.embeddings.create(model=s.azure_openai_embedding_deployment,
                                     input=texts[i:i + 64], dimensions=s.embedding_dimensions)
        out += [d.embedding for d in r.data]
    return out, "azure"
