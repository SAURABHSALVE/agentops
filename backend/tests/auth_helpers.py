"""A stand-in for a Cognito user pool: signs access tokens with a throwaway RSA key and publishes its key set.

Nothing here talks to AWS. The key is generated fresh for each test session and protects nothing.
"""

import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth import CognitoTokenVerifier

POOL_ID = "us-east-1_TestPool1"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}"
KID = "test-key-1"
USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"


class FakePool:
    def __init__(self, kid: str = KID) -> None:
        self.kid = kid
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._pem = self.private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        self.fetches = 0
        self.fail = False
        self.extra_keys: list[dict[str, Any]] = []

    def jwks(self) -> dict[str, Any]:
        public = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "alg": "RS256", "use": "sig"}, *self.extra_keys]}

    def fetch(self, url: str) -> dict[str, Any]:
        assert url == f"{ISSUER}/.well-known/jwks.json", url
        self.fetches += 1
        if self.fail:
            raise OSError("pool unreachable")
        return self.jwks()

    def token(self, sub: str = USER_A, *, kid: str | None = None, **claims: Any) -> str:
        """An access token. A claim set to None is left out; header keys can be overridden via `headers`."""
        now = int(time.time())
        body: dict[str, Any] = {
            "sub": sub,
            "iss": ISSUER,
            "client_id": "test-client",
            "token_use": "access",
            "scope": "openid email",
            "iat": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
        }
        headers = claims.pop("headers", {})
        body.update(claims)
        body = {k: v for k, v in body.items() if v is not None}
        return jwt.encode(body, self._pem, algorithm="RS256", headers={"kid": kid or self.kid, **headers})

    def verifier(self, clock: Any = time.monotonic) -> CognitoTokenVerifier:
        return CognitoTokenVerifier(ISSUER, fetch=self.fetch, clock=clock)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
