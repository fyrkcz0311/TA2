"""Redacción local de secretos antes de consultar al modelo o guardar trazas."""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[DATO SENSIBLE OMITIDO]"

_SECRET_PATTERNS = (
    # Preserve the label so the assistant can still understand the email.
    re.compile(
        r"(?i)(\b(?:contrase(?:ñ|n)a|password|clave\s+de\s+acceso|api[_ -]?key|token)\b\s*(?:es|:|=)\s*)"
        r"(?!\[DATO SENSIBLE OMITIDO\])([\"']?[^\s,;\"']+[\"']?)"
    ),
    re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]{8,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b"),
    re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
)


def _replace_labeled_secret(match: re.Match[str]) -> str:
    token = match.group(2)
    suffix = token[len(token.rstrip(".!?")):]
    return match.group(1) + REDACTED + suffix


def redact_text(value: str, known_secrets: tuple[str, ...] = ()) -> str:
    """Oculta patrones comunes y los valores ya extraídos del correo original."""
    result = value
    for pattern in _SECRET_PATTERNS[:2]:
        result = pattern.sub(_replace_labeled_secret, result)
    for pattern in _SECRET_PATTERNS[2:]:
        result = pattern.sub(REDACTED, result)
    placeholder = "\ue000"
    result = result.replace(REDACTED, placeholder)
    for secret in sorted(known_secrets, key=len, reverse=True):
        if secret:
            result = re.sub(re.escape(secret), REDACTED, result, flags=re.IGNORECASE)
    return result.replace(placeholder, REDACTED)


def sanitize_email(value: str) -> tuple[str, tuple[str, ...]]:
    """Devuelve el correo seguro y los secretos para filtrar respuestas posteriores."""
    secrets: set[str] = set()
    for pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(value):
            secret = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(0)
            secret = secret.strip("\"'.!?")
            if secret:
                secrets.add(secret)
    return redact_text(value), tuple(secrets)


def redact_value(value: Any, known_secrets: tuple[str, ...] = ()) -> Any:
    """Aplica la redacción a argumentos y respuestas JSON sin alterar su estructura."""
    if isinstance(value, str):
        return redact_text(value, known_secrets)
    if isinstance(value, dict):
        return {key: redact_value(item, known_secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, known_secrets) for item in value]
    return value
