"""Encryption of integration secrets at rest (Fernet, with key rotation).

`INTEGRATIONS_ENCRYPTION_KEYS` holds one or more Fernet keys, newest first: new
secrets are encrypted with the first, and any listed key can decrypt, so keys
can be rotated without downtime. In production a key is mandatory. Outside
production, a key is derived from JWT_SECRET so development works out of the box.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings
from app.core.exceptions import AppError


class EncryptionUnavailable(AppError):
    status_code = 503
    error_code = "encryption_unavailable"


@lru_cache
def _box(keys: tuple[str, ...], jwt_secret: str, production: bool) -> MultiFernet:
    if keys:
        try:
            return MultiFernet([Fernet(k.encode()) for k in keys])
        except (ValueError, TypeError) as exc:
            raise EncryptionUnavailable("INTEGRATIONS_ENCRYPTION_KEYS is not valid.") from exc
    if production:
        raise EncryptionUnavailable(
            "Integration secrets need INTEGRATIONS_ENCRYPTION_KEYS in production."
        )
    derived = hashlib.sha256(b"noblen-integrations:" + jwt_secret.encode()).digest()
    return MultiFernet([Fernet(base64.urlsafe_b64encode(derived))])


def _current() -> MultiFernet:
    return _box(
        tuple(settings.INTEGRATIONS_ENCRYPTION_KEYS), settings.JWT_SECRET, settings.is_production
    )


def encrypt(secret: dict[str, Any]) -> str:
    return _current().encrypt(json.dumps(secret, separators=(",", ":")).encode()).decode()


def decrypt(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return dict(json.loads(_current().decrypt(token.encode())))
    except InvalidToken as exc:
        raise EncryptionUnavailable(
            "Stored credentials cannot be decrypted with the configured keys."
        ) from exc


def mask(value: str) -> str:
    """A hint that a secret is set, never the secret: '••••abcd' for long values."""
    return "••••" + value[-4:] if len(value) >= 12 else "••••"
