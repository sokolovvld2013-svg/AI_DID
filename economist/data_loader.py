"""Загрузка и парсинг Excel-файлов плановых и фактических затрат."""
import io
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

PLAN_COLUMNS = [
    "Структурное подразделение",
    "Объект",
    "ИК",
    "Контрагент",
    "Примечание",
    "Доходы/ Расходы",
    "КБК",
    "Группа статьи",
    "Статья",
    "Сумма",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
    "Капитальные вложения",
]

MONTH_COLUMNS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

FACT_COLUMNS = ["Статьи движения денежных средств", "Дебет", "Кредит"]
FACT_ARTICLE_COL = "Статьи движения денежных средств"
FACT_OSV_FILENAME = "fact_osv.txt"

OSV_ARTICLE_ALIASES = [
    FACT_ARTICLE_COL,
    "Субконто",
    "Субконто1",
    "Субконто 1",
    "Наименование",
    "Статья",
    "Счет",
    "Счёт",
    "Вид субконто",
]
OSV_DEBIT_ALIASES = ["Дебет", "Дебет (оборот)", "Оборот Дт", "Сальдо Дт"]
OSV_CREDIT_ALIASES = ["Кредит", "Кредит (оборот)", "Оборот Кт", "Сальдо Кт"]

DESCRIPTION_COL = "Описание статьи"
CODE_COL = "Код"
ARTICLE_NAME_COL = "Наименование статьи"

# Варианты названий колонок (после нормализации — точное совпадение)
DESCRIPTION_ALIASES = [DESCRIPTION_COL, "Описание", "описание статьи"]
CODE_ALIASES = [CODE_COL, "Код статьи", "код"]
NAME_ALIASES = [ARTICLE_NAME_COL, "Наименование", "Статья", "статья"]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Приведение названий колонок к единому виду."""
    mapping = {}
    for col in df.columns:
        clean = str(col).strip()
        mapping[col] = clean
    return df.rename(columns=mapping)


def _find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    """Поиск колонки по списку возможных названий (без учёта регистра)."""
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _rename_catalog_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Приведение справочника к стандартным именам колонок."""
    df = _normalize_columns(df)
    mapping = {}
    desc = _find_column(df, DESCRIPTION_ALIASES)
    code = _find_column(df, CODE_ALIASES)
    name = _find_column(df, NAME_ALIASES)
    if desc and desc != DESCRIPTION_COL:
        mapping[desc] = DESCRIPTION_COL
    if code and code != CODE_COL:
        mapping[code] = CODE_COL
    if name and name != ARTICLE_NAME_COL:
        mapping[name] = ARTICLE_NAME_COL
    if mapping:
        df = df.rename(columns=mapping)
    return df


def validate_catalog_df(df: pd.DataFrame) -> None:
    """Проверка обязательных колонок справочника статей."""
    if df.empty:
        raise ValueError("Файл пуст")
    if DESCRIPTION_COL not in df.columns:
        found = ", ".join(str(c) for c in df.columns)
        raise ValueError(
            f'Нужна колонка «{DESCRIPTION_COL}». Найдены колонки: {found}'
        )
    filled = df[DESCRIPTION_COL].astype(str).str.strip().replace("nan", "")
    if not (filled != "").any():
        raise ValueError(f'Колонка «{DESCRIPTION_COL}» не содержит данных')


def load_catalog_excel(path: Path) -> pd.DataFrame:
    """Загрузка справочника статей (независимый файл)."""
    logger.info("Загрузка справочника статей: %s", path)
    df = _rename_catalog_columns(_read_excel(path))
    validate_catalog_df(df)
    for col in (DESCRIPTION_COL, CODE_COL, ARTICLE_NAME_COL):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def catalog_row_to_text(row: pd.Series) -> str:
    """Текст для поиска по строке справочника (приоритет — описание)."""
    if DESCRIPTION_COL in row.index and pd.notna(row[DESCRIPTION_COL]):
        desc = str(row[DESCRIPTION_COL]).strip()
        if desc and desc.lower() != "nan":
            parts = [desc]
            if CODE_COL in row.index and pd.notna(row.get(CODE_COL)):
                code = str(row[CODE_COL]).strip()
                if code and code.lower() != "nan":
                    parts.append(f"Код: {code}")
            if ARTICLE_NAME_COL in row.index and pd.notna(row.get(ARTICLE_NAME_COL)):
                name = str(row[ARTICLE_NAME_COL]).strip()
                if name and name.lower() != "nan":
                    parts.append(f"Наименование: {name}")
            return ". ".join(parts)
    return row_to_text(row)


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)


def _read_excel(path: Path) -> pd.DataFrame:
    """Чтение .xlsx (openpyxl) или старого .xls (xlrd)."""
    ext = path.suffix.lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    return pd.read_excel(path, engine=engine)


def load_plan_excel(path: Path) -> pd.DataFrame:
    """Загрузка планового файла затрат."""
    logger.info("Загрузка планового Excel: %s", path)
    df = _read_excel(path)
    df = _normalize_columns(df)

    for col in MONTH_COLUMNS + ["Сумма"]:
        if col in df.columns:
            df[col] = _to_numeric(df[col])

    if "Статья" in df.columns:
        df["Статья"] = df["Статья"].astype(str).str.strip()

    return df


def load_generic_excel(path: Path) -> pd.DataFrame:
    """Загрузка произвольного Excel."""
    logger.info("Загрузка Excel: %s", path)
    return _rename_catalog_columns(_read_excel(path))


def _article_name_column(plan_df: pd.DataFrame) -> str:
    if ARTICLE_NAME_COL in plan_df.columns:
        return ARTICLE_NAME_COL
    if "Статья" in plan_df.columns:
        return "Статья"
    return ARTICLE_NAME_COL


def resolve_from_catalog(
    catalog_df: pd.DataFrame,
    row_index: int | None = None,
) -> dict[str, Any]:
    """Код и наименование из строки справочника."""
    empty = {"code": "", "name": "", "group": ""}
    if catalog_df is None or catalog_df.empty:
        return empty
    if row_index is None or row_index not in catalog_df.index:
        return empty

    row = catalog_df.loc[row_index]
    code = str(row[CODE_COL]).strip() if CODE_COL in catalog_df.columns and pd.notna(row.get(CODE_COL)) else ""
    name = (
        str(row[ARTICLE_NAME_COL]).strip()
        if ARTICLE_NAME_COL in catalog_df.columns and pd.notna(row.get(ARTICLE_NAME_COL))
        else ""
    )
    group = (
        str(row["Группа статьи"]).strip()
        if "Группа статьи" in catalog_df.columns and pd.notna(row.get("Группа статьи"))
        else ""
    )
    return {"code": code, "name": name, "group": group}


def search_catalog_for_query(
    catalog_df: pd.DataFrame,
    query: str,
    search_fn,
    top_k: int = 1,
) -> dict[str, Any]:
    """Поиск статьи по запросу пользователя в справочнике."""
    hits = search_fn(query, top_k=top_k)
    if not hits:
        return {"code": "", "name": "", "group": "", "score": 0.0, "description": ""}

    hit = hits[0]
    resolved = resolve_from_catalog(catalog_df, row_index=hit.get("row_index"))
    return {
        "code": resolved["code"] or str(hit.get("code", "")).strip(),
        "name": resolved["name"] or str(hit.get("article", "")).strip(),
        "group": resolved["group"] or str(hit.get("group", "")).strip(),
        "score": float(hit.get("score", 0)),
        "description": str(hit.get("text", ""))[:500],
    }


def match_queries_in_file(
    catalog_df: pd.DataFrame,
    queries_df: pd.DataFrame,
    search_fn,
) -> pd.DataFrame:
    """
    Для каждой строки с «Описание статьи» (запрос) находит статью в справочнике.
    Заполняет Код и Наименование статьи из найденной строки справочника.
    """
    if DESCRIPTION_COL not in queries_df.columns:
        raise ValueError(f'В файле должна быть колонка «{DESCRIPTION_COL}»')

    result = queries_df.copy()
    out_code = "Найденный код" if CODE_COL in result.columns else CODE_COL
    out_name = "Найденное наименование" if ARTICLE_NAME_COL in result.columns else ARTICLE_NAME_COL

    codes, names, groups, scores = [], [], [], []

    for _, row in queries_df.iterrows():
        desc = row[DESCRIPTION_COL]
        if pd.isna(desc) or not str(desc).strip() or str(desc).strip().lower() == "nan":
            codes.append("")
            names.append("")
            groups.append("")
            scores.append(None)
            continue

        match = search_catalog_for_query(catalog_df, str(desc).strip(), search_fn)
        codes.append(match["code"])
        names.append(match["name"])
        groups.append(match["group"])
        scores.append(round(match["score"], 4) if match["score"] else 0.0)

    result[out_code] = codes
    result[out_name] = names
    result["Группа статьи"] = groups
    result["Релевантность"] = scores
    return result


def save_match_result(df: pd.DataFrame, path: Path) -> Path:
    """Сохранение результата сопоставления в .xlsx."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False, engine="openpyxl")
    return path


def load_fact_excel(path: Path) -> pd.DataFrame:
    """Загрузка фактического файла затрат."""
    logger.info("Загрузка фактического Excel: %s", path)
    df = _read_excel(path)
    df = _normalize_columns(df)
    return _normalize_fact_df(df)


def _normalize_fact_df(df: pd.DataFrame) -> pd.DataFrame:
    """Приведение факта к стандартным колонкам."""
    df = _normalize_columns(df)
    article_src = _find_column(df, OSV_ARTICLE_ALIASES)
    debit_src = _find_column(df, OSV_DEBIT_ALIASES)
    credit_src = _find_column(df, OSV_CREDIT_ALIASES)

    rename = {}
    if article_src and article_src != FACT_ARTICLE_COL:
        rename[article_src] = FACT_ARTICLE_COL
    if debit_src and debit_src != "Дебет":
        rename[debit_src] = "Дебет"
    if credit_src and credit_src != "Кредит":
        rename[credit_src] = "Кредит"
    if rename:
        df = df.rename(columns=rename)

    for col in ["Дебет", "Кредит"]:
        if col in df.columns:
            df[col] = _to_numeric(df[col])

    if FACT_ARTICLE_COL in df.columns:
        df[FACT_ARTICLE_COL] = df[FACT_ARTICLE_COL].astype(str).str.strip()

    return df


def _detect_delimiter(line: str) -> str:
    if "\t" in line:
        return "\t"
    if ";" in line:
        return ";"
    return r"\s{2,}"


def _find_osv_header_row(lines: list[str]) -> int | None:
    """Строка заголовка ОСВ (содержит дебет/кредит или статью)."""
    for i, line in enumerate(lines[:80]):
        low = line.lower()
        has_amounts = "дебет" in low or "кредит" in low
        has_article = any(
            a.lower() in low
            for a in ["стать", "субконто", "наименование", "счет", "счёт"]
        )
        if has_amounts or (has_article and ("\t" in line or ";" in line)):
            return i
    return None


def parse_osv_text(text: str) -> pd.DataFrame:
    """
    Разбор текста ОСВ, скопированного из 1С (табуляция или «;» между колонками).
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ValueError("Вставьте текст отчёта ОСВ из 1С")

    lines = [ln for ln in raw.split("\n") if ln.strip()]
    header_idx = _find_osv_header_row(lines)
    if header_idx is None:
        raise ValueError(
            "Не найдена строка заголовков. Скопируйте из 1С вместе с шапкой "
            "(колонки Дебет, Кредит, Субконто/Наименование)."
        )

    delim = _detect_delimiter(lines[header_idx])
    table_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(
        io.StringIO(table_text),
        sep=delim,
        engine="python",
        dtype=str,
        on_bad_lines="skip",
    )
    df = _normalize_fact_df(df)

    if FACT_ARTICLE_COL not in df.columns:
        # Одна текстовая колонка + числа — считаем первую колонку статьёй
        non_num = [
            c
            for c in df.columns
            if c not in ("Дебет", "Кредит")
            and not str(c).startswith("Unnamed")
        ]
        if non_num:
            df = df.rename(columns={non_num[0]: FACT_ARTICLE_COL})
        else:
            found = ", ".join(str(c) for c in df.columns)
            raise ValueError(
                f'Не найдена колонка статей. Найдены: {found}. '
                f'Нужны колонки вроде «{FACT_ARTICLE_COL}», «Субконто» или «Наименование».'
            )

    # Убрать пустые и итоговые строки
    art = df[FACT_ARTICLE_COL].astype(str).str.strip()
    skip = art.str.lower().isin(
        ("", "nan", "итого", "всего", "сальдо", "обороты", "начальное сальдо")
    )
    df = df[~skip].copy()

    if df.empty:
        raise ValueError("После разбора не осталось строк с данными")

    logger.info("ОСВ: загружено %d строк", len(df))
    return df


def save_fact_osv_text(text: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / FACT_OSV_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


def load_fact_osv_from_disk(directory: Path) -> tuple[pd.DataFrame | None, str | None]:
    path = directory / FACT_OSV_FILENAME
    if not path.exists():
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
        return parse_osv_text(text), "ОСВ из 1С"
    except Exception as e:
        logger.warning("Не удалось загрузить сохранённый ОСВ: %s", e)
        return None, None


def get_fact_by_article(fact_df: pd.DataFrame, article_query: str) -> dict[str, Any]:
    """Факт (кредит/дебет) по статье из ОСВ."""
    empty = {
        "article": article_query,
        "matched_article": None,
        "debit": 0.0,
        "credit": 0.0,
        "rows_count": 0,
    }
    if fact_df is None or fact_df.empty or FACT_ARTICLE_COL not in fact_df.columns:
        return empty

    matched_name = match_fact_article(fact_df, article_query)
    if not matched_name:
        q = article_query.lower().strip()
        mask = fact_df[FACT_ARTICLE_COL].astype(str).str.lower().str.contains(q, na=False, regex=False)
        rows = fact_df[mask]
        if rows.empty:
            return empty
        matched_name = str(rows.iloc[0][FACT_ARTICLE_COL])
    else:
        rows = fact_df[fact_df[FACT_ARTICLE_COL].astype(str) == matched_name]

    debit = float(rows["Дебет"].sum()) if "Дебет" in rows.columns else 0.0
    credit = float(rows["Кредит"].sum()) if "Кредит" in rows.columns else 0.0
    return {
        "article": article_query,
        "matched_article": matched_name,
        "debit": debit,
        "credit": credit,
        "rows_count": len(rows),
    }


def search_fact_articles(fact_df: pd.DataFrame, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Поиск статей в факте по подстроке."""
    if fact_df is None or fact_df.empty or FACT_ARTICLE_COL not in fact_df.columns:
        return []

    q = query.lower().strip()
    results = []
    for art in fact_df[FACT_ARTICLE_COL].dropna().unique():
        art_str = str(art).strip()
        if not art_str or art_str.lower() == "nan":
            continue
        if q in art_str.lower() or art_str.lower() in q:
            data = get_fact_by_article(fact_df, art_str)
            results.append(data)
            if len(results) >= limit:
                break
    return results


def row_to_text(row: pd.Series, columns: list[str] | None = None) -> str:
    """Текстовое представление строки для RAG."""
    cols = columns or [c for c in row.index if pd.notna(row[c]) and str(row[c]).strip()]
    parts = []
    for col in cols:
        val = row[col]
        if pd.notna(val) and str(val).strip() and str(val) != "0":
            parts.append(f"{col}: {val}")
    return ", ".join(parts)


def find_article_match(plan_df: pd.DataFrame, article_name: str) -> str | None:
    """Нечёткое сопоставление названия статьи между планом и фактом."""
    if plan_df is None or plan_df.empty:
        return article_name

    name_col = _article_name_column(plan_df)
    if name_col not in plan_df.columns and "Статья" not in plan_df.columns:
        return article_name

    col = name_col if name_col in plan_df.columns else "Статья"
    article_lower = article_name.lower().strip()
    for art in plan_df[col].dropna().unique():
        art_str = str(art).lower().strip()
        if art_str == article_lower or art_str in article_lower or article_lower in art_str:
            return str(art)
    return article_name


def match_fact_article(fact_df: pd.DataFrame, plan_article: str) -> str | None:
    """Сопоставление статьи плана со статьёй в фактическом файле."""
    col = "Статьи движения денежных средств"
    if fact_df is None or fact_df.empty or col not in fact_df.columns:
        return None

    plan_lower = plan_article.lower().strip()
    for fact_art in fact_df[col].dropna().unique():
        fact_str = str(fact_art).lower().strip()
        if fact_str == plan_lower or plan_lower in fact_str or fact_str in plan_lower:
            return str(fact_art)
    # Частичное совпадение по словам
    plan_words = set(re.findall(r"\w+", plan_lower))
    best, best_score = None, 0
    for fact_art in fact_df[col].dropna().unique():
        fact_words = set(re.findall(r"\w+", str(fact_art).lower()))
        score = len(plan_words & fact_words)
        if score > best_score:
            best_score = score
            best = str(fact_art)
    return best if best_score >= 2 else None


def get_limit_and_fact(
    plan_df: pd.DataFrame,
    fact_df: pd.DataFrame,
    article: str,
    period_months: int,
    reference_month: int | None = None,
) -> dict[str, Any]:
    """
    Лимит = сумма плановых месячных значений за период.
    Факт = сумма кредитовых оборотов (расходов) за те же месяцы.
    period_months: 3, 6, 9 или 12 — последние N месяцев от reference_month.
    """
    import datetime

    ref_month = reference_month or datetime.datetime.now().month
    # Месяцы периода: от (ref_month - period_months + 1) до ref_month
    start_idx = max(0, ref_month - period_months)
    end_idx = ref_month
    period_month_cols = MONTH_COLUMNS[start_idx:end_idx]

    result = {
        "article": article,
        "period_months": period_months,
        "months": period_month_cols,
        "limit": 0.0,
        "fact": 0.0,
        "group": None,
    }

    if plan_df is not None and not plan_df.empty and "Статья" in plan_df.columns:
        matched = find_article_match(plan_df, article)
        mask = plan_df["Статья"].astype(str).str.lower().str.contains(
            matched.lower() if matched else article.lower(), na=False, regex=False
        )
        rows = plan_df[mask]
        if not rows.empty:
            result["group"] = rows.iloc[0].get("Группа статьи")
            result["article"] = rows.iloc[0]["Статья"]
            for m in period_month_cols:
                if m in rows.columns:
                    result["limit"] += float(rows[m].sum())

    if fact_df is not None and not fact_df.empty:
        fact_article = match_fact_article(fact_df, result["article"])
        col = "Статьи движения денежных средств"
        if fact_article and col in fact_df.columns:
            mask = fact_df[col].astype(str) == fact_article
            # Факт — нарастающий итог кредита (расходы) по статье
            if "Кредит" in fact_df.columns:
                result["fact"] = float(fact_df.loc[mask, "Кредит"].sum())

    return result


def get_object_yearly_cost(plan_df: pd.DataFrame, object_query: str) -> dict[str, Any]:
    """Годовые затраты по объекту (частичное совпадение в колонке Объект)."""
    if plan_df is None or plan_df.empty or "Объект" not in plan_df.columns:
        return {"object": object_query, "total": 0.0, "rows_count": 0}

    query_lower = object_query.lower().strip()
    mask = plan_df["Объект"].astype(str).str.lower().str.contains(query_lower, na=False, regex=False)
    rows = plan_df[mask]

    if rows.empty:
        return {"object": object_query, "total": 0.0, "rows_count": 0}

    total = 0.0
    if "Сумма" in rows.columns and rows["Сумма"].sum() > 0:
        total = float(rows["Сумма"].sum())
    else:
        for m in MONTH_COLUMNS:
            if m in rows.columns:
                total += float(rows[m].sum())

    object_name = rows.iloc[0]["Объект"]
    return {
        "object": str(object_name),
        "total": total,
        "rows_count": len(rows),
    }
