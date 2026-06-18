"""Диагностика: как распознан документ и почему он (не) попал в поиск.

Примеры:
  python scripts/diagnose_lawyer_search.py "Виды служебной информации"
  python scripts/diagnose_lawyer_search.py "Виды служебной информации" --file порядке
  python scripts/diagnose_lawyer_search.py --file порядке --reparse
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import LAWYER_UPLOAD_DIR  # noqa: E402
from lawyer.doc_processor import load_document, process_upload  # noqa: E402
from lawyer.rag import LawyerRAG, build_chunk_embed_text  # noqa: E402
from lawyer.search_utils import (  # noqa: E402
    core_query_tokens,
    count_core_matches,
    min_core_matches_required,
    query_search_substrings,
    word_stem,
)


def _find_uploaded(name_part: str) -> list[Path]:
    part = name_part.lower()
    return sorted(
        p
        for p in LAWYER_UPLOAD_DIR.iterdir()
        if p.is_file() and part in p.name.lower()
    )


def _print_extraction(path: Path, max_chars: int = 2500) -> None:
    print(f"\n=== Извлечение текста: {path.name} ===")
    print(f"Размер: {path.stat().st_size} байт")
    pages = load_document(path)
    total = sum(len(p.get("text") or "") for p in pages)
    print(f"Страниц: {len(pages)}, символов: {total}")
    if not pages:
        print("Текст не извлечён.")
        return
    sample = ""
    for p in pages:
        sample += p.get("text") or ""
        if len(sample) >= max_chars:
            break
    sample = sample[:max_chars]
    print("--- начало текста ---")
    print(sample)
    if total > max_chars:
        print(f"... (ещё {total - max_chars} симв.)")
    needles = ("служебн", "информац", "виды", "вид ")
    print("--- вхождения ---")
    low = sample.lower()
    for n in needles:
        print(f"  '{n}': {low.count(n)}")


def _print_chunks_for_file(rag: LawyerRAG, file_id: str, filename: str, query: str) -> None:
    core = core_query_tokens(query) if query else []
    need = min_core_matches_required(core) if core else 0
    print(f"\n=== Чанки в индексе: {filename} (id={file_id}) ===")
    if core:
        print(f"Запрос core: {core} (нужно совпадений ≥ {need})")
        print(f"Основы: {[word_stem(w) for w in core]}")

    try:
        part = rag._collection.get(
            where={"file_id": file_id},
            include=["documents", "metadatas"],
        )
    except Exception as e:
        print(f"Ошибка Chroma: {e}")
        return

    docs = part.get("documents") or []
    metas = part.get("metadatas") or []
    if not docs:
        print("В индексе нет чанков для этого file_id.")
        return

    rows: list[tuple[int, int, int, str, str]] = []
    for doc, meta in zip(docs, metas):
        if not doc or not meta:
            continue
        cm = count_core_matches(core, doc) if core else 0
        idx = int(meta.get("chunk_index") or 0)
        page = int(meta.get("page") or 1)
        preview = (doc or "").replace("\n", " ")[:120]
        rows.append((cm, idx, page, preview, doc))

    rows.sort(key=lambda r: (-r[0], r[1]))
    print(f"Всего чанков: {len(rows)}")
    print("Топ-8 по core_matches:")
    for cm, idx, page, preview, full in rows[:8]:
        flag = " ✓" if core and cm >= need else ""
        print(f"  [{idx}] стр.{page} matches={cm}{flag}: {preview}…")

    if query:
        for sub in query_search_substrings(query, core)[:6]:
            if len(sub) < 8:
                continue
            hit = sum(1 for _, _, _, _, d in rows if sub in d.lower())
            if hit:
                print(f"  подстрока «{sub[:50]}…» → в {hit} чанках")

    _, _, page, _, first = rows[0]
    embed_preview = build_chunk_embed_text(filename, page, first[:800], max_chars=400)
    print("\n--- пример текста для эмбеддинга (с заголовком) ---")
    print(embed_preview[:500])
    print("--- в Chroma в поле document хранится только тело чанка, без [имя файла] ---")


def _print_search_ranking(rag: LawyerRAG, query: str, top: int) -> None:
    print(f"\n=== Ранжирование поиска: «{query}» ===")
    hits = rag.search(query, top_k=top)
    if not hits:
        print("Нет результатов (пустая база или всё отфильтровано).")
        return
    for i, h in enumerate(hits[:top], 1):
        print(
            f"{i}. {h.get('filename')} стр.{h.get('page')} "
            f"score={h.get('score', 0):.3f} sem={h.get('semantic_score', 0):.3f} "
            f"kw={h.get('keyword_score', 0):.1f} core={h.get('core_matches')} "
            f"phrase={h.get('phrase_score', 0):.0f} stem={h.get('stem_score', 0):.0f}"
        )
        print(f"   {(h.get('text') or '')[:140].replace(chr(10), ' ')}…")

    by_file: dict[str, int] = {}
    for h in hits:
        fn = h.get("filename") or "?"
        by_file[fn] = by_file.get(fn, 0) + 1
    print("\nФайлы в топе:", by_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Диагностика Юриста: файл и поиск")
    parser.add_argument("query", nargs="?", default="", help="Текст запроса")
    parser.add_argument(
        "--file",
        default="порядке",
        help="Подстрока в имени файла в lawyer/uploaded/",
    )
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="Показать извлечение с диска (без индекса)",
    )
    parser.add_argument("--top", type=int, default=12, help="Сколько hit показать")
    args = parser.parse_args()

    rag = LawyerRAG()
    files = rag.list_files()
    print("Файлы в индексе:")
    for f in files:
        print(f"  {f['file_id']}: {f['filename']}")

    matches = [f for f in files if args.file.lower() in f["filename"].lower()]
    if not matches:
        print(f"\nВ индексе нет файла с «{args.file}» в имени.")
    else:
        for f in matches:
            _print_chunks_for_file(rag, f["file_id"], f["filename"], args.query)

    uploaded = _find_uploaded(args.file)
    if uploaded:
        for path in uploaded:
            if args.reparse or not matches:
                _print_extraction(path)
            orig = path.name.split("_", 1)[-1] if "_" in path.name else path.name
            if args.reparse:
                try:
                    fid, chunks = process_upload(path, orig)
                    print(f"\nПовторная нарезка (не в индекс): {len(chunks)} чанков, id={fid}")
                    if args.query:
                        core = core_query_tokens(args.query)
                        scored = sorted(
                            (
                                count_core_matches(core, c["text"]),
                                c["metadata"]["chunk_index"],
                                c["text"][:100],
                            )
                            for c in chunks
                        )
                        scored.sort(reverse=True)
                        print("Топ чанков по core_matches (с диска):")
                        for cm, idx, prev in scored[:5]:
                            print(f"  [{idx}] matches={cm}: {prev}…")
                except Exception as e:
                    print(f"Ошибка process_upload: {e}")
    else:
        print(f"\nВ lawyer/uploaded/ нет файла с «{args.file}».")

    if args.query:
        _print_search_ranking(rag, args.query, args.top)


if __name__ == "__main__":
    main()
