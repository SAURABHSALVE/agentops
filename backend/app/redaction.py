"""Remove secrets and personal data from telemetry text before it reaches a model, the API or the UI.

Logs written by real systems can contain tokens, passwords, emails and card numbers. Simulated and
lab data contain none, so this changes nothing for them. It exists so that a future real source
(for example CloudWatch) cannot leak those values just because it is plugged in. It is a safety net,
not a guarantee: patterns catch common shapes, not every possible secret.
"""

import re
from re import Match

_JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_AWS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")
_API_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})")
# Words that always introduce a secret, versus words that only sometimes do ("token: expired" is a message).
_ALWAYS = r"password|passwd|pwd|secret|client[_-]?secret|secret[_-]?key|private[_-]?key"
_SOMETIMES = r"token|api[_-]?key|apikey|access[_-]?key|authorization|auth"
_SEP = r"""["']?\s*[=:]\s*"""  # `key=`, `key: ` and the JSON form `"key": `
_VALUE = r"""("[^"]*"|'[^']*'|[^\s,;&"']+)"""
_KV_ALWAYS = re.compile(rf"(?i)\b({_ALWAYS})\b({_SEP}){_VALUE}")
_KV_SOMETIMES = re.compile(rf"(?i)\b({_SOMETIMES})\b({_SEP})((?:\"[^\"]{{8,}}\")|(?:'[^']{{8,}}')|[^\s,;&\"']{{8,}})")
# Quantifiers are bounded (RFC limits: 64-char local part, 63-char labels) so a long run of dots or letters
# cannot make the scan quadratic. Log text is attacker-influenced.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}\b")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card(match: Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[card]" if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)


MAX_INPUT_CHARS = 20_000  # callers show far less; this only bounds the work done on a hostile line


def redact(text: str) -> str:
    """The text with secrets and personal data replaced by short placeholders such as `[email]`."""
    text = text[:MAX_INPUT_CHARS]
    text = _JWT.sub("[token]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", text)
    text = _AWS_KEY_ID.sub("[aws-key-id]", text)
    text = _API_KEY.sub("[api-key]", text)
    text = _KV_ALWAYS.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    text = _KV_SOMETIMES.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    text = _EMAIL.sub("[email]", text)
    text = _IPV4.sub("[ip]", text)
    return _CARD.sub(_card, text)
