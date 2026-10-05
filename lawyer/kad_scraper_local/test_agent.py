"""Проверка агента, не требующая сервера.

Раньше здесь поднимался отдельный сервер (``server.app``) и агент прогонялся
против него по HTTP. Сервер переехал в приложение «Юрист»
(``lawyer/arbitr``), поэтому сквозная проверка протокола живёт там, в
``tests/test_arbitr_jobs.py``, а здесь осталось то, что проверяет самого агента и
не зависит от сервера: отбрасывание мусорного ИНН без запуска браузера и
устойчивость к обрыву связи.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

os.environ.setdefault("KAD_AGENT_TOKEN", "agent-test-token")

from kad_scraper import agent as agent_mod  # noqa: E402
from kad_scraper.agent import AgentClient, run_loop  # noqa: E402
from kad_scraper.exporter import records_to_dataframe, records_to_excel  # noqa: E402
from kad_scraper.models import (  # noqa: E402
    COLUMNS,
    CaseRecord,
    build_deep_status,
    latest_event,
)
from kad_scraper.protocol import make_result  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

passed = failed = 0


def fake_collection(*, inn, limit=None, enrich=False, headless=False,
                    output_dir=None, on_progress=None, seed=None) -> dict:
    """Подмена обхода КАД: браузер не открывается, отчёт выглядит настоящим."""
    records = [
        {
            "number": "А40-12345/2024",
            "court_name": "Арбитражный суд города Москвы",
            "case_date": "2024-05-01",
            "plaintiffs": [{"name": "ООО Ромашка", "inn": "7701234567"}],
            "respondents": [{"name": "ПАО Ромашка", "inn": "7707654321"}],
            "third_parties": [{"name": "ИП Иванов", "inn": ""}],
            "others": [], "instance": "А40-12345/2024", "enriched": True, "warnings": [],
        },
        {
            "number": "А40-12346/2024",
            "court_name": "Арбитражный суд города Москвы",
            "case_date": "2024-06-11",
            "plaintiffs": [{"name": "ООО Ромашка", "inn": "7701234567"}],
            "respondents": [{"name": "ООО Ромашка-2", "inn": "7709876543"}],
            "third_parties": [], "others": [], "instance": "", "enriched": True,
            "warnings": [],
        },
    ]
    if on_progress:
        on_progress(type("P", (), {
            "phase": "Сбор данных", "page": 1, "total_pages": 1,
            "message": "2 дела", "enriched": 0, "enrich_total": 0,
        })())
    return make_result(
        job_id="", agent="ivan-pc", status="ok", records=records,
        stop_reason="last_page", pages_visited=1, total_cases=2, elapsed=0.4,
    )


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [ок]   {name}")
    else:
        failed += 1
        print(f"  [СБОЙ] {name} {detail}")


def main() -> int:
    print("\n1. Мусорный ИНН агент отбрасывает, не открывая браузер")
    sent: dict[str, object] = {}

    class Recorder:
        def send_result(self, report, *, xlsx):
            sent["report"] = report

    agent_mod.run_collection = lambda **kw: (_ for _ in ()).throw(
        AssertionError("сборщик не должен запускаться при неверном ИНН")
    )
    agent_mod.handle_job(
        Recorder(),
        {"id": "job-мусор", "inn": "не-цифры", "note": "", "limit": None,
         "enrich": False, "headless": False},
        agent="ivan-pc",
        output_dir=Path("output_agent_test"),
    )
    report = sent.get("report") or {}
    check("мусорный ИНН отклонён без сбора", report.get("status") == "error", str(report.get("status")))
    check("причина объяснена", "ИНН" in (report.get("error") or ""), str(report.get("error")))

    print("\n2. Отчёт сохраняет ИНН истцов и ответчиков")
    agent_mod.run_collection = fake_collection
    result = fake_collection(inn="7707083893")
    plaintiffs = {p["inn"] for r in result["records"] for p in r["plaintiffs"]}
    respondents = {p["inn"] for r in result["records"] for p in r["respondents"]}
    check("ИНН истца на месте", plaintiffs == {"7701234567"}, str(plaintiffs))
    check("ИНН ответчиков на месте", respondents == {"7707654321", "7709876543"}, str(respondents))
    check("третье лицо сохранено",
          result["records"][0]["third_parties"][0]["name"] == "ИП Иванов")

    print("\n3. Обрыв связи не роняет агента")
    offline = AgentClient("http://127.0.0.1:1", "test-token")
    check("пинг недоступного сервера не проходит", offline.ping() is False)
    code = run_loop(offline, agent="ivan-pc", output_dir="output_agent_test", once=True)
    check("агент сообщил о проблеме кодом 2", code == 2, str(code))

    print("\n4. Колонки карточки: инстанция, сумма иска, статус по «Карточкам»")
    check("колонка инстанции есть в выгрузке",
          "Номер дела по инстанции" in COLUMNS)
    check("колонка суммы иска есть в выгрузке",
          "Сумма иска" in COLUMNS)
    check("колонки инстанции и суммы идут до статуса",
          COLUMNS.index("Номер дела по инстанции") < COLUMNS.index("Статус")
          and COLUMNS.index("Сумма иска") < COLUMNS.index("Статус"),
          str(COLUMNS))

    # Реальный разбор карточки дела 09АП-46507/2026: две инстанции, у
    # апелляционной есть текст акта и ссылка на документ.
    record = CaseRecord(
        url="https://kad.arbitr.ru/Card/38f30802-7a13-4e4f-84ce-0e738ab76d22",
        case_number="А40-120838/2026",
        reg_date="11.08.2026",
        instance_level="А40-120838/2026",
        claim_amount="1 234 567,89 руб.",
        case_events=[
            {
                "stage": "Апелляционная инстанция",
                "date": "29.09.2026",
                "instance_number": "09АП-46507/2026",
                "court": "9 арбитражный апелляционный суд",
                "result": (
                    "Оставить без изменения Решение; Оставить без изменения решение, "
                    "а апелляционную жалобу - без удовлетворения (п.1 ст.269 АПК)"
                ),
                "result_url": "https://kad.arbitr.ru/Kad/PdfDocument/38f30802/postanovlenie.pdf",
            },
            {
                "stage": "Первая инстанция",
                "date": "11.08.2026",
                "instance_number": "А40-120838/2026",
                "court": "АС города Москвы",
                "result": "Мотивированное решение по делу",
                "result_url": "https://kad.arbitr.ru/Kad/PdfDocument/38f30802/reshenie.pdf",
            },
        ],
    )
    record.status = build_deep_status(
        case_events=record.case_events,
        instance_desc="Рассмотрение дела завершено",
        status_details="Рассмотрение дела завершено",
        next_date="",
        duration="5 месяцев",
        category="Гражданские дела",
        reg_date="11.08.2026",
    )
    expected_status = (
        "09АП-46507/2026 9 арбитражный апелляционный суд. Оставить без изменения "
        "Решение; Оставить без изменения решение, а апелляционную жалобу - "
        "без удовлетворения (п.1 ст.269 АПК)."
    )
    check("статус взят из последнего акта дела", record.status == expected_status,
          record.status)

    event = latest_event(record.case_events)
    record.status_url = str((event or {}).get("result_url") or "")
    row = record.as_row()
    check("номер инстанции попал в строку",
          row["Номер дела по инстанции"] == "А40-120838/2026",
          str(row["Номер дела по инстанции"]))
    check("сумма иска попала в строку",
          row["Сумма иска"] == "1 234 567,89 руб.", str(row["Сумма иска"]))
    check("статус в строке — тот же текст",
          row["Статус"] == expected_status, str(row["Статус"]))

    restored = CaseRecord.from_saved(record.as_dict())
    check("сумма иска переживает чекпойнт",
          restored is not None and restored.claim_amount == record.claim_amount,
          str(restored.claim_amount if restored else None))
    check("хронология переживает чекпойнт",
          restored is not None and restored.case_events == record.case_events,
          str(restored.case_events if restored else None))
    check("ссылка на документ переживает чекпойнт",
          restored is not None and restored.status_url == record.status_url,
          str(restored.status_url if restored else None))

    workbook = load_workbook(io.BytesIO(records_to_excel([record])))
    sheet = workbook[workbook.sheetnames[0]]
    status_header = [cell.value for cell in sheet[1]].index("Статус") + 1
    status_cell = sheet.cell(row=2, column=status_header)
    check("в Excel статус — гиперссылка на документ",
          status_cell.hyperlink is not None
          and status_cell.hyperlink.target == record.status_url,
          str(status_cell.hyperlink.target if status_cell.hyperlink else None))
    check("текст статуса в Excel не потерялся",
          status_cell.value == expected_status, str(status_cell.value))

    frame = records_to_dataframe([record])
    check("DataFrame содержит обе новые колонки",
          "Номер дела по инстанции" in frame.columns
          and "Сумма иска" in frame.columns,
          str(list(frame.columns)))
    check("значения попали в DataFrame",
          frame.loc[0, "Сумма иска"] == "1 234 567,89 руб.",
          str(frame.loc[0, "Сумма иска"]))

    # Дело 0c83f2c3: хронология без судебного акта, зато назначено заседание.
    # Текст взят с карточки дословно, вместе с пробелом перед запятой в
    # «09:30 , зал» — это набор КАД, и приводить его к нашему виду нельзя.
    hearing_record = CaseRecord(
        url="https://kad.arbitr.ru/Card/0c83f2c3-8375-4335-8ed6-b7c124bb5842",
        case_number="А60-43345/2026",
        reg_date="10.07.2026",
        case_events=[
            {
                "stage": "Первая инстанция",
                "date": "",
                "instance_number": "А60-43345/2026",
                "court": "АС Свердловской области",
                "result": "",
                "result_url": "",
                "next_hearing": "Следующее заседание: 21.10.2026, 09:30 , зал № 306",
            },
        ],
    )
    hearing_record.status = build_deep_status(
        case_events=hearing_record.case_events,
        instance_desc="Рассматривается в первой инстанции",
        status_details="Рассматривается в первой инстанции",
        next_date="10 июля 2026",
        duration="2 месяца 25 дней",
        category="Гражданские дела",
        reg_date="10.07.2026",
    )
    expected_hearing_status = (
        "А60-43345/2026 АС Свердловской области. "
        "Следующее заседание: 21.10.2026, 09:30 , зал № 306"
    )
    check("статус без судебного акта берёт номер инстанции",
          hearing_record.status == expected_hearing_status,
          hearing_record.status)

    # Заседание может быть назначено не в той инстанции, чей акт стал
    # статусом, — тогда оно всё равно дописывается.
    mixed = [
        {
            "stage": "Апелляционная инстанция",
            "date": "29.09.2026",
            "instance_number": "09АП-46507/2026",
            "court": "9 арбитражный апелляционный суд",
            "result": "Оставить без изменения Решение",
            "result_url": "https://kad.arbitr.ru/Kad/PdfDocument/postanovlenie.pdf",
            "next_hearing": "",
        },
        {
            "stage": "Первая инстанция",
            "date": "11.08.2026",
            "instance_number": "А40-120838/2026",
            "court": "АС города Москвы",
            "result": "Решение",
            "result_url": "",
            "next_hearing": "Следующее заседание: 05.11.2026, 10:00 , зал № 12",
        },
    ]
    mixed_status = build_deep_status(
        case_events=mixed,
        instance_desc="",
        status_details="",
        next_date="",
        duration="",
        category="",
        reg_date="11.08.2026",
    )
    check("заседание из другой инстанции дописывается к статусу",
          mixed_status.endswith("Следующее заседание: 05.11.2026, 10:00 , зал № 12"),
          mixed_status)
    check("статус с заседанием не потерял текст акта",
          mixed_status.startswith(
              "09АП-46507/2026 9 арбитражный апелляционный суд. "
              "Оставить без изменения Решение."
          ),
          mixed_status)

    check("заседание без статуса не теряется",
          build_deep_status(
              case_events=[
                  {"next_hearing": "Следующее заседание: 21.10.2026, 09:30 , зал № 306"},
              ],
              instance_desc="",
              status_details="",
              next_date="",
              duration="",
              category="",
              reg_date="",
          ) == "Следующее заседание: 21.10.2026, 09:30 , зал № 306",
          build_deep_status(
              case_events=[
                  {"next_hearing": "Следующее заседание: 21.10.2026, 09:30 , зал № 306"},
              ],
              instance_desc="",
              status_details="",
              next_date="",
              duration="",
              category="",
              reg_date="",
          ))

    saved_hearing = CaseRecord.from_saved(hearing_record.as_dict())
    check("заседание переживает чекпойнт",
          saved_hearing is not None
          and saved_hearing.case_events == hearing_record.case_events,
          str(saved_hearing.case_events if saved_hearing else None))

    print(f"\n=== ИТОГ: успешно {passed}, сбоев {failed} ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())