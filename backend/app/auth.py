"""Sign-in: turn a Cognito access token into an owner id, or refuse it.

The browser signs in at Cognito (authorization code + PKCE) and sends the access token as
`Authorization: Bearer <token>`. This module is the only place a token is believed, and only after:

* the header says RS256 (never `none`, never an HMAC keyed with a public key), and names a `kid`
  that is in the user pool's published signing keys;
* the signature, `exp` and `iss` check out (PyJWT does the cryptography; nothing here is hand-rolled);
* `token_use` is `access` (an ID token is for the browser, not for this API) and a `client_id` is present;
* `sub` is a UUID, so it can never equal the ANONYMOUS owner or break a storage key.

Cognito access tokens carry no `aud`, so the app client is not pinned here: the pool has exactly one
client (created by the same stack), so any token it signs came from that client. Nothing about a token is ever
logged or returned. Locally, with no pool configured, none of this runs.
"""

import json
import logging
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from app.config import Settings
from app.errors import AuthUnavailableError, InvalidTokenError

logger = logging.getLogger("agentops.auth")

MAX_TOKEN_CHARS = 4096  # real Cognito access tokens are about 1 KB
LEEWAY_SECONDS = 30  # clock skew between Cognito and this host
JWKS_TIMEOUT_SECONDS = 3
MAX_JWKS_BYTES = 64 * 1024
MIN_REFRESH_SECONDS = 60.0  # at most one key fetch a minute, however many forged `kid`s arrive
MAX_KEY_AGE_SECONDS = 6 * 3600  # pick up a rotation even when every presented `kid` is known
_SUB = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

JwksFetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class Identity:
    sub: str


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Identity:
        """Raise InvalidTokenError for a token not to be trusted, AuthUnavailableError if the keys are unreachable."""
        ...


def fetch_jwks(url: str) -> dict[str, Any]:
    """GET the signing keys. The URL comes from validated configuration, never from a request or a token."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=JWKS_TIMEOUT_SECONDS) as response:  # noqa: S310
        raw = response.read(MAX_JWKS_BYTES + 1)
    if len(raw) > MAX_JWKS_BYTES:
        raise ValueError("key set too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("key set is not an object")
    return data


class _SigningKeys:
    """The pool's public keys by `kid`, refreshed rarely and never on demand more than once a minute."""

    def __init__(self, url: str, fetch: JwksFetcher, clock: Callable[[], float]) -> None:
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._attempted_at: float | None = None
        self._loaded_at: float | None = None

    def get(self, kid: str) -> Any:
        with self._lock:
            now = self._clock()
            known = self._keys.get(kid)
            stale = self._loaded_at is None or now - self._loaded_at > MAX_KEY_AGE_SECONDS
            may_try = self._attempted_at is None or now - self._attempted_at >= MIN_REFRESH_SECONDS
            if (known is None or stale) and may_try:
                self._refresh(now)
                known = self._keys.get(kid)
            if known is None and not self._keys:
                raise AuthUnavailableError("Sign-in is temporarily unavailable. Try again shortly.")
        if known is None:
            raise InvalidTokenError("This sign-in is not valid. Sign in again.")
        return known

    def _refresh(self, now: float) -> None:
        self._attempted_at = now
        try:
            keys = jwt.PyJWKSet.from_dict(self._fetch(self._url)).keys
            fresh = {k.key_id: k.key for k in keys if k.key_id and k.key_type == "RSA"}
        except Exception as exc:  # noqa: BLE001 - network, JSON and key-format failures all mean "cannot check"
            logger.error("could not refresh signing keys: %s", type(exc).__name__)
            return
        if fresh:
            self._keys, self._loaded_at = fresh, now


class CognitoTokenVerifier:
    def __init__(
        self,
        issuer: str,
        fetch: JwksFetcher = fetch_jwks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._keys = _SigningKeys(f"{issuer}/.well-known/jwks.json", fetch, clock)

    def verify(self, token: str) -> Identity:
        invalid = InvalidTokenError("This sign-in is not valid. Sign in again.")
        if not token or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
            raise invalid
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str):
                raise invalid
            claims = jwt.decode(
                token,
                self._keys.get(kid),
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:
            logger.info("token refused: %s", type(exc).__name__)
            raise invalid from None
        client_id, sub = claims.get("client_id"), claims.get("sub")
        if claims.get("token_use") != "access" or not isinstance(client_id, str) or not client_id:
            raise invalid
        if not isinstance(sub, str) or not _SUB.fullmatch(sub):
            raise invalid
        return Identity(sub=sub.lower())


def build_verifier(settings: Settings) -> TokenVerifier | None:
    """None when sign-in is not configured; otherwise a verifier for the configured user pool."""
    pool = settings.cognito_user_pool_id
    if not pool:
        return None
    region = pool.split("_", 1)[0]
    issuer = settings.cognito_issuer_override or f"https://cognito-idp.{region}.amazonaws.com/{pool}"
    return CognitoTokenVerifier(issuer)
