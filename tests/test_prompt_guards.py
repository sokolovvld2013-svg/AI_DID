"""Тесты нумерации ссылок и блока «Источники» в core.prompt_guards."""

from __future__ import annotations

from core.prompt_guards import (
    format_expert_source_lines,
    renumber_inline_citations,
)


def test_empty_answer_unchanged() -> None:
    assert renumber_inline_citations("") == ""


def test_answer_without_citations_unchanged() -> None:
    answer = "Текст без ссылок."
    assert renumber_inline_citations(answer) == answer


def test_verbose_citations_become_numbers() -> None:
    answer = "Условие одно [п. 2.13 Положения о закупке]. Условие два [ст. 5 Устава]."
    result = renumber_inline_citations(answer)
    assert "[1]" in result
    assert "[2]" in result
    assert "**Источники:**" in result


def test_numeric_citations_kept_as_is() -> None:
    answer = "Ссылка [1] и ещё [2]."
    assert renumber_inline_citations(answer) == answer


def test_same_source_numbered_once() -> None:
    answer = "Первое [п. 2.13 Положения]. Второе то же [п. 2.13 Положения]."
    result = renumber_inline_citations(answer)
    # Обе ссылки получают один номер, а в блоке источников запись одна.
    assert result.startswith("Первое [1]. Второе то же [1].")
    block = result.split("**Источники:**", 1)[1]
    assert len([line for line in block.strip().splitlines() if line.strip()]) == 1


def test_modal_annotations_untouched() -> None:
    answer = "Нужно уточнить [требуется проверка данных]."
    assert renumber_inline_citations(answer) == answer


def test_format_expert_source_lines() -> None:
    lines = format_expert_source_lines(
        [{"filename": "Договор.pdf", "page": 3, "id": 1}, {"filename": "Устав.docx"}]
    )
    assert lines[0] == "[1] Договор.pdf, стр. 3"
    assert lines[1] == "Устав.docx, стр. —"


def test_format_expert_source_lines_keeps_section_label() -> None:
    lines = format_expert_source_lines([{"filename": "Положение", "page": "разд. 3"}])
    assert lines == ["Положение, разд. 3"]


def test_format_expert_source_lines_empty() -> None:
    assert format_expert_source_lines([]) == []