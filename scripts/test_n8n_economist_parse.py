"""Проверка разбора ответа n8n для Экономиста."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from economist.n8n_client import finalize_economist_response, render_economist_html

SAMPLE_ARTICLES = [
    {
        "output": [
            {
                "Приоритет": "Приоритетная статья",
                "Код статьи": "2214",
                "Наименование статьи": "Газоснабжение",
                "Годовой лимит": 1736,
                "Текущий период": "3 месяца",
                "Текущий лимит": 738,
            },
            {
                "Приоритет": "Альтернатива",
                "Код статьи": "11224",
                "Наименование статьи": "Газоснабжение",
                "Годовой лимит": 1736,
                "Текущий период": "3 месяца",
                "Текущий лимит": 738,
            },
        ]
    }
]

SAMPLE_OBJECT_REPORT = [
    {
        "output": {
            "Объект": "Санкт-Петербург, ул. Гороховая д.2/6",
            "Статьи доходов и расходов": {
                "Доход": {
                    "items": [
                        {
                            "Код статьи": "11221",
                            "Наименование статьи": "Возмещение коммунальных услуг (теплоснабжение)",
                            "Годовой лимит": 2168,
                            "Текущий период": "Январь-Июнь",
                            "Текущий лимит": 1323,
                        },
                        {
                            "Код статьи": "11222",
                            "Наименование статьи": "Возмещение коммунальных услуг (электроснабжение)",
                            "Годовой лимит": 4308,
                            "Текущий период": "Январь-Июнь",
                            "Текущий лимит": 2076,
                        },
                    ],
                    "Всего доходов": {"Годовой лимит": 6476, "Текущий лимит": 3399},
                },
                "Расход": {
                    "items": [
                        {
                            "Код статьи": "2312а",
                            "Наименование статьи": "Текущий ремонт помещений",
                            "Годовой лимит": 29560,
                            "Текущий период": "Январь-Июнь",
                            "Текущий лимит": 12104,
                        },
                    ],
                    "Всего расходов": {"Годовой лимит": 29560, "Текущий лимит": 12104},
                },
                "Финансовый результат": {"Годовой лимит": -23084, "Текущий лимит": -8705},
            },
        }
    }
]


def run_sample(name: str, sample):
    text, records, report = finalize_economist_response(sample)
    html = render_economist_html(records, report)
    print(f"=== {name} ===")
    print("records:", len(records), "report:", bool(report))
    print("html length:", len(html))
    assert "economist-table" in html, "table missing"
    if report:
        assert "Объект:" in html
        assert "Доход" in html
        assert "Расход" in html
        assert "Всего доходов" in html
        assert "Финансовый результат" in html
    print("OK\n")


if __name__ == "__main__":
    run_sample("articles", SAMPLE_ARTICLES)
    run_sample("object report", SAMPLE_OBJECT_REPORT)
