from datetime import datetime, timedelta, timezone

import jwt
import bcrypt

from .config import get_settings

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode()[:72], hashed.encode())


def create_token(user_id: str, tenant_id: str | None = None) -> str:
    """`tenant_id` (the chosen workspace) is only a preference: membership is re-checked on every request."""
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.jwt_ttl_minutes)
    claims = {"sub": user_id, "exp": exp}
    if tenant_id:
        claims["tid"] = tenant_id
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> str:
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    return payload["sub"]


def decode_claims(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
