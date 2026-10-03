"""Тесты хеширования, выдачи сессий и троттлинга в core.auth.

Все тесты работают с USERS_FILE из conftest (временный каталог),
поэтому реальные users.json не затрагиваются.
"""

from __future__ import annotations

import pytest

from core import auth
from core.auth import AuthError, Principal, UserRecord


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сброс состояния модуля и троттлинга между тестами."""
    monkeypatch.setattr(auth, "USERS_FILE", auth.USERS_FILE)
    auth.reset_throttle()
    yield
    auth.reset_throttle()


def test_hash_password_is_salted() -> None:
    first = auth.hash_password("Пароль-123")
    second = auth.hash_password("Пароль-123")
    assert first != second
    assert auth.verify_password("Пароль-123", first)
    assert auth.verify_password("Пароль-123", second)


def test_verify_password_rejects_wrong_password() -> None:
    hashed = auth.hash_password("Пароль-123")
    assert not auth.verify_password("Пароль-124", hashed)


def test_validate_password_min_length() -> None:
    with pytest.raises(AuthError):
        auth.validate_password("коротк")


def test_validate_password_accepts_long_password() -> None:
    auth.validate_password("достаточно-длинный-пароль")


def test_normalize_login_trims_and_lowercases() -> None:
    assert auth.normalize_login("  ivanov  ") == "ivanov"


def test_normalize_login_rejects_empty() -> None:
    with pytest.raises(AuthError):
        auth.normalize_login(None)
    with pytest.raises(AuthError):
        auth.normalize_login("   ")


def test_token_roundtrip_requires_existing_user() -> None:
    principal = Principal(
        login="ivanov",
        role=auth.ROLE_USER,
        session_id="sid-1",
        issued_at=0,
    )
    token = auth.issue_token(principal)
    # Пользователя в users.json нет — токен не проходит проверку.
    assert auth.read_token(token) is None


def test_token_roundtrip_for_known_user(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    auth.upsert_user("ivanov", "Пароль-123", role=auth.ROLE_USER, must_change_password=False)

    principal = Principal(
        login="ivanov",
        role=auth.ROLE_USER,
        session_id="sid-1",
        issued_at=0,
    )
    token = auth.issue_token(principal)
    restored = auth.read_token(token)
    assert restored is not None
    assert restored.login == "ivanov"
    assert restored.session_id == "sid-1"


def test_read_token_rejects_garbage() -> None:
    assert auth.read_token("не-токен") is None


def test_throttle_blocks_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "AUTH_MAX_ATTEMPTS", 2)
    key = auth.throttle_key("ivanov", "10.0.0.1")

    assert auth.register_failure(key) == 0
    left = auth.register_failure(key)
    assert left > 0
    assert auth.lockout_remaining(key) > 0

    auth.clear_failures(key)
    assert auth.lockout_remaining(key) == 0


def test_user_record_defaults() -> None:
    record = UserRecord(login="ivanov", role=auth.ROLE_USER, password_hash="x")
    assert record.disabled is False
    assert record.must_change_password is False