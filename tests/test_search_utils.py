"""Тесты чистых функций lawyer.search_utils.

Модуль не обращается к сети и Chroma, поэтому проверяется напрямую.
"""

from __future__ import annotations

from lawyer.search_utils import (
    count_core_matches,
    normalize_match_text,
    reciprocal_rank_fusion,
    stems_in_order,
    tokenize,
)


def test_tokenize_lowercases_and_splits() -> None:
    assert tokenize("Договор поставки № 44") == ["договор", "поставки", "44"]


def test_normalize_match_text_collapses_dashes_and_spaces() -> None:
    assert normalize_match_text("  ПУБЛИЧНЫЙ\n\tДОГОВОР  ") == "публичный договор"


def test_normalize_match_text_removes_soft_hyphen() -> None:
    assert "­" not in normalize_match_text("судь­ба")


def test_count_core_matches_finds_tokens() -> None:
    document = "Настоящий договор поставки заключён между сторонами."
    assert count_core_matches(["договор"], document) == 1


def test_count_core_matches_handles_empty_input() -> None:
    assert count_core_matches([], "текст") == 0
    assert count_core_matches(["договор"], "") == 0


def test_stems_in_order_requires_order() -> None:
    assert stems_in_order(["договор", "поставки"], "Договор поставки заключён")
    assert not stems_in_order(["поставки", "договор"], "Договор поставки заключён")


def test_stems_in_order_empty_words() -> None:
    assert not stems_in_order([], "любой текст")


def test_rrf_scores_first_positions_higher() -> None:
    scores = reciprocal_rank_fusion([["a", "b"], ["a", "c"]])
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == {}