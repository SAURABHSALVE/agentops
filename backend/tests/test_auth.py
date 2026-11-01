"""Token verification: only a genuine, current access token from this pool becomes an identity."""

import base64
import json
import time
from typing import Any

import jwt
import pytest

from app.auth import MAX_TOKEN_CHARS, MIN_REFRESH_SECONDS, CognitoTokenVerifier, build_verifier
from app.config import ConfigError, Settings
from app.errors import AuthUnavailableError, InvalidTokenError
from tests.auth_helpers import ISSUER, KID, POOL_ID, USER_A, FakePool


@pytest.fixture(scope="module")
def pool() -> FakePool:
    return FakePool()


@pytest.fixture
def verifier(pool: FakePool) -> CognitoTokenVerifier:
    pool.fetches, pool.fail = 0, False
    return pool.verifier()


def test_a_genuine_access_token_gives_the_users_id(verifier: CognitoTokenVerifier, pool: FakePool) -> None:
    assert verifier.verify(pool.token(USER_A)).sub == USER_A
    assert verifier.verify(pool.token(USER_A.upper())).sub == USER_A  # one spelling per user


def test_signing_keys_are_fetched_once_not_per_request(verifier: CognitoTokenVerifier, pool: FakePool) -> None:
    for _ in range(5):
        verifier.verify(pool.token())
    assert pool.fetches == 1


@pytest.mark.parametrize(
    ("label", "claims"),
    [
        ("expired", {"exp": int(time.time()) - 600}),
        ("wrong issuer", {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"}),
        ("an id token", {"token_use": "id", "aud": "test-client"}),
        ("no token_use", {"token_use": None}),
        ("no client_id", {"client_id": None}),
        ("empty client_id", {"client_id": ""}),
        ("no expiry", {"exp": None}),
        ("no subject", {"sub": None}),
        ("sub is not a uuid", {"sub": "anonymous"}),
        ("sub that would break a key", {"sub": "abc#def"}),
        ("not yet valid", {"nbf": int(time.time()) + 600}),
    ],
)
def test_bad_claims_are_refused(
    verifier: CognitoTokenVerifier, pool: FakePool, label: str, claims: dict[str, Any]
) -> None:
    with pytest.raises(InvalidTokenError):
        verifier.verify(pool.token(**claims))


def test_a_little_clock_skew_is_tolerated(verifier: CognitoTokenVerifier, pool: FakePool) -> None:
    assert verifier.verify(pool.token(exp=int(time.time()) - 5)).sub == USER_A


def _segments(token: str) -> list[str]:
    return token.split(".")


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_a_token_with_the_signature_of_another_is_refused(verifier: CognitoTokenVerifier, pool: FakePool) -> None:
    head, _, sig = _segments(pool.token(USER_A))
    forged_body = _b64({"sub": "33333333-3333-4333-8333-333333333333", "iss": ISSUER, "client_id": "c",
                        "token_use": "access", "exp": int(time.time()) + 3600})  # fmt: skip
    with pytest.raises(InvalidTokenError):
        verifier.verify(f"{head}.{forged_body}.{sig}")


def test_a_token_signed_by_some_other_key_is_refused_even_with_the_right_kid(
    verifier: CognitoTokenVerifier,
) -> None:
    impostor = FakePool(kid=KID)  # same kid, different private key
    with pytest.raises(InvalidTokenError):
        verifier.verify(impostor.token())


def test_algorithm_none_is_refused(verifier: CognitoTokenVerifier) -> None:
    body = {"sub": USER_A, "iss": ISSUER, "client_id": "c", "token_use": "access", "exp": int(time.time()) + 3600}
    unsigned = jwt.encode(body, None, algorithm="none", headers={"kid": KID})  # type: ignore[arg-type]
    with pytest.raises(InvalidTokenError):
        verifier.verify(unsigned)


def test_an_hmac_token_cannot_pass_as_rsa(verifier: CognitoTokenVerifier) -> None:
    body = {"sub": USER_A, "iss": ISSUER, "client_id": "c", "token_use": "access", "exp": int(time.time()) + 3600}
    hmac_token = jwt.encode(body, "x" * 64, algorithm="HS256", headers={"kid": KID})
    with pytest.raises(InvalidTokenError):
        verifier.verify(hmac_token)


@pytest.mark.parametrize("junk", ["", "abc", "a.b", "a.b.c.d", "....", "x" * (MAX_TOKEN_CHARS + 1), "é.é.é"])
def test_garbage_is_refused_without_a_key_fetch(verifier: CognitoTokenVerifier, pool: FakePool, junk: str) -> None:
    with pytest.raises(InvalidTokenError):
        verifier.verify(junk)
    assert pool.fetches == 0


def test_a_token_without_a_key_id_is_refused(verifier: CognitoTokenVerifier, pool: FakePool) -> None:
    body = {"sub": USER_A, "iss": ISSUER, "client_id": "c", "token_use": "access", "exp": int(time.time()) + 3600}
    no_kid = jwt.encode(body, pool.private_key, algorithm="RS256")
    odd_kid = f"{_b64({'alg': 'RS256', 'typ': 'JWT', 'kid': 7})}.{_b64(body)}.c2ln"  # PyJWT will not even encode this
    for token in (no_kid, odd_kid):
        with pytest.raises(InvalidTokenError):
            verifier.verify(token)


def test_the_error_never_echoes_the_token(verifier: CognitoTokenVerifier, pool: FakePool) -> None:
    token = pool.token(exp=int(time.time()) - 600)
    with pytest.raises(InvalidTokenError) as caught:
        verifier.verify(token)
    assert token not in caught.value.message and token not in str(caught.value)


# ---- key fetching ---------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_forged_key_ids_cannot_make_the_server_hammer_the_pool(pool: FakePool) -> None:
    pool.fetches, pool.fail = 0, False
    clock = Clock()
    verifier = pool.verifier(clock)
    for n in range(20):
        with pytest.raises(InvalidTokenError):
            verifier.verify(pool.token(kid=f"made-up-{n}"))
    assert pool.fetches == 1  # the first unknown kid refreshed once; the rest are inside the cooldown
    clock.now += MIN_REFRESH_SECONDS + 1
    with pytest.raises(InvalidTokenError):
        verifier.verify(pool.token(kid="made-up-again"))
    assert pool.fetches == 2


def test_a_rotated_key_is_picked_up_after_the_cooldown(pool: FakePool) -> None:
    pool.fetches, pool.fail = 0, False
    clock = Clock()
    verifier = pool.verifier(clock)
    verifier.verify(pool.token())  # loads the key set
    rotated = FakePool(kid="rotated-key")
    token = rotated.token()
    pool.extra_keys = [rotated.jwks()["keys"][0]]
    try:
        with pytest.raises(InvalidTokenError):
            verifier.verify(token)  # unknown kid, but inside the cooldown: no refetch
        clock.now += MIN_REFRESH_SECONDS + 1
        assert verifier.verify(token).sub == USER_A
    finally:
        pool.extra_keys = []


def test_unreachable_keys_mean_unavailable_not_a_bad_token(pool: FakePool) -> None:
    pool.fetches, pool.fail = 0, True
    clock = Clock()
    verifier = pool.verifier(clock)
    with pytest.raises(AuthUnavailableError):
        verifier.verify(pool.token())
    with pytest.raises(AuthUnavailableError):  # still inside the cooldown: no hammering
        verifier.verify(pool.token())
    assert pool.fetches == 1
    pool.fail = False
    clock.now += MIN_REFRESH_SECONDS + 1
    assert verifier.verify(pool.token()).sub == USER_A


def test_a_malformed_key_set_is_unavailable_not_a_crash() -> None:
    verifier = CognitoTokenVerifier(ISSUER, fetch=lambda _url: {"keys": "nope"})
    with pytest.raises(AuthUnavailableError):
        verifier.verify(FakePool().token())


# ---- configuration --------------------------------------------------------------------------


def test_no_user_pool_means_no_verifier() -> None:
    assert build_verifier(Settings()) is None


def test_the_issuer_is_derived_from_the_pool_id() -> None:
    verifier = build_verifier(Settings(cognito_user_pool_id=POOL_ID))
    assert isinstance(verifier, CognitoTokenVerifier) and verifier._issuer == ISSUER  # noqa: SLF001


def test_settings_read_and_validate_the_pool_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL_ID)
    assert Settings.from_env().cognito_user_pool_id == POOL_ID
    for bad in ("not-a-pool", "us-east-1_", "us-east-1_has space", "../../etc_passwd1"):
        monkeypatch.setenv("COGNITO_USER_POOL_ID", bad)
        with pytest.raises(ConfigError):
            Settings.from_env()


def test_an_issuer_override_must_be_https_or_loopback_and_needs_a_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL_ID)
    monkeypatch.setenv("COGNITO_ISSUER", "http://127.0.0.1:9099/pool/")
    assert Settings.from_env().cognito_issuer_override == "http://127.0.0.1:9099/pool"
    for bad in ("http://example.com/x", "ftp://x", "https://x/?q=1", "https://x/#f", "javascript:alert(1)"):
        monkeypatch.setenv("COGNITO_ISSUER", bad)
        with pytest.raises(ConfigError):
            Settings.from_env()
    monkeypatch.delenv("COGNITO_USER_POOL_ID")
    monkeypatch.setenv("COGNITO_ISSUER", "https://issuer.example")
    with pytest.raises(ConfigError):
        Settings.from_env()
