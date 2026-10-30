"""Public-demo protections for the endpoints that cost money or change state.

A deployed backend that holds a billable model key is an abuse target (SECURITY.md, "Public demo"):

* AccessGate: when an access code is configured, every write (start an investigation, approve,
  reject) must present it in `X-Access-Code`. Reads stay open because they serve only simulated data.
  The comparison is constant-time and a wrong or missing code is a plain 401 that reveals nothing.
* RateLimiter: caps how many investigations can start per minute and per hour for the whole process.
  It is per process, so it is a backstop behind the API Gateway throttle, not a global guarantee.

The code itself is never logged and never returned. Locally nothing is configured, so nothing changes.
"""

import hmac
import os
import threading
import time
from collections import deque
from collections.abc import Callable

from app.config import ConfigError
from app.errors import RateLimitedError, UnauthorizedError

ACCESS_HEADER = "X-Access-Code"


def _secret_from_arn(arn: str) -> str:
    import boto3  # lazy: local runs and unit tests never need AWS

    value = boto3.client("secretsmanager").get_secret_value(SecretId=arn).get("SecretString", "")
    return str(value).strip()


def load_access_code() -> str | None:
    """From `ACCESS_CODE`, or fetched once from the Secrets Manager secret named by `ACCESS_SECRET_ARN`."""
    code = os.environ.get("ACCESS_CODE", "").strip()
    arn = os.environ.get("ACCESS_SECRET_ARN", "").strip()
    if not code and arn:
        try:
            code = _secret_from_arn(arn)
        except Exception:
            raise ConfigError("ACCESS_SECRET_ARN could not be read.") from None
    if code and len(code) < 8:
        raise ConfigError("The access code must be at least 8 characters.")
    return code or None


class AccessGate:
    def __init__(self, code: str | None) -> None:
        self._code = code.encode() if code else None

    @property
    def required(self) -> bool:
        return self._code is not None

    def check(self, presented: str | None) -> None:
        if self._code is None:
            return
        if presented is None or not hmac.compare_digest(presented.encode(), self._code):
            raise UnauthorizedError("A valid access code is required for this action.")


class RateLimiter:
    def __init__(self, per_minute: int, per_hour: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limits = ((60.0, per_minute), (3600.0, per_hour))
        self._events: deque[float] = deque()
        self._clock = clock
        self._lock = threading.Lock()

    def check(self) -> None:
        """Record one start, or raise RateLimitedError if a window is full (nothing is recorded then)."""
        with self._lock:
            now = self._clock()
            while self._events and now - self._events[0] > 3600.0:
                self._events.popleft()
            for window, limit in self._limits:
                inside = [t for t in self._events if now - t <= window]
                if len(inside) >= limit:
                    wait = int(window - (now - inside[0])) + 1
                    raise RateLimitedError(f"Too many investigations started. Try again in about {wait} s.", wait)
            self._events.append(now)
