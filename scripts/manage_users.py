"""Управление пользователями входа: add, passwd, role, disable, rm, list.

Запуск из корня проекта:

    python -m scripts.manage_users add ivanova --admin
    python -m scripts.manage_users list
    python -m scripts.manage_users passwd ivanova
    python -m scripts.manage_users role ivanova user
    python -m scripts.manage_users disable ivanova
    python -m scripts.manage_users rm ivanova

Прямой запуск файлом тоже допустим — `python scripts/manage_users.py list`.

Пароль вводится скрыто. Без ``--password`` он спрашивается дважды, чтобы
опечатку можно было заметить. Ключ ответа не печатается, в users.json лежит
только argon2id-хеш.
"""
from __future__ import annotations

import argparse
import getpass
import pathlib
import sys

# При прямом запуске `python scripts/manage_users.py` в sys.path попадает только
# каталог scripts/, и пакеты проекта не видны: ModuleNotFoundError: No module
# named 'core'. Модульный запуск (`python -m scripts.manage_users`) добавляет корень
# сам, поэтому путь дописываем только в первом случае.
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.auth import (
    ROLE_ADMIN,
    ROLE_USER,
    AuthError,
    delete_user,
    get_user,
    list_users,
    session_secret,
    set_disabled,
    set_password,
    set_role,
    upsert_user,
)
from config import AUTH_ENABLED, USERS_FILE


def _ask_password(prompt: str = "Пароль: ") -> str:
    return getpass.getpass(prompt)


def _password_from_args(args: argparse.Namespace, *, confirm: bool = True) -> str:
    if args.password is not None:
        return args.password
    password = _ask_password()
    if confirm and password != _ask_password("Ещё раз: "):
        raise AuthError("Пароли не совпадают")
    return password


def _cmd_add(args: argparse.Namespace) -> int:
    role = ROLE_ADMIN if args.admin else ROLE_USER
    existing = get_user(args.login)
    password = _password_from_args(args, confirm=existing is None)
    record = upsert_user(
        args.login,
        password,
        role=role,
        must_change_password=not args.no_change,
        disabled=False,
    )
    action = "Обновлён" if existing else "Создан"
    print(f"{action} пользователь {record.login} (роль {record.role})")
    if record.must_change_password:
        print("При первом входе приложение попросит задать свой пароль.")
    if not AUTH_ENABLED:
        print("Внимание: AUTH_ENABLED=false — вход выключен, пользователь пока не используется.")
    return 0


def _cmd_passwd(args: argparse.Namespace) -> int:
    password = _password_from_args(args)
    record = set_password(
        args.login, password, must_change_password=args.require_change
    )
    print(f"Пароль изменён: {record.login}. Ранее выданные сессии больше не работают.")
    return 0


def _cmd_role(args: argparse.Namespace) -> int:
    record = set_role(args.login, args.role)
    print(f"Роль {record.login}: {record.role}")
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    record = set_disabled(args.login, not args.enable)
    state = "разблокирован" if args.enable else "заблокирован"
    print(f"Пользователь {record.login} {state}")
    return 0


def _cmd_rm(args: argparse.Namespace) -> int:
    delete_user(args.login)
    print(f"Пользователь {args.login} удалён")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    users = list_users()
    if not users:
        print(f"Пользователей нет ({USERS_FILE})")
        print("Создайте администратора: python -m scripts.manage_users add <логин> --admin")
        return 0
    print(f"{'Логин':<24} {'Роль':<7} {'Состояние':<14} Пароль изменён")
    print("-" * 72)
    for user in users:
        if user.disabled:
            state = "заблокирован"
        elif user.must_change_password:
            state = "временный"
        else:
            state = "активен"
        print(
            f"{user.login:<24} {user.role:<7} {state:<14} "
            f"{user.password_changed_at or '—'}"
        )
    print(f"\nВсего: {len(users)}. Файл: {USERS_FILE}")
    return 0


def _cmd_secret(args: argparse.Namespace) -> int:
    """Показать, каким ключом подписываются сессии (нужно при переносе на
    другой сервер, чтобы не разлогинивать всех)."""
    print(f"Ключ подписи сессий: {session_secret()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.manage_users",
        description="Пользователи входа по логину и паролю.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="создать или обновить пользователя")
    add.add_argument("login")
    add.add_argument("--admin", action="store_true", help="роль admin (настройки и логи)")
    add.add_argument("--password", help="пароль; без него спрашивается скрыто")
    add.add_argument(
        "--no-change",
        action="store_true",
        help="не требовать смену пароля при первом входе",
    )
    add.set_defaults(func=_cmd_add)

    passwd = sub.add_parser("passwd", help="сменить пароль")
    passwd.add_argument("login")
    passwd.add_argument("--password")
    passwd.add_argument(
        "--require-change", action="store_true", help="потребовать смену при входе"
    )
    passwd.set_defaults(func=_cmd_passwd)

    role = sub.add_parser("role", help="назначить роль")
    role.add_argument("login")
    role.add_argument("role", choices=(ROLE_USER, ROLE_ADMIN))
    role.set_defaults(func=_cmd_role)

    disable = sub.add_parser("disable", help="заблокировать или разблокировать")
    disable.add_argument("login")
    disable.add_argument("--enable", action="store_true", help="разблокировать")
    disable.set_defaults(func=_cmd_disable)

    remove = sub.add_parser("rm", help="удалить пользователя")
    remove.add_argument("login")
    remove.set_defaults(func=_cmd_rm)

    listing = sub.add_parser("list", help="показать пользователей")
    listing.set_defaults(func=_cmd_list)

    secret = sub.add_parser("secret", help="показать ключ подписи сессий")
    secret.set_defaults(func=_cmd_secret)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AuthError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
