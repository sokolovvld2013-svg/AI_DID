"""Тесты API управления пользователями в разделе настроек.

Барьер проверяется отдельно от остального: если открыть раздел настроек
обычному пользователю (AUTH_ADMIN_ONLY_SETTINGS=false), выдача прав остаётся
закрытой, иначе это было бы повышение до администратора.

USERS_FILE подменяется на временный каталог, рабочий users.json не затрагивается.
"""

from __future__ import annotations

import time

import pytest

from core import auth, auth_router, settings_router

PASSWORD = "Пароль-123"


@pytest.fixture()
def store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Отдельный users.json на каждый тест."""
    path = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_FILE", path)
    return path


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включить вход и разрешить раздел настроек обычному пользователю."""
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "AUTH_ADMIN_ONLY_SETTINGS", False)


def make_admin(login: str = "admin") -> None:
    auth.upsert_user(login, PASSWORD, role=auth.ROLE_ADMIN, must_change_password=False)


def make_user(login: str) -> None:
    auth.upsert_user(login, PASSWORD, role=auth.ROLE_USER, must_change_password=False)


def sign_in(client, login: str) -> None:
    """Положить в клиент подписанную cookie сессии пользователя."""
    record = auth.get_user(login)
    token = auth.issue_token(
        auth.Principal(
            login=record.login,
            role=record.role,
            session_id="sid-test",
            # С дробями, как в auth_router: смена пароля сравнивается с этим
            # значением, и целая секунда здесь ломала бы токен как «ранний».
            issued_at=time.time(),
        )
    )
    client.cookies.set(auth_router.AUTH_COOKIE, token)


# ------------------------------------------------------------- вход выключен (dev)

async def test_list_users_never_exposes_password_hash(client, store) -> None:
    make_admin()
    resp = await client.get("/api/settings/users")
    assert resp.status_code == 200
    body = resp.json()
    assert [u["login"] for u in body["users"]] == ["admin"]
    # Хеш пароля наружу не отдаётся: в представлении его нет по построению.
    assert "password_hash" not in resp.text
    assert PASSWORD not in resp.text
    assert body["roles"] == ["user", "admin"]
    assert body["admins"] == 1


async def test_create_user_marks_password_as_temporary(client, store) -> None:
    resp = await client.post(
        "/api/settings/users",
        json={"login": "Ivanova", "password": PASSWORD, "role": "user"},
    )
    assert resp.status_code == 200
    record = auth.get_user("ivanova")
    assert record is not None
    assert record.must_change_password is True
    assert auth.verify_password(PASSWORD, record.password_hash)


async def test_create_user_rejects_short_password(client, store) -> None:
    resp = await client.post("/api/settings/users", json={"login": "ivanova", "password": "коротк"})
    assert resp.status_code == 400
    assert "8" in resp.json()["detail"]
    assert auth.get_user("ivanova") is None


async def test_create_user_rejects_non_latin_login(client, store) -> None:
    resp = await client.post("/api/settings/users", json={"login": "Пётр", "password": PASSWORD})
    assert resp.status_code == 400
    assert auth.get_user("Пётр") is None


async def test_reset_password_revokes_old_sessions(client, store) -> None:
    make_user("ivanova")
    # Токен выдан за доли секунды до смены пароля, то есть в ту же секунду:
    # именно этот случай не отзывался при точности до целых секунд.
    old_token = auth.issue_token(
        auth.Principal(login="ivanova", role="user", issued_at=time.time())
    )
    resp = await client.post(
        "/api/settings/users/ivanova/password", json={"password": "Другой-пароль-456"}
    )
    assert resp.status_code == 200
    assert auth.read_token(old_token) is None
    assert auth.verify_credentials("ivanova", "Другой-пароль-456") is not None


async def test_last_admin_cannot_be_deleted(client, store) -> None:
    make_admin()
    resp = await client.delete("/api/settings/users/admin")
    assert resp.status_code == 400
    assert "последний администратор" in resp.json()["detail"]
    assert auth.get_user("admin") is not None


async def test_last_admin_cannot_be_demoted(client, store) -> None:
    make_admin()
    resp = await client.post("/api/settings/users/admin/role", json={"role": "user"})
    assert resp.status_code == 400
    assert auth.get_user("admin").role == "admin"


async def test_block_and_unblock(client, store) -> None:
    make_admin()
    make_user("ivanova")
    blocked = await client.post("/api/settings/users/ivanova/disable", json={"disabled": True})
    assert blocked.status_code == 200
    assert auth.get_user("ivanova").disabled is True
    # Заблокированный не входит, даже с верным паролем.
    assert auth.verify_credentials("ivanova", PASSWORD) is None

    opened = await client.post("/api/settings/users/ivanova/disable", json={"disabled": False})
    assert opened.status_code == 200
    assert auth.get_user("ivanova").disabled is False
    assert auth.verify_credentials("ivanova", PASSWORD) is not None


async def test_unknown_login_returns_readable_error(client, store) -> None:
    resp = await client.post("/api/settings/users/nosuchuser/password", json={"password": PASSWORD})
    assert resp.status_code == 400
    assert "не найден" in resp.json()["detail"]


async def test_delete_removes_user(client, store) -> None:
    make_admin()
    make_user("ivanova")
    resp = await client.delete("/api/settings/users/ivanova")
    assert resp.status_code == 200
    assert auth.get_user("ivanova") is None


# ------------------------------------------------------------------- вход включён

async def test_anonymous_cannot_manage_users(client, store, auth_on) -> None:
    make_admin()
    resp = await client.get("/api/settings/users")
    assert resp.status_code == 401


async def test_non_admin_forbidden_even_when_settings_open(client, store, auth_on) -> None:
    """Пользователю с открытым разделом настроек управлять учётками нельзя."""
    make_admin()
    make_user("ivanova")
    sign_in(client, "ivanova")

    assert (await client.get("/api/settings/users")).status_code == 403
    assert (
        await client.post("/api/settings/users", json={"login": "x", "password": PASSWORD})
    ).status_code == 403
    assert (
        await client.post("/api/settings/users/ivanova/role", json={"role": "admin"})
    ).status_code == 403
    assert (
        await client.post("/api/settings/users/ivanova/disable", json={"disabled": True})
    ).status_code == 403
    assert (await client.delete("/api/settings/users/ivanova")).status_code == 403
    # И роли не изменились, и новых администраторов не появилось.
    assert auth.get_user("ivanova").role == "user"


async def test_admin_allowed_when_settings_open(client, store, auth_on) -> None:
    make_admin()
    sign_in(client, "admin")
    assert (await client.get("/api/settings/users")).status_code == 200


async def test_admin_can_reset_own_password_and_keep_session(client, store, auth_on) -> None:
    """Смена собственного пароля не должна выкидывать администратора из системы."""
    make_admin()
    sign_in(client, "admin")

    resp = await client.post(
        "/api/settings/users/admin/password",
        json={"password": "Новый-пароль-789", "must_change_password": False},
    )
    assert resp.status_code == 200
    assert auth_router.AUTH_COOKIE in resp.headers.get("set-cookie", "")
    assert auth.verify_credentials("admin", "Новый-пароль-789") is not None
    # Перевыпущенная cookie работает — администратор остался в системе.
    assert (await client.get("/api/settings/users")).status_code == 200


async def test_temporary_password_blocks_management(client, store, auth_on) -> None:
    """Пока пароль временный, управление пользователями недоступно."""
    auth.upsert_user("ivanova", PASSWORD, role=auth.ROLE_ADMIN, must_change_password=True)
    sign_in(client, "ivanova")
    resp = await client.get("/api/settings/users")
    assert resp.status_code == 403
    assert "смените пароль" in resp.json()["detail"].lower()
