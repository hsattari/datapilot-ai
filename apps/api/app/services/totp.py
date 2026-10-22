"""TOTP (RFC 6238) for authenticator apps, recovery codes, and encryption of the stored secret."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(n).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // 30)


def verify(secret: str, code: str, last_step: int | None = None, now: float | None = None) -> int | None:
    """Return the matched time step (to store, so a code cannot be replayed) or None. Allows +/-1 step of clock drift."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    cur = current_step(now)
    for step in (cur - 1, cur, cur + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            if last_step is not None and step <= last_step:
                return None  # already used
            return step
    return None


def otpauth_uri(secret: str, email: str, issuer: str = "DataPilot AI") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def new_recovery_codes(n: int = 8) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]


def hash_recovery(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def _fernet() -> Fernet:
    s = get_settings()
    material = (s.mfa_encryption_key or s.jwt_secret).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"datapilot-mfa|" + material).digest()))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
