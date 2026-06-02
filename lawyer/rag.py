"""RAG для юридической базы знаний."""
import logging
from typing import Any

import chromadb

from config import CHROMA_PERSIST_DIR, LAWYER_UPLOAD_DIR
from core.embedding import get_embedder
from lawyer.search_utils import (
    combined_score,
    core_query_tokens,
    count_core_matches,
    expand_query_phrases,
    expand_query_tokens,
    keyword_score,
    min_core_matches_required,
    phrase_bonus,
)
from lawyer.text_encoding import repair_citation_text, repair_text

logger = logging.getLogger(__name__)

COLLECTION_NAME = "lawyer_kb"
# Сколько кандидатов собрать перед отбором в контекст LLM
RETRIEVE_K = 48
# Сколько фрагментов отдать в LLM
CONTEXT_K = 6
# Минимальный комбинированный score (отсекаем явный мусор)
MIN_COMBINED_SCORE = 0.08
# Порог для показа источника (доля от лучшего score)
MIN_CITATION_SCORE_RATIO = 0.45
# Минимум кандидатов с каждого файла при нескольких документах в базе
PER_FILE_SEMANTIC_K = 18
# Полный перебор чанков для ключевых слов (если база небольшая)
KEYWORD_SCAN_MAX_CHUNKS = 8000


def _chunk_key(meta: dict[str, Any]) -> str:
    return f"{meta.get('file_id', '')}_{meta.get('chunk_index', 0)}"


def _hit_key(hit: dict[str, Any]) -> str:
    return f"{hit.get('file_id', '')}_{hit.get('chunk_index', 0)}"


def _balance_hits_by_file(
    hits: list[dict[str, Any]],
    file_ids: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Не отдавать весь топ одному файлу, если в базе несколько документов."""
    if limit <= 0 or not hits:
        return []
    if len(file_ids) <= 1:
        return hits[:limit]

    by_file: dict[str, list[dict[str, Any]]] = {fid: [] for fid in file_ids}
    for hit in hits:
        fid = hit.get("file_id") or ""
        if fid in by_file:
            by_file[fid].append(hit)

    balanced: list[dict[str, Any]] = []
    seen: set[str] = set()

    for fid in file_ids:
        pool = by_file.get(fid) or []
        if pool:
            balanced.append(pool[0])
            seen.add(_hit_key(pool[0]))

    for hit in hits:
        if len(balanced) >= limit:
            break
        key = _hit_key(hit)
        if key in seen:
            continue
        balanced.append(hit)
        seen.add(key)

    return balanced[:limit]


class LawyerRAG:
    def __init__(self):
        CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder: Any = None
        self._files: dict[str, str] = {}
        self._load_file_registry()

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def _load_file_registry(self) -> None:
        if self._collection.count() == 0:
            return
        try:
            all_meta = self._collection.get(include=["metadatas"])
            for meta in all_meta.get("metadatas", []) or []:
                if meta:
                    fid = meta.get("file_id")
                    fname = meta.get("filename")
                    if fid and fname:
                        self._files[fid] = fname
        except Exception as e:
            logger.warning("Не удалось восстановить реестр файлов: %s", e)

    def add_chunks(self, chunks: list[dict[str, Any]]) -> int:
        if not chunks:
            return 0

        valid = [c for c in chunks if str(c.get("text") or "").strip()]
        if not valid:
            logger.warning("add_chunks: все фрагменты пустые после обработки")
            return 0

        file_id = valid[0]["metadata"]["file_id"]
        filename = valid[0]["metadata"]["filename"]
        self._files[file_id] = filename

        ids = [c["id"] for c in valid]
        documents = [str(c["text"]) for c in valid]
        metadatas = [c["metadata"] for c in valid]
        embeddings = self.embedder.embed(documents)

        self._collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        return len(valid)

    def list_files(self) -> list[dict[str, str]]:
        return [{"file_id": fid, "filename": fn} for fid, fn in self._files.items()]

    def delete_file(self, file_id: str) -> bool:
        if file_id not in self._files:
            return False

        results = self._collection.get(where={"file_id": file_id})
        if results and results["ids"]:
            self._collection.delete(ids=results["ids"])

        del self._files[file_id]

        for path in LAWYER_UPLOAD_DIR.glob(f"{file_id}_*"):
            path.unlink(missing_ok=True)

        return True

    def clear_all(self) -> None:
        try:
            self._client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._files.clear()
        for path in LAWYER_UPLOAD_DIR.iterdir():
            if path.is_file():
                path.unlink()

    def _merge_semantic_hit(
        self,
        merged: dict[str, dict[str, Any]],
        doc: str,
        meta: dict[str, Any],
        dist: float,
    ) -> None:
        chunk_id = _chunk_key(meta)
        sem = max(0.0, 1.0 - float(dist))
        prev = merged.get(chunk_id)
        if prev is None or sem > prev["semantic_score"]:
            merged[chunk_id] = {
                "id": chunk_id,
                "text": doc,
                "filename": meta.get("filename", ""),
                "page": int(meta.get("page") or 1),
                "chunk_index": meta.get("chunk_index", 0),
                "file_id": meta.get("file_id", ""),
                "semantic_score": sem,
                "keyword_score": 0.0,
            }

    def _semantic_candidates_scoped(
        self,
        query: str,
        n: int,
        *,
        file_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        count = self._collection.count()
        if count == 0:
            return {}

        n = min(n, count)
        merged: dict[str, dict[str, Any]] = {}
        where = {"file_id": file_id} if file_id else None

        for phrase in expand_query_phrases(query):
            query_emb = self.embedder.embed_query(phrase)
            kwargs: dict[str, Any] = {
                "query_embeddings": [query_emb],
                "n_results": n,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where
            try:
                results = self._collection.query(**kwargs)
            except Exception as e:
                logger.warning("Chroma query failed (file_id=%s): %s", file_id, e)
                continue
            if not results or not results["documents"]:
                continue

            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                self._merge_semantic_hit(merged, doc, meta, dist)

        return merged

    def _semantic_candidates(self, query: str, n: int) -> dict[str, dict[str, Any]]:
        """Кандидаты по эмбеддингам; при нескольких файлах — отдельно по каждому."""
        file_ids = list(self._files.keys())
        if len(file_ids) <= 1:
            return self._semantic_candidates_scoped(query, n)

        merged: dict[str, dict[str, Any]] = {}
        per_file_n = min(
            max(PER_FILE_SEMANTIC_K, n // len(file_ids) + 4),
            self._collection.count(),
        )
        for fid in file_ids:
            sub = self._semantic_candidates_scoped(query, per_file_n, file_id=fid)
            for key, hit in sub.items():
                prev = merged.get(key)
                if prev is None or hit["semantic_score"] > prev["semantic_score"]:
                    merged[key] = hit
        logger.debug(
            "Семантический поиск по %d файлам: %d уникальных чанков",
            len(file_ids),
            len(merged),
        )
        return merged

    def _keyword_candidates(
        self,
        query_tokens: list[str],
        limit: int = 40,
    ) -> dict[str, dict[str, Any]]:
        """Кандидаты по совпадению слов (опечатки, «бизне» → «бизнес»)."""
        if not query_tokens:
            return {}

        count = self._collection.count()
        if count == 0:
            return {}

        merged: dict[str, dict[str, Any]] = {}
        seen_ids: set[str] = set()

        # Подстрока в Chroma для длинных токенов
        for token in query_tokens:
            if len(token) < 3:
                continue
            try:
                part = self._collection.get(
                    where_document={"$contains": token},
                    include=["documents", "metadatas"],
                    limit=limit,
                )
            except Exception:
                part = None
            if part and part.get("ids"):
                for doc, meta in zip(part["documents"], part["metadatas"]):
                    cid = _chunk_key(meta)
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    ks = keyword_score(query_tokens, doc or "")
                    if ks > 0:
                        merged[cid] = {
                            "id": cid,
                            "text": doc,
                            "filename": meta.get("filename", ""),
                            "page": int(meta.get("page") or 1),
                            "chunk_index": meta.get("chunk_index", 0),
                            "file_id": meta.get("file_id", ""),
                            "semantic_score": 0.0,
                            "keyword_score": ks,
                        }

        # Полный перебор при небольшой базе — надёжнее для опечаток и OCR
        if count <= KEYWORD_SCAN_MAX_CHUNKS:
            all_data = self._collection.get(include=["documents", "metadatas"])
            for doc, meta in zip(
                all_data.get("documents") or [],
                all_data.get("metadatas") or [],
            ):
                if not doc:
                    continue
                cid = _chunk_key(meta)
                ks = keyword_score(query_tokens, doc)
                if ks <= 0:
                    continue
                prev = merged.get(cid)
                if prev is None or ks > prev["keyword_score"]:
                    merged[cid] = {
                        "id": cid,
                        "text": doc,
                        "filename": meta.get("filename", ""),
                        "page": int(meta.get("page") or 1),
                        "chunk_index": meta.get("chunk_index", 0),
                        "file_id": meta.get("file_id", ""),
                        "semantic_score": prev["semantic_score"] if prev else 0.0,
                        "keyword_score": ks,
                    }

        return merged

    def search(self, query: str, top_k: int = CONTEXT_K) -> list[dict[str, Any]]:
        if self._collection.count() == 0:
            return []

        query_tokens = expand_query_tokens(query)
        core = core_query_tokens(query)
        candidates = self._semantic_candidates(query, RETRIEVE_K)
        kw = self._keyword_candidates(query_tokens)

        for cid, hit in kw.items():
            if cid in candidates:
                candidates[cid]["keyword_score"] = max(
                    candidates[cid]["keyword_score"],
                    hit["keyword_score"],
                )
            else:
                candidates[cid] = hit

        for hit in candidates.values():
            ks = hit["keyword_score"] + phrase_bonus(core, hit["text"])
            hit["keyword_score"] = ks
            core_n = count_core_matches(core, hit["text"])
            hit["core_matches"] = core_n
            ratio = (core_n / len(core)) if core else 0.0
            hit["score"] = combined_score(
                hit["semantic_score"],
                ks,
                ratio,
            )

        ranked = sorted(
            candidates.values(),
            key=lambda h: (
                h.get("core_matches", 0),
                h["score"],
                h["keyword_score"],
                h["semantic_score"],
            ),
            reverse=True,
        )

        # Сначала фрагменты, где есть все слова запроса (бизнес + план)
        if len(core) >= 2:
            strong = [h for h in ranked if h.get("core_matches", 0) >= len(core)]
            if strong:
                ranked = strong + [h for h in ranked if h not in strong]

        min_core = min_core_matches_required(core)
        pool = [
            h
            for h in ranked
            if h["score"] >= MIN_COMBINED_SCORE
            or h.get("core_matches", 0) >= min_core
            or float(h.get("keyword_score") or 0) >= 3.0
        ]
        if not pool and ranked:
            pool = ranked[: max(top_k * 2, len(self._files) * 3)]
        else:
            pool = pool[: max(top_k * 4, len(self._files) * 6)]

        # Сильные совпадения по словам — в начало, даже если эмбеддинг слабый
        pool.sort(
            key=lambda h: (
                h.get("core_matches", 0),
                h.get("keyword_score", 0),
                h["score"],
            ),
            reverse=True,
        )

        filtered = _balance_hits_by_file(pool, list(self._files.keys()), top_k)

        best = filtered[0] if filtered else {}
        files_in_result = {h.get("file_id") for h in filtered}
        logger.info(
            "Поиск «%s»: core=%s, кандидатов=%d, в контекст=%d, "
            "файлов=%d, score=%.3f, kw=%.1f, core_match=%s/%s",
            query[:50],
            core,
            len(candidates),
            len(filtered),
            len(files_in_result),
            best.get("score", 0),
            best.get("keyword_score", 0),
            best.get("core_matches", 0),
            len(core),
        )

        return [
            {
                "text": repair_citation_text(h["text"] or ""),
                "filename": repair_text(h["filename"] or ""),
                "page": int(h.get("page") or 1),
                "file_id": h["file_id"],
                "chunk_index": h.get("chunk_index", 0),
                "score": h["score"],
                "keyword_score": h["keyword_score"],
                "core_matches": h.get("core_matches", 0),
            }
            for h in filtered
        ]
