"""Динамическая метка проверки торговой документации."""

from __future__ import annotations

from core.history_label import condense, find_object_address, first_address


def _auction_address(parsed: dict | None) -> str:
    if not parsed:
        return ""
    return find_object_address(parsed.get("text") or "")


def _egrn_address(parsed: dict | None) -> str:
    if not parsed:
        return ""
    fields = parsed.get("fields") or {}
    return first_address(str(fields.get("address") or ""))


def build_history_label(
    parsed_auction: dict | None,
    parsed_egrn: dict | None = None,
    parsed_approval: dict | None = None,
) -> str:
    procedure = "Аукцион" if parsed_auction else "Торги"
    address = _egrn_address(parsed_egrn) or _auction_address(parsed_auction)
    return condense(procedure, address)
