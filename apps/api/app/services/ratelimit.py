"""In-process sliding-window rate limiter.

Good for a single API process (development, the Starter profile). With several replicas each process counts
separately, so production should ALSO enforce limits at the gateway (Azure Front Door / API Management) or swap
this store for Redis. Keys are the caller's IP for unauthenticated endpoints and the user id for authenticated ones.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from ..config import get_settings

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def reset() -> None:
    with _lock:
        _hits.clear()


def check(key: str, limit: int, window: float = 60.0) -> float:
    """Record a hit; returns 0 if allowed, otherwise the seconds to wait."""
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return max(1.0, q[0] + window - now)
        q.append(now)
    return 0.0


def client_ip(request: Request) -> str:
    s = get_settings()
    if s.trust_proxy_headers:  # only behind a proxy you control; otherwise the header is attacker-supplied
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce(bucket: str, key: str, limit: int):
    s = get_settings()
    if not s.rate_limit_enabled or limit <= 0:
        return
    wait = check(f"{bucket}:{key}", limit)
    if wait:
        raise HTTPException(429, "Too many requests. Please slow down and try again shortly.",
                            headers={"Retry-After": str(int(wait) + 1)})


def by_ip(bucket: str, setting: str):
    def dep(request: Request):
        _enforce(bucket, client_ip(request), getattr(get_settings(), setting))
    return Depends(dep)


def by_user(bucket: str, setting: str):
    from ..deps import Principal, get_principal  # local import: deps is loaded after config

    def dep(p: Principal = Depends(get_principal)):
        _enforce(bucket, p.user_id, getattr(get_settings(), setting))
    return Depends(dep)
