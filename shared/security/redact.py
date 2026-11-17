from __future__ import annotations

import logging
import re
import sys

MARK = "[redacted]"
MIN_SECRET_LENGTH = 8

_PATTERNS = (
    (re.compile(r"(?<![0-9])\d{6,12}:[A-Za-z0-9_-]{30,}"), MARK),
    (re.compile(r"(?i)\b((?:postgres(?:ql)?|mysql|redis|amqp|https?|socks5h?)://[^:/\s@]+:)[^@\s/]+@"),
     r"\g<1>" + MARK + "@"),
    (re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{40,}"), MARK),
    (re.compile(r"(?i)\b((?:api[_-]?hash|api[_-]?key|password|passwd|secret|access[_-]?token)\s*[=:]\s*)"
                r"[\"']?[^\s\"',;&]{6,}"), r"\g<1>" + MARK),
)


def _values() -> "tuple[str, ...]":
    shared = sys.modules.get("family_process")
    values = getattr(shared, "secret_values", ()) if shared is not None else ()
    return tuple(sorted((v for v in values if isinstance(v, str) and len(v) >= MIN_SECRET_LENGTH),
                        key=len, reverse=True))


def redact(text):
    if not isinstance(text, str) or not text:
        return text
    for value in _values():
        if value in text:
            text = text.replace(value, MARK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message:
                record.msg, record.args = cleaned, None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            pass
        return True
