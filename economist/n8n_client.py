"""Отправка запросов чата Экономиста в n8n webhook."""
import html as html_module
import json
import logging
import re
from typing import Any

import httpx
from config import (
    N8N_ECONOMIST_TIMEOUT,
    N8N_ECONOMIST_WEBHOOK_METHOD,
    N8N_ECONOMIST_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)


def is_test_webhook_url(url: str) -> bool:
    return "/webhook-test/" in (url or "")


def _format_n8n_error(
    status_code: int,
    body: str,
    url: str,
    *,
    method: str,
) -> str:
    """Понятное сообщение для пользователя (без сырого JSON n8n)."""
    hint = ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            msg = str(data.get("message") or "").strip()
            n8n_hint = str(data.get("hint") or "").strip()
            if msg:
                hint = msg
            if n8n_hint:
                hint = f"{hint} {n8n_hint}".strip() if hint else n8n_hint
    except json.JSONDecodeError:
        hint = body.strip()[:200]

    hint_lower = hint.lower()

    if status_code == 404 and "not registered for post" in hint_lower:
        return (
            "Webhook n8n настроен на GET, а приложение отправляет POST. "
            "В n8n откройте узел Webhook → HTTP Method → POST, сохраните и включите workflow (Active). "
            "Либо в .env укажите N8N_ECONOMIST_WEBHOOK_METHOD=GET."
        )

    if status_code == 404 and "not registered for get" in hint_lower:
        return (
            "Webhook n8n настроен на POST, а приложение отправляет GET. "
            "В .env укажите N8N_ECONOMIST_WEBHOOK_METHOD=POST или измените метод в узле Webhook в n8n."
        )

    if status_code == 404:
        parts = ["Webhook n8n не найден (404)."]
        if is_test_webhook_url(url):
            parts.append(
                "В .env указан Test URL (/webhook-test/…). "
                "Для постоянной работы замените на Production URL (/webhook/…) "
                "из узла Webhook в n8n."
            )
            parts.append(
                "Test URL работает только после «Execute workflow» в редакторе "
                "и принимает один запрос."
            )
        else:
            parts.append(
                f"Проверьте: workflow включён (Active), URL из Production URL, "
                f"HTTP Method в n8n = {method.upper()}."
            )
        if hint:
            parts.append(hint)
        return " ".join(parts)

    if status_code in (401, 403):
        return f"n8n отклонил запрос ({status_code}). Проверьте авторизацию webhook."

    if "unused respond to webhook" in hint_lower:
        return (
            "Ошибка настройки workflow в n8n (500): лишний или неиспользуемый узел "
            "«Respond to Webhook». "
            "Оставьте один такой узел в конце цепочки: Webhook → … → Respond to Webhook. "
            "Удалите дубликаты и отключённые ветки. "
            "В узле Webhook: Response Mode = «Using Respond to Webhook Node»."
        )

    if status_code == 500 and "respond to webhook" in hint_lower:
        return (
            f"Ошибка workflow n8n (500): {hint}. "
            "Проверьте узел Webhook (Response Mode → Respond to Webhook) "
            "и один финальный узел Respond to Webhook с JSON {\"answer\": \"...\"}."
        )

    if hint:
        return f"n8n вернул {status_code}: {hint}"
    return f"n8n вернул {status_code}"


def _safe_url_for_log(url: str) -> str:
    """URL без лишних деталей для лога."""
    return re.sub(r"(webhook(?:-test)?/)[^/\s]+", r"\1…", url or "")


def _fmt_value(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value).is_integer():
            return f"{int(value):,}".replace(",", "\u00a0")
        return f"{value:,.2f}".replace(",", "\u00a0")
    return str(value).strip()


_ARTICLE_FIELD_ORDER = (
    "Приоритет",
    "Код статьи",
    "Наименование статьи",
    "Годовой лимит",
    "Текущий период",
    "Текущий лимит",
    "Факт",
)

_ITEM_FIELD_ORDER = (
    "Код статьи",
    "Наименование статьи",
    "Годовой лимит",
    "Текущий период",
    "Текущий лимит",
)

_TEXT_COLUMNS = {
    "Приоритет",
    "Код статьи",
    "Наименование статьи",
    "Текущий период",
    "Раздел",
}


def _norm_key(key: str) -> str:
    return str(key).replace("\ufeff", "").strip()


def _looks_like_article_record(data: dict) -> bool:
    keys = {_norm_key(k) for k in data.keys()}
    markers = {"Код статьи", "Наименование статьи", "Факт", "Годовой лимит", "Текущий лимит"}
    return len(keys & markers) >= 2


def _scalarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Только плоские поля — без вложенных output/массивов."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            continue
        if value is None:
            continue
        out[_norm_key(key)] = value
    return out


def _walk_n8n_records(node: Any, found: list[dict[str, Any]], depth: int = 0) -> None:
    """Обход ответа n8n: output — объект или массив статей."""
    if depth > 12:
        return
    if isinstance(node, str):
        s = node.strip()
        if s.startswith(("[", "{")):
            try:
                _walk_n8n_records(json.loads(s), found, depth + 1)
            except json.JSONDecodeError:
                pass
        return
    if isinstance(node, list):
        for item in node:
            _walk_n8n_records(item, found, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if _looks_like_article_record(node):
        flat = _scalarize_record(node)
        if flat:
            found.append(flat)
        return
    for wrap_key in ("output", "json", "data", "result", "body", "answer", "response"):
        if wrap_key in node:
            _walk_n8n_records(node[wrap_key], found, depth + 1)
    if not found:
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk_n8n_records(value, found, depth + 1)


def _is_object_report(node: dict) -> bool:
    for key in node:
        if _norm_key(key) == "Статьи доходов и расходов" and isinstance(node[key], dict):
            return True
    return False


def _find_object_report(data: Any, depth: int = 0) -> dict[str, Any] | None:
    """Отчёт по объекту: output → Объект + Статьи доходов и расходов."""
    if depth > 12:
        return None
    if isinstance(data, str):
        s = data.strip()
        if s.startswith(("[", "{")):
            try:
                return _find_object_report(json.loads(s), depth + 1)
            except json.JSONDecodeError:
                return None
        return None
    if isinstance(data, list):
        for item in data:
            found = _find_object_report(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    if _is_object_report(data):
        return data
    for wrap_key in ("output", "json", "data", "result", "body", "answer", "response"):
        if wrap_key in data:
            found = _find_object_report(data[wrap_key], depth + 1)
            if found:
                return found
    return None


def _dict_value_by_norm_key(data: dict[str, Any], target: str) -> Any:
    for key, value in data.items():
        if _norm_key(key) == target:
            return value
    return None


def _parse_object_report(output: dict[str, Any]) -> dict[str, Any]:
    object_name = str(_dict_value_by_norm_key(output, "Объект") or "").strip()
    articles_block = _dict_value_by_norm_key(output, "Статьи доходов и расходов")
    if not isinstance(articles_block, dict):
        return {}

    sections: list[dict[str, Any]] = []
    for section_title in ("Доход", "Расход"):
        section_data = articles_block.get(section_title)
        if not isinstance(section_data, dict):
            continue

        rows: list[dict[str, Any]] = []
        items = section_data.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    flat = _scalarize_record(item)
                    if flat:
                        rows.append(flat)

        total_label = "Всего доходов" if section_title == "Доход" else "Всего расходов"
        total_raw = section_data.get(total_label)
        total_row = _scalarize_record(total_raw) if isinstance(total_raw, dict) else None

        sections.append(
            {
                "title": section_title,
                "rows": rows,
                "total_label": total_label,
                "total": total_row,
            }
        )

    summary = None
    fin = articles_block.get("Финансовый результат")
    if isinstance(fin, dict):
        values = _scalarize_record(fin)
        if values:
            summary = {"title": "Финансовый результат", "values": values}

    return {"object": object_name, "sections": sections, "summary": summary}


def _format_object_report_text(report: dict[str, Any]) -> str:
    parts: list[str] = []
    if report.get("object"):
        parts.append(f"Объект: {report['object']}")

    for section in report.get("sections", []):
        parts.append(str(section.get("title") or ""))
        for row in section.get("rows", []):
            parts.append(_format_article_record(row))
        total = section.get("total")
        if total:
            label = section.get("total_label") or "Итого"
            parts.append(f"{label}: {_format_article_record(total)}")

    summary = report.get("summary")
    if summary and summary.get("values"):
        parts.append(
            f"{summary.get('title', 'Итого')}: "
            f"{_format_article_record(summary['values'])}"
        )

    return "\n\n".join(p for p in parts if p.strip())


def _flatten_report_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in report.get("sections", []):
        title = str(section.get("title") or "")
        for row in section.get("rows", []):
            rec = dict(row)
            rec["Раздел"] = title
            out.append(rec)
        if section.get("total"):
            rec = dict(section["total"])
            rec["Раздел"] = str(section.get("total_label") or title)
            out.append(rec)
    summary = report.get("summary")
    if summary and summary.get("values"):
        rec = dict(summary["values"])
        rec["Раздел"] = str(summary.get("title") or "Итого")
        out.append(rec)
    return out


def _cell_is_empty(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, (dict, list)):
        return True
    if isinstance(val, str) and not val.strip():
        return True
    return False


def _column_has_data(col: str, records: list[dict[str, Any]]) -> bool:
    return any(not _cell_is_empty(rec.get(col)) for rec in records)


def _show_remainder_column(records: list[dict[str, Any]]) -> bool:
    return any(
        not _cell_is_empty(rec.get("Текущий лимит"))
        and not _cell_is_empty(rec.get("Факт"))
        for rec in records
    )


def _collect_columns(
    records: list[dict[str, Any]],
    preferred: tuple[str, ...],
) -> list[str]:
    cols: list[str] = list(preferred)
    for rec in records:
        for key in rec:
            if key not in cols and not isinstance(rec[key], (dict, list)):
                cols.append(key)
    return [c for c in cols if _column_has_data(c, records)]


def _render_table_row(
    rec: dict[str, Any],
    cols: list[str],
    *,
    text_cols: set[str],
    show_remainder: bool,
    row_class: str = "",
) -> str:
    cells: list[str] = []
    for col in cols:
        val = rec.get(col, "")
        if isinstance(val, (dict, list)):
            display = "—"
        elif col in text_cols:
            display = "—" if val in (None, "") else str(val)
        else:
            display = "—" if val in (None, "") else _fmt_value(val)
        cells.append(f"<td>{html_module.escape(display)}</td>")

    if show_remainder:
        remainder = "—"
        try:
            if "Текущий лимит" in rec and "Факт" in rec:
                remainder = _fmt_value(float(rec["Текущий лимит"]) - float(rec["Факт"]))
        except (TypeError, ValueError):
            pass
        cells.append(f"<td>{html_module.escape(remainder)}</td>")

    cls = f' class="{row_class}"' if row_class else ""
    return f"<tr{cls}>{''.join(cells)}</tr>"


def _render_data_table(
    records: list[dict[str, Any]],
    *,
    preferred_cols: tuple[str, ...] = _ARTICLE_FIELD_ORDER,
    total_label: str | None = None,
    total_row: dict[str, Any] | None = None,
    table_class: str = "economist-table",
) -> str:
    if not records and not total_row:
        return ""

    all_records = list(records)
    if total_row and total_label:
        summary_row = dict(total_row)
        summary_row.setdefault("Код статьи", "")
        summary_row["Наименование статьи"] = total_label
        all_records = [*records, summary_row]

    cols = _collect_columns(all_records, preferred_cols)
    if not cols:
        return ""

    show_remainder = _show_remainder_column(records)
    rows = [
        _render_table_row(
            rec,
            cols,
            text_cols=_TEXT_COLUMNS,
            show_remainder=show_remainder,
            row_class="economist-table-total" if total_row and idx == len(all_records) - 1 else "",
        )
        for idx, rec in enumerate(all_records)
    ]

    header = "".join(f"<th>{html_module.escape(c)}</th>" for c in cols)
    if show_remainder:
        header += "<th>Остаток</th>"
    return (
        f'<table class="{table_class}"><thead><tr>{header}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_economist_report_html(report: dict[str, Any]) -> str:
    """HTML-отчёт по объекту: доходы, расходы, финансовый результат."""
    parts: list[str] = ['<div class="economist-report">']

    if report.get("object"):
        parts.append(
            f'<p class="economist-object"><strong>Объект:</strong> '
            f"{html_module.escape(str(report['object']))}</p>"
        )

    for section in report.get("sections", []):
        title = str(section.get("title") or "")
        rows = section.get("rows") or []
        if not rows and not section.get("total"):
            continue
        parts.append(f'<h4 class="economist-section-title">{html_module.escape(title)}</h4>')
        parts.append(
            _render_data_table(
                rows,
                preferred_cols=_ITEM_FIELD_ORDER,
                total_label=section.get("total_label"),
                total_row=section.get("total"),
            )
        )

    summary = report.get("summary")
    if summary and summary.get("values"):
        values = summary["values"]
        title = str(summary.get("title") or "Итого")
        cols = [c for c in ("Годовой лимит", "Текущий лимит") if not _cell_is_empty(values.get(c))]
        if cols:
            parts.append(
                f'<h4 class="economist-section-title">{html_module.escape(title)}</h4>'
            )
            header = "".join(f"<th>{html_module.escape(c)}</th>" for c in cols)
            cells = "".join(
                f"<td>{html_module.escape(_fmt_value(values.get(c)))}</td>" for c in cols
            )
            parts.append(
                f'<table class="economist-table economist-table-summary">'
                f"<thead><tr>{header}</tr></thead>"
                f"<tbody><tr>{cells}</tr></tbody></table>"
            )

    parts.append("</div>")
    return "".join(parts)


def render_economist_html(
    records: list[dict[str, Any]],
    report: dict[str, Any] | None = None,
) -> str:
    if report:
        html = render_economist_report_html(report)
        if html.strip():
            return html
    return render_economist_table_html(records)


def _collect_article_records(data: Any) -> list[dict[str, Any]]:
    """Записи из n8n: [{output: {...}}] или [{output: [{...}, {...}]}]."""
    found: list[dict[str, Any]] = []
    _walk_n8n_records(data, found)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in found:
        key = f"{rec.get('Код статьи', '')}|{rec.get('Приоритет', '')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    return unique


def finalize_economist_response(
    data: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Текст, плоские records и (опционально) отчёт по объекту."""
    report_raw = _find_object_report(data)
    if report_raw:
        report = _parse_object_report(report_raw)
        if report.get("sections") or report.get("summary"):
            return (
                _format_object_report_text(report),
                _flatten_report_records(report),
                report,
            )

    records = _collect_article_records(data)
    if records:
        text = "\n\n".join(_format_article_record(r) for r in records)
        return text, records, None

    answer = _extract_answer(data)
    if answer and not isinstance(answer, (dict, list)) and not _is_meaningless_text(answer):
        return str(answer).strip(), [], None
    return "", [], None


def _format_article_record(record: dict[str, Any]) -> str:
    lines: list[str] = []
    seen: set[str] = set()

    for label in _ARTICLE_FIELD_ORDER:
        if label in record:
            lines.append(f"{label}: {_fmt_value(record[label])}")
            seen.add(label)

    for key, value in record.items():
        if key not in seen and value is not None and str(key).strip():
            lines.append(f"{key}: {_fmt_value(value)}")

    try:
        if "Текущий лимит" in record and "Факт" in record:
            remainder = float(record["Текущий лимит"]) - float(record["Факт"])
            lines.append(f"Остаток: {_fmt_value(remainder)}")
    except (TypeError, ValueError):
        pass

    return "\n".join(lines)


def render_economist_table_html(records: list[dict[str, Any]]) -> str:
    """HTML-таблица для чата (генерируется на сервере)."""
    return _render_data_table(records, preferred_cols=_ARTICLE_FIELD_ORDER)


def _is_js_object_string(text: str) -> bool:
    """JavaScript toString() для объектов — типичная ошибка шаблона n8n."""
    return "[object Object]" in (text or "")


def _object_object_error() -> str:
    return (
        "n8n вернул «[object Object]»: в Respond to Webhook массив/объект "
        "подставлен в текстовое поле answer. "
        "Для таблицы статей в Response Body включите Expression (fx) и укажите: "
        "={{ [{ output: $json.output }] }} "
        "(или ={{ $json }}, если output уже на верхнем уровне). "
        "Не используйте \"answer\": \"{{ $json.output }}\" — так объекты превращаются в строку."
    )


def _format_n8n_payload(data: Any) -> str | None:
    records = _collect_article_records(data)
    if not records:
        return None
    return "\n\n".join(_format_article_record(r) for r in records)


def _is_meaningless_text(text: str | None) -> bool:
    """Пустой ответ или строковое представление пустого JSON от n8n."""
    if not text:
        return True
    s = str(text).strip()
    return not s or s in ("[]", "{}", "null", '""')


def _is_empty_n8n_data(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, str):
        s = data.strip()
        if not s:
            return True
        if s in ("[]", "{}", "null"):
            return True
        if s.startswith(("[", "{")):
            try:
                parsed = json.loads(s)
                return _is_empty_n8n_data(parsed)
            except json.JSONDecodeError:
                return False
        return False
    if isinstance(data, (list, dict)) and not data:
        return True
    return False


def _extract_answer(data: Any) -> str | None:
    """Извлечение текста ответа из типичных форматов ответа n8n."""
    if _is_empty_n8n_data(data):
        return None
    if isinstance(data, str):
        text = data.strip()
        return None if _is_meaningless_text(text) else text
    if isinstance(data, list):
        return _extract_answer(data[0])
    if not isinstance(data, dict):
        text = str(data).strip()
        return None if _is_meaningless_text(text) else text

    for key in ("answer", "response", "output", "text", "message", "result", "reply", "content", "body"):
        if key in data and data[key] is not None:
            found = _extract_answer(data[key])
            if found:
                return found

    # OpenAI / LLM-узлы n8n
    for key in ("choices", "candidates"):
        if key in data and isinstance(data[key], list) and data[key]:
            found = _extract_answer(data[key][0])
            if found:
                return found

    if "json" in data:
        return _extract_answer(data["json"])
    if "data" in data:
        return _extract_answer(data["data"])

    return None


def _empty_response_error(status_code: int) -> str:
    return (
        f"n8n вернул пустой ответ (HTTP {status_code}). "
        "Workflow выполнился, но текст не вернулся. В n8n проверьте: "
        "1) Webhook → Response Mode = «Using Respond to Webhook Node»; "
        "2) в конце цепочки один узел Respond to Webhook; "
        "3) Respond With = JSON, тело: {\"answer\": \"{{ $json.text }}\"} "
        "(подставьте поле с ответом из вашего LLM/логики). "
        "Посмотрите Executions в n8n — доходит ли поток до Respond to Webhook."
    )


def _parse_response_body(resp: httpx.Response) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Текст ответа и структурированные записи для таблицы в UI."""
    raw = (resp.text or "").strip()
    if not raw:
        if resp.status_code in (200, 201, 202, 204):
            raise RuntimeError(_empty_response_error(resp.status_code))
        raise RuntimeError(f"n8n вернул пустой ответ (HTTP {resp.status_code})")

    try:
        data = resp.json()
    except Exception:
        return raw, [], None

    if isinstance(data, str):
        s = data.strip()
        if s.startswith(("[", "{")):
            try:
                data = json.loads(s)
            except json.JSONDecodeError:
                return raw, [], None

    if data is None or data == "":
        raise RuntimeError(_empty_response_error(resp.status_code))

    if _is_empty_n8n_data(data):
        raise RuntimeError(_empty_response_error(resp.status_code))

    text, records, report = finalize_economist_response(data)
    if report or records:
        logger.info(
            "n8n: отчёт=%s, записей=%d",
            bool(report),
            len(records),
        )
        return text, records, report

    if _is_js_object_string(text):
        raise RuntimeError(_object_object_error())

    if text and not _is_meaningless_text(text):
        logger.info("n8n: текстовый ответ, записей статей: 0")
        return text, records, None

    logger.warning(
        "n8n ответ %s, тело не распознано: %s",
        resp.status_code,
        str(data)[:400],
    )
    raise RuntimeError(
        "n8n не вернул поле answer (или output/text/response). "
        f"Получено: {str(data)[:200]}. "
        'Настройте Respond to Webhook: {"answer": "текст ответа"} '
        "или массив [{\"output\": {...}}]."
    )


async def _call_webhook(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, str],
    method: str,
) -> httpx.Response:
    method = method.upper()
    if method == "GET":
        return await client.get(url, params=payload)
    return await client.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
    )


async def ask_economist_n8n(
    message: str,
    *,
    fact_sheet_url: str = "",
    session_id: str = "economist",
) -> tuple[str, list[dict[str, Any]], str]:
    """
    Запрос на webhook n8n (POST или GET).
    Возвращает текст, записи и HTML-таблицу для UI.
    """
    url = N8N_ECONOMIST_WEBHOOK_URL
    if not url:
        raise ValueError("N8N_ECONOMIST_WEBHOOK_URL не задан в .env")

    method = N8N_ECONOMIST_WEBHOOK_METHOD
    if is_test_webhook_url(url):
        logger.warning(
            "N8N_ECONOMIST_WEBHOOK_URL — test URL (%s). "
            "Для продакшена используйте /webhook/, не /webhook-test/.",
            _safe_url_for_log(url),
        )

    payload = {
        "message": message,
        "query": message,
        "module": "economist",
        "fact_sheet_url": fact_sheet_url,
        "session_id": session_id,
    }

    async with httpx.AsyncClient(timeout=N8N_ECONOMIST_TIMEOUT) as client:
        resp = await _call_webhook(client, url, payload, method)

    if resp.status_code >= 400:
        body = resp.text[:500]
        logger.error(
            "n8n %s %s → %s",
            method.upper(),
            _safe_url_for_log(url),
            resp.status_code,
        )
        raise RuntimeError(
            _format_n8n_error(resp.status_code, body, url, method=method)
        )

    logger.info(
        "n8n %s %s → %s, %d байт",
        method.upper(),
        _safe_url_for_log(url),
        resp.status_code,
        len(resp.content or b""),
    )
    text, records, report = _parse_response_body(resp)
    table_html = render_economist_html(records, report)
    return text, records, table_html
