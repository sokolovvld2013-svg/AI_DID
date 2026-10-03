"""API общих настроек: выбор модели LLM, наименование компании и логотип."""
from __future__ import annotations

import csv
import io
import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from config import AUTH_ENABLED, USER_LOGS_DIR, USER_LOGS_RETENTION_DAYS, USERS_FILE
from core.api_models import api_models, is_offered, offered_models
from core.app_time import now_app
from core.auth import (
    ROLE_ADMIN,
    ROLE_USER,
    ROLES,
    AuthError,
    Principal,
    delete_user,
    get_user,
    list_users,
    set_auth_cookie,
    set_disabled,
    set_password,
    set_role,
    upsert_user,
)
from core.auth_router import require_admin, require_user
from core.settings import (
    PRICES_UPDATED_AT,
    SettingsError,
    canonical_model_id,
    find_model,
    fx_info,
    get_settings,
    logo_info,
    provider_unavailable_reason,
    reset_logo,
    save_logo_bytes,
    update_settings,
)
from core.user_logs import ERROR_CODES, read_logs
from core.user_logs import modules as user_log_modules

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Настройки меняют модель и компанию, логи показывают вопросы пользователей —
# поэтому весь раздел закрыт require_admin (роль admin, см. AUTH_ADMIN_ONLY_SETTINGS).
_admin = Depends(require_admin)


def _require_users_admin(request: Request) -> Principal:
    """Управление пользователями — только для администратора, без исключений.

    В отличие от ``_admin`` этот барьер не подчиняется AUTH_ADMIN_ONLY_SETTINGS.
    Тот флаг открывает обычному пользователю весь раздел настроек, а добавив
    сюда выдачу прав, мы дали бы ему повышение до admin. Поэтому при включённом
    входе проверка строгая; при AUTH_ENABLED=false (режим разработки) маршруты
    и так открыты всем, как и остальное приложение.
    """
    principal = require_user(request)
    if AUTH_ENABLED and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return principal


_users_admin = Depends(_require_users_admin)


class SettingsUpdate(BaseModel):
    company_name: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=64)


def _payload() -> dict:
    settings = get_settings()
    return {
        "company_name": settings["company_name"],
        "model": settings["model"],
        "logo": logo_info(),
        "models": offered_models(),
        "api": api_models(),
        "prices_updated_at": PRICES_UPDATED_AT,
        "fx": fx_info(),
    }


@router.get("", dependencies=[_admin])
async def read_settings():
    return _payload()


@router.get("/models", dependencies=[_admin])
async def read_models(refresh: bool = False):
    status = api_models(force=refresh)
    return {
        "models": offered_models(force=refresh),
        "api": status,
        "prices_updated_at": PRICES_UPDATED_AT,
        "fx": fx_info(),
        "providers": {
            name: {"available": provider_unavailable_reason(name) is None}
            for name in ("deepseek", "gigachat")
        },
    }


@router.post("", dependencies=[_admin])
async def write_settings(payload: SettingsUpdate):
    if payload.model:
        # Псевдонимы (GigaChat-Pro и т. п.) приводим к каноническому ID,
        # иначе проверка искала бы их в списке стенда как отдельные модели.
        canonical = canonical_model_id(payload.model) or payload.model
        if not is_offered(canonical):
            model = find_model(canonical)
            title = model["title"] if model else canonical
            raise HTTPException(
                status_code=400,
                detail=f"Модель {title} недоступна: её нет в списке моделей проекта",
            )
    try:
        update_settings(company_name=payload.company_name, model=payload.model)
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()


@router.post("/logo", dependencies=[_admin])
async def upload_logo(file: UploadFile = File(...)):
    """Замена логотипа в шапке: файл кладётся в static/img/logo_custom.<ext>."""
    try:
        data = await file.read()
        save_logo_bytes(data, source_name=file.filename or "")
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()


@router.delete("/logo", dependencies=[_admin])
async def delete_logo():
    """Возврат исходного логотипа static/img/logo.png."""
    try:
        reset_logo()
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()


# ---------------------------------------------------------- управление пользователями


class UserCreate(BaseModel):
    """Создание пользователя или сброс его пароля вместе с ролью."""

    login: str = Field(min_length=1, max_length=64)
    password: str
    role: str = ROLE_USER
    must_change_password: bool = True


class UserPassword(BaseModel):
    password: str
    must_change_password: bool = True


class UserRole(BaseModel):
    role: str


class UserDisabled(BaseModel):
    disabled: bool


def _users_payload(message: str = "") -> dict:
    """Список пользователей для интерфейса. Паролей здесь нет по построению:
    наружу отдаётся ``UserRecord.public()`` без хеша."""
    records = list_users()
    payload = {
        "users": [record.public() for record in records],
        "roles": list(ROLES),
        "storage_file": str(USERS_FILE),
        "total": len(records),
        "admins": sum(1 for r in records if r.role == ROLE_ADMIN and not r.disabled),
    }
    if message:
        payload["message"] = message
    return payload


def _bad_request(exc: AuthError) -> HTTPException:
    """Ошибка проверки входа: сообщение из core.auth уже понятно пользователю."""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/users", dependencies=[_users_admin])
async def read_users():
    """Кто имеет доступ: логин, роль, состояние, дата смены пароля."""
    return _users_payload()


@router.post("/users", dependencies=[_users_admin])
async def save_user(payload: UserCreate):
    """Создать пользователя или обновить его пароль и роль.

    Один маршрут на оба случая: заводить и перезаводить учётку — одно и то же
    с точки зрения файла, различается только формулировка ответа.
    """
    existed = get_user(payload.login) is not None
    try:
        record = upsert_user(
            payload.login,
            payload.password,
            role=payload.role,
            must_change_password=payload.must_change_password,
        )
    except AuthError as e:
        raise _bad_request(e) from e
    verb = "Обновлён" if existed else "Создан"
    hint = " При первом входе приложение попросит задать свой пароль." if record.must_change_password else ""
    return _users_payload(f"{verb} пользователь {record.login}.{hint}")


@router.post("/users/{login}/password", dependencies=[_users_admin])
async def reset_user_password(request: Request, login: str, payload: UserPassword):
    """Сбросить пароль пользователя.

    Пароль меняется датой, поэтому все ранее выданные этому пользователю сессии
    перестают работать. Сеанс самого администратора перевыпускаем — иначе он
    вылетел бы из системы собственным действием, и это выглядело бы как ошибка.
    """
    try:
        record = set_password(
            login, payload.password, must_change_password=payload.must_change_password
        )
    except AuthError as e:
        raise _bad_request(e) from e
    principal = getattr(request.state, "user", None)
    if principal is not None and record.login == principal.login:
        response = JSONResponse(_users_payload(f"Пароль {record.login} изменён."))
        set_auth_cookie(
            response,
            Principal(
                login=record.login,
                role=record.role,
                session_id=principal.session_id,
                issued_at=time.time(),
                must_change_password=record.must_change_password,
            ),
        )
        return response
    return _users_payload(f"Пароль {record.login} изменён, прежние сессии закрыты.")


@router.post("/users/{login}/role", dependencies=[_users_admin])
async def change_user_role(login: str, payload: UserRole):
    try:
        record = set_role(login, payload.role)
    except AuthError as e:
        raise _bad_request(e) from e
    return _users_payload(f"Роль {record.login}: {record.role}.")


@router.post("/users/{login}/disable", dependencies=[_users_admin])
async def block_user(login: str, payload: UserDisabled):
    """Заблокировать или разблокировать. Учётка остаётся, история логов сохраняется."""
    try:
        record = set_disabled(login, payload.disabled)
    except AuthError as e:
        raise _bad_request(e) from e
    state = "заблокирован" if record.disabled else "разблокирован"
    return _users_payload(f"Пользователь {record.login} {state}.")


@router.delete("/users/{login}", dependencies=[_users_admin])
async def remove_user(login: str):
    try:
        delete_user(login)
    except AuthError as e:
        raise _bad_request(e) from e
    return _users_payload(f"Пользователь {login} удалён.")


@router.get("/logs", dependencies=[_admin])
async def read_user_logs(
    limit: int | None = None,
    module: str = "",
    status: str = "",
    days: int | None = None,
):
    """Логи пользователей за окно хранения (по умолчанию 30 дней), свежие сверху.

    ``limit`` не передан (или ``0``) — отдать все записи окна хранения; это
    вариант «все» в интерфейсе. Само значение сверху ограничивает окно хранения.
    """
    if not limit:
        limit = None
    return {
        "logs": read_logs(limit=limit, module=module, status=status, days=days),
        "retention_days": USER_LOGS_RETENTION_DAYS,
        "storage_dir": str(USER_LOGS_DIR),
        "modules": user_log_modules(),
        "error_codes": ERROR_CODES,
    }


@router.get("/logs.csv", dependencies=[_admin])
async def download_user_logs(days: int | None = None):
    """Выгрузка логов в CSV для Excel."""
    rows = read_logs(limit=100000, days=days)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["Дата и время", "Логин", "IP-адрес", "Модуль", "Вопрос", "Токены", "Статус", "Код ошибки", "Ошибка"]
    )
    for row in rows:
        code = row.get("error_code", "")
        writer.writerow(
            [
                row.get("ts", ""),
                row.get("login", ""),
                row.get("ip", ""),
                row.get("module", ""),
                row.get("question", ""),
                row.get("tokens", 0),
                row.get("status", ""),
                code,
                ERROR_CODES.get(code, ""),
            ]
        )
    filename = f"user-logs-{now_app().strftime('%Y-%m-%d')}.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),  # BOM — чтобы Excel понял кириллицу
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
