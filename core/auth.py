"""Вход по логину и паролю: пользователи, argon2id, сессия в подписанной cookie.

Хранение — JSON-файл ``users.json`` рядом с приложением (тот же приём, что и
в ``core/settings.py``): атомарная запись через ``os.replace`` и один
``RLock`` на процесс. Пароли в нём только в виде argon2id-хешей; ключ
подписи cookie лежит в этом же файле, поэтому ``.env`` обычно править не
нужно — он появляется автоматически при первом создании пользователя.

Сессия — подписанный itsdangerous-токен в cookie ``did_auth``: в самом токене
лежит только логин, время выдачи и идентификатор сессии браузера. Пароль и
его хеш наружу не отдаются, а подделать или изменить логин нельзя. Смена
пароля делает старые сессии недействительными: в токене записано время
выдачи, и оно сравнивается со временем последней смены пароля.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from config import (
    AUTH_COOKIE,
    AUTH_COOKIE_SECURE,
    AUTH_ENABLED,
    AUTH_LOCKOUT_SEC,
    AUTH_MAX_ATTEMPTS,
    AUTH_MAX_PASSWORD_LEN,
    AUTH_MIN_PASSWORD_LEN,
    AUTH_SECRET,
    AUTH_SESSION_MAX_AGE,
    USERS_FILE,
)
from core.app_time import now_app

logger = logging.getLogger(__name__)

_lock = threading.RLock()

USERS_FILE_VERSION = 1
SESSION_SALT = "did-auth-v1"
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_USER, ROLE_ADMIN)

# Логин попадает в cookie, в имя файла выгрузки логов и в путь по браузеру,
# поэтому набор символов сознательно узкий: латиница, цифры, «._-».
_LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Хеш-заглушка для несуществующих логинов: считается один раз лениво.
_DUMMY_HASH: str | None = None


class AuthError(ValueError):
    """Некорректные данные пользователя или пароля."""


class AuthUnavailable(RuntimeError):
    """Вход настроен, но пользователей в системе нет."""


@dataclass(frozen=True)
class UserRecord:
    """Запись о пользователе из users.json (без представления для интерфейса)."""

    login: str
    role: str
    password_hash: str
    must_change_password: bool = False
    disabled: bool = False
    password_changed_at: str = ""

    def public(self) -> dict[str, Any]:
        """Данные для интерфейса и логов — без хеша пароля."""
        return {
            "login": self.login,
            "role": self.role,
            "must_change_password": self.must_change_password,
            "disabled": self.disabled,
            "password_changed_at": self.password_changed_at,
        }


@dataclass(frozen=True)
class Principal:
    """Кто вошёл. Кладётся в ``request.state.user`` — оттуда его читает
    :func:`core.user_logs.get_login`, поэтому логи пользователей начинают
    писать настоящий логин без правок в роутерах."""

    login: str
    role: str
    session_id: str = ""
    # Доли секунды: точность нужна, чтобы отзыв сессий сравнением с
    # password_changed_at не спотыкался о границу секунды (см. read_token).
    issued_at: float = 0.0
    # Пароль выдан временно: пока флаг не снят, пускаем только на смену пароля.
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def public(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "role": self.role,
            "must_change_password": self.must_change_password,
        }


# ---------------------------------------------------------------- хеширование

def _hasher():
    """Argon2id через pwdlib. Создаётся лениво: импорт не должен тянуть
    нативную библиотеку, пока вход выключен."""
    try:
        from pwdlib import PasswordHash
        from pwdlib.hashers.argon2 import Argon2Hasher
    except ImportError as e:  # pragma: no cover - зависит от окружения
        raise AuthUnavailable(
            'Не установлен pwdlib[argon2] — выполните: pip install "pwdlib[argon2]"'
        ) from e
    return PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher().hash(password)


def validate_password(password: str) -> None:
    """Проверка пароля перед сохранением."""
    if not isinstance(password, str) or not password:
        raise AuthError("Пароль не может быть пустым")
    if len(password) < AUTH_MIN_PASSWORD_LEN:
        raise AuthError(
            f"Пароль короче {AUTH_MIN_PASSWORD_LEN} символов — сделайте его длиннее"
        )
    if len(password) > AUTH_MAX_PASSWORD_LEN:
        raise AuthError(f"Пароль длиннее {AUTH_MAX_PASSWORD_LEN} символов")
    if password.strip() != password:
        raise AuthError("Пароль не должен начинаться или заканчиваться пробелом")


def _dummy_hash() -> str:
    """Хеш-заглушка: проверяется, когда логина нет в файле.

    Без неё время ответа на несуществующий логин заметно короче, чем на
    существующий, и по задержке можно перебором выяснить, какие логины есть.
    """
    global _DUMMY_HASH
    with _lock:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = _hasher().hash(secrets.token_urlsafe(24))
        return _DUMMY_HASH


def verify_password(password: str, password_hash: str) -> bool:
    """Сверка пароля с хешем. Любая ошибка разбора — «не подошёл»."""
    try:
        return bool(_hasher().verify(password or "", password_hash or ""))
    except Exception as e:
        # UnknownHashError на испорченном хеше и любые сбои библиотеки:
        # наружу это уходить не должно.
        logger.warning("Не удалось проверить хеш пароля: %s", type(e).__name__)
        return False


def normalize_login(raw: Any) -> str:
    """Логин в каноническом виде: без пробелов и в нижнем регистре."""
    login = str(raw or "").strip().lower()
    if not login:
        raise AuthError("Логин не может быть пустым")
    if not _LOGIN_RE.match(login):
        raise AuthError(
            "Логин: 1–64 символа, латиница, цифры и «._-», первый символ — "
            "не точка и не дефис"
        )
    return login


# ------------------------------------------------------------- файл пользователей

def _empty_store() -> dict[str, Any]:
    return {"version": USERS_FILE_VERSION, "session_secret": "", "users": []}


def _read_store() -> dict[str, Any]:
    store = _empty_store()
    try:
        raw = USERS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return store
    except OSError as e:
        logger.warning("Не удалось прочитать %s: %s", USERS_FILE, e)
        return store
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Повреждён файл пользователей %s: %s", USERS_FILE, e)
        return store
    if not isinstance(data, dict):
        logger.error("Файл пользователей %s: ожидался объект", USERS_FILE)
        return store
    store["session_secret"] = str(data.get("session_secret") or "")
    users = data.get("users")
    store["users"] = [u for u in users if isinstance(u, dict)] if isinstance(users, list) else []
    return store


def _write_store(store: dict[str, Any]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = USERS_FILE.with_name(USERS_FILE.name + ".tmp")
    tmp_path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        # На Windows права выставлять нечем — файл всё равно не в git.
        pass


def _to_record(item: dict[str, Any]) -> UserRecord | None:
    login = str(item.get("login") or "").strip().lower()
    if not login or not str(item.get("password_hash") or ""):
        return None
    role = str(item.get("role") or ROLE_USER)
    if role not in ROLES:
        role = ROLE_USER
    return UserRecord(
        login=login,
        role=role,
        password_hash=str(item["password_hash"]),
        must_change_password=bool(item.get("must_change_password")),
        disabled=bool(item.get("disabled")),
        password_changed_at=str(item.get("password_changed_at") or ""),
    )


def _records() -> list[UserRecord]:
    result = []
    for item in _read_store()["users"]:
        record = _to_record(item)
        if record is not None:
            result.append(record)
    return result


def list_users() -> list[UserRecord]:
    """Все пользователи по алфавиту — для CLI и интерфейса управления."""
    with _lock:
        return sorted(_records(), key=lambda u: u.login)


def get_user(login: str) -> UserRecord | None:
    wanted = str(login or "").strip().lower()
    with _lock:
        for record in _records():
            if record.login == wanted:
                return record
    return None


def user_count() -> int:
    return len(_records())


def has_admin() -> bool:
    return any(u.role == ROLE_ADMIN and not u.disabled for u in _records())


def session_secret() -> str:
    """Ключ подписи cookie сессии.

    Приоритет у ``AUTH_SECRET`` из .env. Если его нет, ключ один раз создаётся
    и хранится в users.json — иначе после каждого перезапуска все были бы
    разлогинены.
    """
    if AUTH_SECRET:
        return AUTH_SECRET
    with _lock:
        store = _read_store()
        secret = str(store.get("session_secret") or "")
        if not secret:
            secret = secrets.token_hex(32)
            store["session_secret"] = secret
            store["version"] = USERS_FILE_VERSION
            _write_store(store)
            logger.info("Создан ключ подписи сессий в %s", USERS_FILE)
        return secret


def _save_record(record: UserRecord) -> None:
    """Добавить или заменить пользователя."""
    with _lock:
        store = _read_store()
        users = [u for u in store["users"] if str(u.get("login") or "").lower() != record.login]
        users.append(
            {
                "login": record.login,
                "role": record.role,
                "password_hash": record.password_hash,
                "must_change_password": record.must_change_password,
                "disabled": record.disabled,
                "password_changed_at": record.password_changed_at,
            }
        )
        store["users"] = users
        if not store.get("session_secret"):
            store["session_secret"] = secrets.token_hex(32)
        store["version"] = USERS_FILE_VERSION
        _write_store(store)


def upsert_user(
    login: str,
    password: str,
    *,
    role: str = ROLE_USER,
    must_change_password: bool = True,
    disabled: bool = False,
) -> UserRecord:
    """Создать пользователя или обновить его пароль и роль."""
    key = normalize_login(login)
    if role not in ROLES:
        raise AuthError(f"Роль должна быть одной из: {', '.join(ROLES)}")
    record = UserRecord(
        login=key,
        role=role,
        password_hash=hash_password(password),
        must_change_password=bool(must_change_password),
        disabled=bool(disabled),
        password_changed_at=now_app().isoformat(timespec="milliseconds"),
    )
    _save_record(record)
    logger.info("Сохранён пользователь %s (роль %s)", record.login, record.role)
    return record


def set_password(login: str, password: str, *, must_change_password: bool = False) -> UserRecord:
    """Сменить пароль. Старые сессии этого пользователя сразу перестают работать."""
    record = get_user(login)
    if record is None:
        raise AuthError(f"Пользователь {normalize_login(login)} не найден")
    updated = UserRecord(
        login=record.login,
        role=record.role,
        password_hash=hash_password(password),
        must_change_password=bool(must_change_password),
        disabled=record.disabled,
        password_changed_at=now_app().isoformat(timespec="milliseconds"),
    )
    _save_record(updated)
    logger.info("Пароль изменён: %s", updated.login)
    return updated


def set_role(login: str, role: str) -> UserRecord:
    if role not in ROLES:
        raise AuthError(f"Роль должна быть одной из: {', '.join(ROLES)}")
    record = get_user(login)
    if record is None:
        raise AuthError(f"Пользователь {normalize_login(login)} не найден")
    if record.role == ROLE_ADMIN and role != ROLE_ADMIN and not _other_admins(record.login):
        raise AuthError(
            "Это последний администратор — сначала назначьте администратора другому"
        )
    updated = UserRecord(
        login=record.login,
        role=role,
        password_hash=record.password_hash,
        must_change_password=record.must_change_password,
        disabled=record.disabled,
        password_changed_at=record.password_changed_at,
    )
    _save_record(updated)
    logger.info("Роль изменена: %s -> %s", updated.login, updated.role)
    return updated


def set_disabled(login: str, disabled: bool) -> UserRecord:
    record = get_user(login)
    if record is None:
        raise AuthError(f"Пользователь {normalize_login(login)} не найден")
    if disabled and record.role == ROLE_ADMIN and not _other_admins(record.login):
        raise AuthError(
            "Это последний администратор — сначала назначьте администратора другому"
        )
    updated = UserRecord(
        login=record.login,
        role=record.role,
        password_hash=record.password_hash,
        must_change_password=record.must_change_password,
        disabled=bool(disabled),
        password_changed_at=record.password_changed_at,
    )
    _save_record(updated)
    logger.info("Пользователь %s %s", updated.login, "заблокирован" if disabled else "разблокирован")
    return updated


def delete_user(login: str) -> None:
    key = normalize_login(login)
    record = get_user(key)
    if record is None:
        raise AuthError(f"Пользователь {key} не найден")
    if record.role == ROLE_ADMIN and not _other_admins(key):
        raise AuthError(
            "Это последний администратор — сначала назначьте администратора другому"
        )
    with _lock:
        store = _read_store()
        store["users"] = [u for u in store["users"] if str(u.get("login") or "").lower() != key]
        _write_store(store)
    logger.info("Пользователь удалён: %s", key)


def _other_admins(login: str) -> bool:
    return any(u.role == ROLE_ADMIN and not u.disabled and u.login != login for u in _records())


# ------------------------------------------------------------------- вход и выход

def verify_credentials(login: str, password: str) -> UserRecord | None:
    """Проверка пароля. None — логин неизвестен, неверный пароль или учётка
    заблокирована; наружу эта разница не выдаётся."""
    key = normalize_login(login)
    record = get_user(key)
    if record is None:
        # Ровно та же работа, что и при верном пароле, — иначе по времени
        # ответа определяется, какие логины существуют.
        verify_password(password or "", _dummy_hash())
        return None
    if not verify_password(password or "", record.password_hash):
        return None
    if record.disabled:
        logger.warning("Попытка входа в заблокированную учётку: %s", key)
        return None
    return record


# ------------------------------------------------------------------- сессия

def _serializer():
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(session_secret(), salt=SESSION_SALT)


def issue_token(principal: Principal) -> str:
    return _serializer().dumps(
        {"l": principal.login, "s": principal.session_id, "i": principal.issued_at}
    )


def read_token(token: str) -> Principal | None:
    """Разбор и проверка токена сессии.

    None — токен подделан, просрочен, пользователь удалён или заблокирован,
    либо пароль сменили после выдачи этого токена.
    """
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=AUTH_SESSION_MAX_AGE)
    except Exception:
        # BadSignature, SignatureExpired и любые сбои разбора — недействительный токен.
        return None
    if not isinstance(data, dict):
        return None
    login = str(data.get("l") or "")
    record = get_user(login) if login else None
    if record is None or record.disabled:
        return None
    issued_at = data.get("i")
    if not isinstance(issued_at, (int, float)):
        issued_at = 0.0
    # Смена пароля закрывает все ранее выданные токены. Сравнение идёт по
    # дробным секундам: при точности до целой секунды токен, выданный в ту же
    # секунду, что и смена пароля, попал бы в «не раньше» и пережил бы отзыв.
    if record.password_changed_at:
        try:
            from datetime import datetime

            changed = datetime.fromisoformat(record.password_changed_at).timestamp()
            if issued_at and issued_at < changed:
                return None
        except ValueError:
            pass
    return Principal(
        login=record.login,
        role=record.role,
        session_id=str(data.get("s") or ""),
        issued_at=issued_at,
        must_change_password=record.must_change_password,
    )


def cookie_kwargs() -> dict[str, Any]:
    """Параметры cookie сессии. ``secure`` — по HTTPS, иначе браузер не
    примет cookie с ``secure`` по обычному http."""
    return {
        "max_age": AUTH_SESSION_MAX_AGE,
        "httponly": True,
        "samesite": "lax",
        "secure": AUTH_COOKIE_SECURE,
        "path": "/",
    }


def set_auth_cookie(response, principal: Principal) -> None:
    response.set_cookie(AUTH_COOKIE, issue_token(principal), **cookie_kwargs())


def clear_auth_cookie(response) -> None:
    response.delete_cookie(AUTH_COOKIE, path="/")


# ------------------------------------------------------------ защита от подбора

# Неудачные попытки: ключ «логин + IP» -> время последних попыток (монотонное).
_failures: dict[str, list[float]] = {}
# Ключ -> момент, до которого вход заблокирован (монотонное).
_locked_until: dict[str, float] = {}


def throttle_key(login: str, ip: str) -> str:
    return f"{str(login or '').strip().lower()}|{ip or ''}"


def lockout_remaining(key: str) -> int:
    """Сколько секунд ещё нельзя пробовать (0 — можно)."""
    with _lock:
        deadline = _locked_until.get(key, 0.0)
        if deadline <= time.monotonic():
            return 0
        return max(0, int(round(deadline - time.monotonic())))


def register_failure(key: str) -> int:
    """Запомнить неудачную попытку. Возвращает оставшуюся блокировку."""
    now = time.monotonic()
    with _lock:
        _prune_failures(now)
        attempts = [t for t in _failures.get(key, []) if now - t < AUTH_LOCKOUT_SEC]
        attempts.append(now)
        _failures[key] = attempts
        if len(attempts) >= AUTH_MAX_ATTEMPTS:
            _locked_until[key] = now + AUTH_LOCKOUT_SEC
            attempts = []
            _failures[key] = attempts
            logger.warning("Вход заблокирован на %s с после %s попыток", AUTH_LOCKOUT_SEC, AUTH_MAX_ATTEMPTS)
        return lockout_remaining(key)


def clear_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)
        _locked_until.pop(key, None)


def reset_throttle() -> None:
    """Сбросить счётчики (используется в тестах и при смене пароля)."""
    with _lock:
        _failures.clear()
        _locked_until.clear()


def _prune_failures(now: float) -> None:
    """Выбросить старые записи, иначе словарь растёт с каждого неверного входа."""
    stale = [k for k, ts in _failures.items() if not ts or now - ts[-1] > AUTH_LOCKOUT_SEC]
    for key in stale:
        _failures.pop(key, None)
    for key, deadline in list(_locked_until.items()):
        if deadline <= now:
            _locked_until.pop(key, None)


# ------------------------------------------------------------------ диагностика

def auth_status() -> dict[str, Any]:
    """Состояние входа — для /api/auth/me и диагностических сообщений."""
    return {
        "enabled": AUTH_ENABLED,
        "users": user_count(),
        "admins": sum(1 for u in _records() if u.role == ROLE_ADMIN and not u.disabled),
        "session_max_age": AUTH_SESSION_MAX_AGE,
        "cookie_secure": AUTH_COOKIE_SECURE,
        "users_file": str(USERS_FILE),
    }


def ready() -> bool:
    """Готов ли вход принимать людей."""
    return user_count() > 0
