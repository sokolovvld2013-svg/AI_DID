"""Правила проверки торговой документации."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from config import (
    TENDERS_ADDRESS_SIMILARITY,
    TENDERS_AREA_TOLERANCE,
    TENDERS_EGRN_MAX_AGE_DAYS,
    TENDERS_LEASEHOLDER_SIMILARITY,
)
from tenders.services.fields import (
    CONTRACT_CLAUSES,
    REQUIRED_AUCTION_SECTIONS,
    extract_address_hint,
    normalize_cadastral,
    normalize_text,
    section_has_keywords,
    text_similarity,
)


def _issue(
    document: str,
    field: str,
    message: str,
    *,
    severity: str = "critical",
) -> dict[str, str]:
    return {
        "document": document,
        "field": field,
        "message": message,
        "severity": severity,
    }


def validate_auction_doc(text: str, fields: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []

    for title, keywords in REQUIRED_AUCTION_SECTIONS:
        if not section_has_keywords(text, keywords):
            errors.append(
                _issue(
                    "auction",
                    title,
                    f"Не найден обязательный раздел/элемент: «{title}» (Приказ ФАС №147).",
                    severity="major",
                )
            )

    contract_part = text
    idx = text.lower().find("проект договора")
    if idx >= 0:
        contract_part = text[idx:]
    for title, keywords in CONTRACT_CLAUSES:
        if not section_has_keywords(contract_part, keywords):
            warnings.append(
                _issue(
                    "auction",
                    title,
                    f"В проекте договора не обнаружен пункт: «{title}» (ст. 17.1 135-ФЗ).",
                    severity="minor",
                )
            )

    if not fields.get("cadastral"):
        errors.append(_issue("auction", "cadastral", "Не найден кадастровый номер объекта."))
    if not fields.get("address"):
        warnings.append(
            _issue("auction", "address", "Не удалось однозначно определить адрес объекта.", severity="minor")
        )
    if fields.get("area") is None:
        warnings.append(
            _issue("auction", "area", "Не найдена площадь объекта (м²).", severity="minor")
        )
    if not fields.get("prices"):
        warnings.append(
            _issue("auction", "price", "Не найдена начальная цена (руб.).", severity="minor")
        )
    if not fields.get("lease_terms"):
        warnings.append(
            _issue("auction", "lease_term", "Не найден срок аренды.", severity="minor")
        )

    return errors, warnings


def validate_egrn_doc(text: str, fields: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []

    if not text.strip():
        errors.append(_issue("egrn", "text", "Не удалось извлечь текст из выписки ЕГРН."))
        return errors, warnings

    if not fields.get("cadastral"):
        errors.append(_issue("egrn", "cadastral", "В выписке не найден кадастровый номер."))
    if not fields.get("address"):
        warnings.append(
            _issue("egrn", "address", "В выписке не определён адрес объекта.", severity="minor")
        )
    if not fields.get("owner"):
        warnings.append(
            _issue("egrn", "owner", "Не найдены сведения о собственнике.", severity="minor")
        )
    else:
        owner_low = fields["owner"].lower()
        if not any(k in owner_low for k in ("россий", "фгуп", "муницип", "росимущ", "государ")):
            warnings.append(
                _issue(
                    "egrn",
                    "owner",
                    "Собственник может не относиться к государственной/муниципальной собственности.",
                    severity="minor",
                )
            )

    low = text.lower()
    if any(k in low for k in ("арест", "запрет", "ограничение права", "обременен")):
        warnings.append(
            _issue(
                "egrn",
                "encumbrances",
                "В выписке упоминаются обременения/ограничения — проверьте возможность аренды.",
                severity="minor",
            )
        )

    if fields.get("latest_date"):
        try:
            dt = datetime.fromisoformat(fields["latest_date"])
            if dt.date() < (datetime.now() - timedelta(days=TENDERS_EGRN_MAX_AGE_DAYS)).date():
                errors.append(
                    _issue(
                        "egrn",
                        "extract_date",
                        f"Дата выписки старше {TENDERS_EGRN_MAX_AGE_DAYS} дней.",
                    )
                )
        except ValueError:
            warnings.append(
                _issue("egrn", "extract_date", "Не удалось проверить актуальность выписки.", severity="minor")
            )
    else:
        warnings.append(
            _issue("egrn", "extract_date", "Не найдена дата выписки для проверки актуальности.", severity="minor")
        )

    return errors, warnings


def validate_approval_doc(text: str, fields: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []

    if not text.strip():
        errors.append(_issue("approval", "text", "Не удалось извлечь текст согласования."))
        return errors, warnings

    if not fields.get("latest_date"):
        warnings.append(
            _issue("approval", "date", "Не найдена дата согласования.", severity="minor")
        )
    if not fields.get("cadastral") and not fields.get("address"):
        errors.append(
            _issue(
                "approval",
                "object",
                "В согласовании не указан объект (кадастровый номер или адрес).",
            )
        )
    if not fields.get("has_signature_hint"):
        warnings.append(
            _issue(
                "approval",
                "signature",
                "Не обнаружены признаки подписи уполномоченного лица.",
                severity="minor",
            )
        )

    return errors, warnings


def cross_validate(
    auction: dict[str, Any],
    egrn: dict[str, Any],
    approval: dict[str, Any],
) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []

    af = auction.get("fields") or {}
    ef = egrn.get("fields") or {}
    pf = approval.get("fields") or {}

    cad_nums = {
        "auction": normalize_cadastral(af.get("cadastral") or ""),
        "egrn": normalize_cadastral(ef.get("cadastral") or ""),
        "approval": normalize_cadastral(pf.get("cadastral") or ""),
    }
    present = {k: v for k, v in cad_nums.items() if v}
    if len(present) >= 2:
        values = list(present.values())
        if len(set(values)) > 1:
            errors.append(
                _issue(
                    "cross",
                    "cadastral",
                    "Кадастровые номера не совпадают между документами: "
                    + ", ".join(f"{k}={v}" for k, v in present.items()),
                )
            )
    elif present:
        warnings.append(
            _issue(
                "cross",
                "cadastral",
                "Кадастровый номер найден не во всех документах — перекрёстная проверка ограничена.",
                severity="minor",
            )
        )

    addresses = {
        "auction": af.get("address") or extract_address_hint(auction.get("text") or ""),
        "egrn": ef.get("address") or "",
        "approval": pf.get("address") or "",
    }
    addr_present = {k: v for k, v in addresses.items() if v}
    if len(addr_present) >= 2:
        keys = list(addr_present.keys())
        sim = text_similarity(addr_present[keys[0]], addr_present[keys[1]])
        for k in keys[2:]:
            sim = min(sim, text_similarity(addr_present[keys[0]], addr_present[k]))
        if sim < TENDERS_ADDRESS_SIMILARITY:
            errors.append(
                _issue(
                    "cross",
                    "address",
                    f"Адреса в документах существенно различаются (сходство {sim:.0%}).",
                )
            )

    areas = {
        "auction": af.get("area"),
        "egrn": ef.get("area"),
    }
    a_vals = {k: v for k, v in areas.items() if v is not None}
    if len(a_vals) == 2:
        v1, v2 = list(a_vals.values())
        if v1 and v2:
            diff = abs(v1 - v2) / max(v1, v2)
            if diff > TENDERS_AREA_TOLERANCE:
                errors.append(
                    _issue(
                        "cross",
                        "area",
                        f"Площади расходятся более чем на {TENDERS_AREA_TOLERANCE:.0%}: {a_vals}.",
                    )
                )

    owner = ef.get("owner") or ""
    landlord = af.get("address") or ""
    if owner and auction.get("text"):
        auction_text = auction.get("text") or ""
        if owner and text_similarity(owner, auction_text) < TENDERS_LEASEHOLDER_SIMILARITY:
            if not any(
                part in normalize_text(auction_text)
                for part in normalize_text(owner).split()
                if len(part) > 4
            ):
                warnings.append(
                    _issue(
                        "cross",
                        "landlord",
                        "Наименование арендодателя/собственника в документации и ЕГРН может не совпадать.",
                        severity="minor",
                    )
                )

    return errors, warnings


def compute_score(errors: list[dict], warnings: list[dict]) -> int:
    score = 100
    for e in errors:
        score -= 12 if e.get("severity") == "critical" else 8
    for w in warnings:
        score -= 3
    return max(0, min(100, score))


def run_all_validations(
    auction: dict[str, Any] | None,
    egrn: dict[str, Any] | None,
    approval: dict[str, Any] | None,
) -> dict[str, Any]:
    all_errors: list[dict] = []
    all_warnings: list[dict] = []

    if auction:
        e, w = validate_auction_doc(auction.get("text") or "", auction.get("fields") or {})
        all_errors.extend(e)
        all_warnings.extend(w)
    if egrn:
        e, w = validate_egrn_doc(egrn.get("text") or "", egrn.get("fields") or {})
        all_errors.extend(e)
        all_warnings.extend(w)
    if approval:
        e, w = validate_approval_doc(approval.get("text") or "", approval.get("fields") or {})
        all_errors.extend(e)
        all_warnings.extend(w)
    if auction and egrn and approval:
        e, w = cross_validate(auction, egrn, approval)
        all_errors.extend(e)
        all_warnings.extend(w)

    score = compute_score(all_errors, all_warnings)
    if all_errors:
        status = "failed"
    elif all_warnings:
        status = "warnings"
    else:
        status = "passed"

    return {
        "status": status,
        "score": score,
        "errors": all_errors,
        "warnings": all_warnings,
    }
