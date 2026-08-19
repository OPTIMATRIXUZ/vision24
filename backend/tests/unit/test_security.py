import uuid

import pytest

from app import security
from app.errors import ConfigurationError
from app.security import TokenError

pytestmark = pytest.mark.unit

SECRET = "test-signing-key-not-a-real-one-padded-to-length"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr(security.settings, "auth_secret_key", SECRET)


def claims_args(**overrides):
    args = {
        "user_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "role": "owner",
    }
    args.update(overrides)
    return args


class TestSigningKey:
    def test_signing_without_a_key_is_refused(self, monkeypatch):
        monkeypatch.setattr(security.settings, "auth_secret_key", "")
        with pytest.raises(ConfigurationError):
            security.create_access_token(**claims_args())

    def test_a_short_key_is_refused(self, monkeypatch):
        monkeypatch.setattr(security.settings, "auth_secret_key", "too-short")
        with pytest.raises(ConfigurationError) as exc:
            security.require_auth_secret()
        assert "too short" in str(exc.value)

    def test_a_key_exactly_at_the_floor_is_accepted(self, monkeypatch):
        monkeypatch.setattr(security.settings, "auth_secret_key", "x" * security.MIN_SECRET_BYTES)
        assert security.require_auth_secret()

    def test_the_error_says_how_to_generate_one(self, monkeypatch):
        monkeypatch.setattr(security.settings, "auth_secret_key", "")
        with pytest.raises(ConfigurationError) as exc:
            security.require_auth_secret()
        assert "AUTH_SECRET_KEY" in str(exc.value)


class TestPasswords:
    def test_round_trip(self):
        hashed = security.hash_password("correct horse battery staple")
        assert security.verify_password("correct horse battery staple", hashed)

    def test_wrong_password_is_rejected(self):
        hashed = security.hash_password("right")
        assert not security.verify_password("wrong", hashed)

    def test_the_hash_is_not_the_password(self):
        hashed = security.hash_password("hunter2")
        assert "hunter2" not in hashed
        assert hashed.startswith("$argon2")

    def test_the_same_password_hashes_differently_each_time(self):
        assert security.hash_password("same") != security.hash_password("same")

    def test_verifying_against_no_hash_is_false_but_still_does_work(self):
        assert security.verify_password("anything", None) is False

    def test_a_garbage_hash_does_not_raise(self):
        assert security.verify_password("x", "not-a-hash") is False

    def test_needs_rehash_on_a_malformed_hash(self):
        assert security.password_needs_rehash("not-a-hash") is True


class TestAccessTokens:
    def test_round_trip_preserves_every_claim(self):
        args = claims_args()
        decoded = security.decode_access_token(security.create_access_token(**args))
        assert decoded.user_id == args["user_id"]
        assert decoded.tenant_id == args["tenant_id"]
        assert decoded.session_id == args["session_id"]
        assert decoded.role == "owner"

    def test_a_token_signed_with_another_key_is_rejected(self, monkeypatch):
        token = security.create_access_token(**claims_args())
        monkeypatch.setattr(
            security.settings, "auth_secret_key", "a-completely-different-key-also-long-enough"
        )
        with pytest.raises(TokenError):
            security.decode_access_token(token)

    def test_a_tampered_token_is_rejected(self):
        token = security.create_access_token(**claims_args())
        head, payload, sig = token.split(".")
        with pytest.raises(TokenError):
            security.decode_access_token(f"{head}.{payload}x.{sig}")

    def test_an_expired_token_is_rejected(self, monkeypatch):
        monkeypatch.setattr(security.settings, "access_token_ttl_minutes", -1)
        token = security.create_access_token(**claims_args())
        with pytest.raises(TokenError):
            security.decode_access_token(token)

    def test_garbage_is_rejected(self):
        with pytest.raises(TokenError):
            security.decode_access_token("not.a.token")

    def test_a_refresh_token_cannot_be_spent_as_an_access_token(self):
        import jwt

        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "tid": str(uuid.uuid4()),
                "sid": str(uuid.uuid4()),
                "typ": "refresh",
                "exp": 9_999_999_999,
            },
            SECRET,
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            security.decode_access_token(forged)

    def test_an_unsigned_token_is_rejected(self):
        import jwt

        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "tid": str(uuid.uuid4()), "sid": str(uuid.uuid4())},
            key="",
            algorithm="none",
        )
        with pytest.raises(TokenError):
            security.decode_access_token(forged)


class TestRefreshTokens:
    def test_only_the_hash_would_be_stored(self):
        token, hashed = security.new_refresh_token()
        assert token != hashed
        assert security.hash_token(token) == hashed
        assert len(hashed) == 64

    def test_tokens_are_unique(self):
        assert len({security.new_refresh_token()[0] for _ in range(50)}) == 50

    def test_comparison_is_constant_time(self):
        _, hashed = security.new_refresh_token()
        assert security.tokens_equal(hashed, hashed)
        assert not security.tokens_equal(hashed, "0" * 64)


class TestApiKeys:
    def test_shape_is_parseable(self):
        full, prefix, hashed = security.new_api_key()
        assert full.startswith(security.API_KEY_PREFIX)
        parsed = security.parse_api_key(full)
        assert parsed is not None
        assert parsed[0] == prefix
        assert security.hash_token(parsed[1]) == hashed

    def test_the_secret_is_not_recoverable_from_the_stored_hash(self):
        full, _, hashed = security.new_api_key()
        assert hashed not in full

    def test_prefixes_are_unique_enough_to_index(self):
        assert len({security.new_api_key()[1] for _ in range(200)}) == 200

    @pytest.mark.parametrize(
        "value", ["", "nope", "v24k_", "v24k_abc", "bearer something", "v24k__secret"]
    )
    def test_malformed_keys_parse_to_none(self, value):
        assert security.parse_api_key(value) is None


def test_security_does_not_leak_into_the_worker_import_graph():
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import sys
        import app.models  # what the worker imports
        assert "argon2" not in sys.modules, "argon2 reached app.models"
        assert "jwt" not in sys.modules, "pyjwt reached app.models"
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
