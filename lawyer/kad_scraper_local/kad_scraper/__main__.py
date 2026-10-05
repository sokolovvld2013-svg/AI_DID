"""Запуск сбора из командной строки.

Нужен ровно в двух случаях: для разовой выгрузки без сервера и для проверки
окружения перед подключением агента. Логика обхода здесь не дублируется —
она целиком в :func:`kad_scraper.runner.run_collection`, тем же кодом
работает и агент.

Примеры:

    python -m kad_scraper --inn 5032034971
    python -m kad_scraper --inn 5032034971 --limit 50 --enrich
    python -m kad_scraper --inn 5032034971 --all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import protocol
from .config import DEFAULT_INN
from .runner import run_collection

#: Коды возврата. Ноль — сбор состоялся, даже если неполный: неполнота из-за
#: капчи это результат, а не сбой, и в CI по нему не надо ронять шаг.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BAD_ARGS = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kad_scraper",
        description="Сбор дел по ИНН из картотеки арбитражных дел (kad.arbitr.ru).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--inn", default=DEFAULT_INN, help=f"ИНН или ОГРН контрагента (по умолчанию {DEFAULT_INN})")
    parser.add_argument("--limit", type=int, default=None, help="сколько записей собрать (по умолчанию без ограничения)")
    parser.add_argument("--all", action="store_true", help="обойти всю выборку, то же что --limit без значения")
    parser.add_argument("--enrich", action="store_true", help="открыть карточку каждого дела (медленно: 5-10 с на дело)")
    parser.add_argument("--headless", action="store_true", help="скрытый браузер, капчи при этом чаще")
    parser.add_argument("--output", default="output", help="папка для чекпойнта и Excel (по умолчанию output)")
    parser.add_argument("--json", dest="json_out", default=None, help="дополнительно выписать отчёт в JSON-файл")
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    return parser


def _print_progress(progress) -> None:
    if progress.phase == "Сбор данных":
        print(f"[{progress.page}/{progress.total_pages or '?'}] {progress.message}", flush=True)
    elif progress.phase == "Обогащение карточек":
        print(f"карточка {progress.enriched}/{progress.enrich_total}: {progress.message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    inn = str(args.inn).strip()
    if not inn.isdigit():
        print(f"ИНН должен состоять из цифр, получено {inn!r}", file=sys.stderr)
        return EXIT_BAD_ARGS

    limit = None if args.all else args.limit
    print(f"Сбор по ИНН {inn}: лимит={limit or 'без ограничения'}, обогащение={args.enrich}")

    report = run_collection(
        inn=inn,
        limit=limit,
        enrich=args.enrich,
        headless=args.headless,
        output_dir=Path(args.output),
        on_progress=_print_progress,
    )

    print("\n=== ИТОГ ===")
    print(f"записей:   {report['records_count']}")
    print(f"страниц:   {report['pages_visited']} из {report['total_cases'] or '?'} дел")
    print(f"статус:    {protocol.FINISH_LABELS.get(report['status'], report['status'])}")
    print(f"остановка: {report['stop_reason']}")
    print(f"капч:      {report['captcha_hits']}")
    print(f"время:     {report['elapsed']:.0f} с")
    for warning in report["warnings"]:
        print(f"  ! {warning}")
    if report["result_path"]:
        print(f"Excel:     {report['result_path']}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON:      {args.json_out}")

    return EXIT_OK if report["status"] in protocol.USABLE_STATUSES else EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())