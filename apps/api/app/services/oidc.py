"""Verification of OpenID Connect ID tokens (e.g. Microsoft Entra ID).

Only asymmetric RS256 is accepted (never `none`, never HMAC, which prevents algorithm-confusion attacks). Signature,
issuer, audience, expiry and required claims are all checked; keys come from the provider's JWKS (cached, refreshed
once when an unknown key id appears, which is how key rotation shows up).
"""
import threading
import time

import httpx
import jwt

from ..config import get_settings

ALLOWED_ALGS = ["RS256"]
JWKS_TTL = 3600.0
MIN_REFRESH = 60.0
_lock = threading.Lock()
_cache: dict = {"keys": None, "at": 0.0}


class OIDCError(ValueError):
    pass


def enabled() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.oidc_client_id)


def fetch_jwks() -> dict:
    """Download the provider's signing keys (network call; replaced in tests)."""
    s = get_settings()
    with httpx.Client(timeout=5.0) as c:
        uri = s.oidc_jwks_uri
        if not uri:
            doc = c.get(s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")
            doc.raise_for_status()
            uri = doc.json()["jwks_uri"]
        r = c.get(uri)
        r.raise_for_status()
        return r.json()


def reset_cache() -> None:
    with _lock:
        _cache.update(keys=None, at=0.0)


def _keys(force: bool = False) -> dict:
    now = time.monotonic()
    with _lock:
        fresh = _cache["keys"] is not None and now - _cache["at"] < JWKS_TTL
        if fresh and not (force and now - _cache["at"] >= MIN_REFRESH):
            return _cache["keys"]
    try:
        jwks = fetch_jwks()
    except Exception as e:
        raise OIDCError("Could not load the identity provider's signing keys") from e
    keys = {k["kid"]: k for k in jwks.get("keys", []) if k.get("kid") and k.get("kty") == "RSA"}
    with _lock:
        _cache.update(keys=keys, at=now)
    return keys


def verify_id_token(token: str, nonce: str | None = None) -> dict:
    s = get_settings()
    if not enabled():
        raise OIDCError("Single sign-on is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise OIDCError("Invalid token")
    if header.get("alg") not in ALLOWED_ALGS or not header.get("kid"):
        raise OIDCError("Invalid token")
    jwk = _keys().get(header["kid"]) or _keys(force=True).get(header["kid"])
    if jwk is None:
        raise OIDCError("Invalid token")
    try:
        claims = jwt.decode(token, key=jwt.PyJWK(jwk).key, algorithms=ALLOWED_ALGS, audience=s.oidc_client_id,
                            issuer=s.oidc_issuer, leeway=30, options={"require": ["exp", "iss", "aud", "sub"]})
    except jwt.PyJWTError:
        raise OIDCError("Invalid token")
    if s.oidc_required_tid and claims.get("tid") != s.oidc_required_tid:
        raise OIDCError("Invalid token")
    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCError("Invalid token")
    return claims
