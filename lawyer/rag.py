"""RAG для юридической базы знаний."""
import logging
import os
from typing import Any

import chromadb

from config import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_PROVIDER,
    GIGACHAT_MAX_EMBED_CHARS,
    LAWYER_BALANCE_FILES,
    LAWYER_CONTEXT_MERGE_NEIGHBORS,
    LAWYER_LLM_QUERY_REWRITE,
    LAWYER_SEMANTIC_MIN_SCORE,
    LAWYER_SEMANTIC_TOP_K,
    LAWYER_SEMANTIC_WEIGHT,
    LAWYER_UPLOAD_DIR,
)
from core.embedding import get_embedder
from core.llm_client import get_llm
from lawyer.search_utils import (
    combined_score,
    core_query_tokens,
    count_core_matches,
    enrich_query_for_embedding,
    expand_query_phrases,
    expand_query_tokens,
    keyword_score,
    keyword_score_core,
    min_core_matches_required,
    phrase_bonus,
    core_stems_proximity_score,
    query_phrase_score_with_context,
    query_search_substrings,
    query_stem_search_terms,
    reciprocal_rank_fusion,
    stem_match_count,
)
from lawyer.text_encoding import repair_citation_text, repair_text

logger = logging.getLogger(__name__)

COLLECTION_NAME = "lawyer_kb"
# Сколько кандидатов собрать перед отбором в контекст LLM
RETRIEVE_K = int(os.getenv("LAWYER_RETRIEVE_K", "80"))
RETRIEVE_K_MAX = int(os.getenv("LAWYER_RETRIEVE_K_MAX", "180"))
# Сколько фрагментов отдать в LLM
CONTEXT_K = int(os.getenv("LAWYER_CONTEXT_K", "8"))
# Минимальный комбинированный score (отсекаем явный мусор)
MIN_COMBINED_SCORE = float(os.getenv("LAWYER_MIN_COMBINED_SCORE", "0.12"))
# Порог для показа источника (доля от лучшего score)
MIN_CITATION_SCORE_RATIO = float(os.getenv("LAWYER_CITATION_SCORE_RATIO", "0.4"))
# Минимум кандидатов с каждого файла при нескольких документах в базе
PER_FILE_SEMANTIC_K = int(os.getenv("LAWYER_PER_FILE_SEMANTIC_K", "24"))
# Полный перебор чанков для ключевых слов (если база небольшая)
KEYWORD_SCAN_MAX_CHUNKS = int(os.getenv("LAWYER_KEYWORD_SCAN_MAX", "12000"))
NEIGHBOR_SEEDS = 10


def build_chunk_embed_text(
    filename: str,
    page: int,
    text: str,
    max_chars: int | None = None,
) -> str:
    """Текст для эмбеддинга: имя документа + страница + содержимое (head+tail при лимите)."""
    body = (text or "").strip()
    title = repair_text(filename or "").strip()
    header = f"[{title}] стр. {page}\n" if title else ""
    if not body:
        return (header.rstrip() or title or ".")

    full = f"{header}{body}"
    if not max_chars or len(full) <= max_chars:
        return full

    body_budget = max(220, max_chars - len(header) - 8)
    if len(body) <= body_budget:
        return f"{header}{body}"

    head_len = max(140, body_budget * 2 // 3)
    tail_len = max(80, body_budget - head_len - 5)
    if head_len + tail_len + 5 > body_budget:
        tail_len = max(60, body_budget - head_len - 5)
    snippet = f"{body[:head_len]}\n...\n{body[-tail_len:]}"
    return f"{header}{snippet}"


def _embedding_query_phrases(query: str) -> list[str]:
    """Фразы для embed_query: обогащение + опционально LLM-перефраз."""
    phrases = expand_query_phrases(query)
    enriched = enrich_query_for_embedding(query)
    if enriched not in phrases:
        phrases.insert(0, enriched)

    if not LAWYER_LLM_QUERY_REWRITE:
        return phrases

    try:
        llm = get_llm()
        paraphrase = llm.generate(
            query,
            system_prompt=(
                "Переформулируй вопрос как поисковый запрос к базе внутренних "
                "регламентов и приказов. Одно-два предложения, только тематические "
                "термины, без ответа и без пояснений."
            ),
        ).strip()
        if paraphrase and paraphrase.lower() != query.strip().lower():
            phrases.insert(0, enrich_query_for_embedding(paraphrase))
            phrases.insert(0, paraphrase)
            logger.debug("LLM embed-query: %s", paraphrase[:120])
    except Exception as e:
        logger.debug("LLM query rewrite skipped: %s", e)

    seen: set[str] = set()
    unique: list[str] = []
    for p in phrases:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _chunk_key(meta: dict[str, Any]) -> str:
    return f"{meta.get('file_id', '')}_{meta.get('chunk_index', 0)}"


def _hit_key(hit: dict[str, Any]) -> str:
    return f"{hit.get('file_id', '')}_{hit.get('chunk_index', 0)}"


def _balance_hits_by_file(
    hits: list[dict[str, Any]],
    file_ids: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Равномерно: несколько лучших чанков с каждого файла, не только с самого большого."""
    if limit <= 0 or not hits:
        return []
    if not LAWYER_BALANCE_FILES or len(file_ids) <= 1:
        return hits[:limit]

    by_file: dict[str, list[dict[str, Any]]] = {fid: [] for fid in file_ids}
    for hit in hits:
        fid = hit.get("file_id") or ""
        if fid in by_file:
            by_file[fid].append(hit)

    n_files = len(file_ids)
    min_per_file = max(1, limit // n_files)
    if limit >= 4 and n_files >= 2:
        min_per_file = max(2, min_per_file)

    balanced: list[dict[str, Any]] = []
    seen: set[str] = set()

    for fid in file_ids:
        pool = sorted(
            by_file.get(fid) or [],
            key=lambda h: (
                h.get("phrase_score", 0),
                h.get("core_matches", 0),
                h.get("score", 0),
            ),
            reverse=True,
        )
        for hit in pool[:min_per_file]:
            key = _hit_key(hit)
            if key in seen:
                continue
            balanced.append(hit)
            seen.add(key)

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
        count = self._collection.count()
        if count == 0:
            self._files.clear()
            return
        try:
            found: dict[str, str] = {}
            batch_size = 5000
            offset = 0
            while offset < count:
                part = self._collection.get(
                    include=["metadatas"],
                    limit=min(batch_size, count - offset),
                    offset=offset,
                )
                offset += batch_size
                for meta in part.get("metadatas", []) or []:
                    if meta:
                        fid = meta.get("file_id")
                        fname = meta.get("filename")
                        if fid and fname:
                            found[fid] = fname
            self._files = found
        except TypeError:
            # Старые версии Chroma без offset
            try:
                part = self._collection.get(
                    include=["metadatas"],
                    limit=count,
                )
                for meta in part.get("metadatas", []) or []:
                    if meta:
                        fid = meta.get("file_id")
                        fname = meta.get("filename")
                        if fid and fname:
                            self._files[fid] = fname
            except Exception as e:
                logger.warning("Не удалось восстановить реестр файлов: %s", e)
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
        embed_limit = (
            GIGACHAT_MAX_EMBED_CHARS
            if EMBEDDING_PROVIDER == "gigachat"
            else None
        )
        embed_inputs = [
            build_chunk_embed_text(
                str(c["metadata"].get("filename") or ""),
                int(c["metadata"].get("page") or 1),
                str(c["text"]),
                max_chars=embed_limit,
            )
            for c in valid
        ]
        embeddings = self.embedder.embed(embed_inputs)

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

    def _effective_retrieve_k(self) -> int:
        """Больше кандидатов в больших базах (иначе теряются релевантные чанки)."""
        total = self._collection.count()
        if total <= RETRIEVE_K:
            return total
        scaled = max(RETRIEVE_K, min(RETRIEVE_K_MAX, total // 6))
        return min(total, scaled)

    def _attach_neighbor_chunks(
        self,
        merged: dict[str, dict[str, Any]],
        core: list[str],
        query_tokens: list[str],
    ) -> None:
        """Соседние чанки того же файла — контекст вокруг точного попадания."""
        seeds = sorted(
            merged.values(),
            key=lambda h: (
                h.get("core_matches", 0),
                h.get("score", 0),
                h.get("keyword_score", 0),
            ),
            reverse=True,
        )[:NEIGHBOR_SEEDS]

        neighbor_ids: list[str] = []
        for hit in seeds:
            fid = hit.get("file_id") or ""
            idx = int(hit.get("chunk_index") or 0)
            if not fid:
                continue
            for delta in (-1, 1):
                nid = f"{fid}_{idx + delta}"
                if nid not in merged:
                    neighbor_ids.append(nid)

        if not neighbor_ids:
            return

        try:
            part = self._collection.get(
                ids=neighbor_ids,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.debug("Соседние чанки: %s", e)
            return

        for doc, meta in zip(
            part.get("documents") or [],
            part.get("metadatas") or [],
        ):
            if not doc or not meta:
                continue
            cid = _chunk_key(meta)
            ks = keyword_score(query_tokens, doc)
            cm = count_core_matches(core, doc) if core else 0
            merged[cid] = {
                "id": cid,
                "text": doc,
                "filename": meta.get("filename", ""),
                "page": int(meta.get("page") or 1),
                "chunk_index": meta.get("chunk_index", 0),
                "file_id": meta.get("file_id", ""),
                "semantic_score": 0.35,
                "keyword_score": ks,
                "core_matches": cm,
                "neighbor": True,
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

        phrases = _embedding_query_phrases(query)

        for phrase in phrases:
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

    def merge_neighbor_context(self, hit: dict[str, Any]) -> str:
        """Склейка с соседними чанками для LLM (parent-child lite)."""
        text = (hit.get("text") or "").strip()
        radius = LAWYER_CONTEXT_MERGE_NEIGHBORS
        if radius <= 0 or not text:
            return text

        fid = hit.get("file_id") or ""
        idx = int(hit.get("chunk_index") or 0)
        if not fid:
            return text

        neighbor_ids = [
            f"{fid}_{idx + delta}"
            for delta in range(-radius, radius + 1)
            if delta != 0
        ]
        try:
            part = self._collection.get(
                ids=neighbor_ids,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.debug("merge_neighbor_context: %s", e)
            return text

        pieces: list[tuple[int, str]] = [(idx, text)]
        for doc, meta in zip(
            part.get("documents") or [],
            part.get("metadatas") or [],
        ):
            if not doc or not meta:
                continue
            pieces.append((int(meta.get("chunk_index") or 0), str(doc).strip()))

        pieces.sort(key=lambda x: x[0])
        merged: list[str] = []
        seen: set[str] = set()
        for _, part_text in pieces:
            if not part_text or part_text in seen:
                continue
            seen.add(part_text)
            merged.append(part_text)
        return "\n\n".join(merged)

    def _semantic_candidates(self, query: str, n: int) -> dict[str, dict[str, Any]]:
        """Кандидаты по эмбеддингам; при нескольких файлах — отдельно по каждому."""
        file_ids = list(self._files.keys())
        if len(file_ids) <= 1:
            return self._semantic_candidates_scoped(query, n)

        merged: dict[str, dict[str, Any]] = {}
        per_file_n = min(
            max(PER_FILE_SEMANTIC_K, n // len(file_ids) + 8),
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

    def _merge_keyword_hit(
        self,
        merged: dict[str, dict[str, Any]],
        doc: str,
        meta: dict[str, Any],
        query_tokens: list[str],
    ) -> None:
        cid = _chunk_key(meta)
        ks = keyword_score(query_tokens, doc)
        if ks <= 0:
            return
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

    def _keyword_candidates_scoped(
        self,
        query_tokens: list[str],
        limit: int,
        *,
        file_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not query_tokens:
            return {}

        merged: dict[str, dict[str, Any]] = {}
        seen_ids: set[str] = set()
        where_file = {"file_id": file_id} if file_id else None

        for token in query_tokens:
            if len(token) < 3:
                continue
            try:
                kwargs: dict[str, Any] = {
                    "where_document": {"$contains": token},
                    "include": ["documents", "metadatas"],
                    "limit": limit,
                }
                if where_file:
                    kwargs["where"] = where_file
                part = self._collection.get(**kwargs)
            except Exception:
                part = None
            if not part or not part.get("ids"):
                continue
            for doc, meta in zip(part["documents"], part["metadatas"]):
                cid = _chunk_key(meta)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                self._merge_keyword_hit(merged, doc or "", meta, query_tokens)

        try:
            kwargs = {"include": ["documents", "metadatas"]}
            if where_file:
                kwargs["where"] = where_file
            all_data = self._collection.get(**kwargs)
        except Exception:
            all_data = None

        docs = (all_data or {}).get("documents") or []
        if docs and len(docs) <= KEYWORD_SCAN_MAX_CHUNKS:
            for doc, meta in zip(docs, (all_data or {}).get("metadatas") or []):
                if doc:
                    self._merge_keyword_hit(merged, doc, meta, query_tokens)

        return merged

    def _keyword_candidates(
        self,
        query_tokens: list[str],
        limit: int = 40,
        core: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Кандидаты по словам; при нескольких файлах — отдельно по каждому."""
        stem_terms = query_stem_search_terms(core or [])
        merged_tokens: list[str] = []
        seen_t: set[str] = set()
        for t in list(query_tokens) + stem_terms:
            if len(t) >= 3 and t not in seen_t:
                seen_t.add(t)
                merged_tokens.append(t)
        query_tokens = merged_tokens

        if not query_tokens:
            return {}

        if self._collection.count() == 0:
            return {}

        file_ids = list(self._files.keys())
        if len(file_ids) <= 1:
            return self._keyword_candidates_scoped(query_tokens, limit)

        merged: dict[str, dict[str, Any]] = {}
        per_limit = max(25, limit // len(file_ids) + 8)
        for fid in file_ids:
            sub = self._keyword_candidates_scoped(
                query_tokens, per_limit, file_id=fid
            )
            for cid, hit in sub.items():
                prev = merged.get(cid)
                if prev is None or hit["keyword_score"] > prev["keyword_score"]:
                    merged[cid] = hit
        return merged

    def _phrase_contains_candidates(
        self,
        query: str,
        core: list[str],
        limit_per_phrase: int = 15,
    ) -> dict[str, dict[str, Any]]:
        """Прямой поиск подстроки в Chroma — не теряется из‑за границ чанков при ранжировании."""
        merged: dict[str, dict[str, Any]] = {}
        file_ids = list(self._files.keys()) or [None]

        search_terms = list(query_search_substrings(query, core))
        search_terms.extend(query_stem_search_terms(core))

        for phrase in search_terms:
            if len(phrase) < 8:
                continue
            for fid in file_ids:
                try:
                    kwargs: dict[str, Any] = {
                        "where_document": {"$contains": phrase},
                        "include": ["documents", "metadatas"],
                        "limit": limit_per_phrase,
                    }
                    if fid:
                        kwargs["where"] = {"file_id": fid}
                    part = self._collection.get(**kwargs)
                except Exception:
                    continue
                if not part or not part.get("ids"):
                    continue
                for doc, meta in zip(part["documents"], part["metadatas"]):
                    if not doc or not meta:
                        continue
                    self._merge_semantic_hit(merged, doc, meta, 0.12)

        return merged

    def _refresh_phrase_scores(
        self,
        candidates: dict[str, dict[str, Any]],
        query: str,
        core: list[str],
    ) -> None:
        """Пересчёт phrase_score с учётом соседних чанков (фраза через границу 600 символов)."""
        for cid, hit in list(candidates.items()):
            fid = hit.get("file_id") or ""
            idx = int(hit.get("chunk_index") or 0)
            neighbors: list[str] = []
            for delta in (-1, 1):
                nhit = candidates.get(f"{fid}_{idx + delta}")
                if nhit:
                    neighbors.append(nhit.get("text") or "")
            merged = " ".join([hit.get("text") or ""] + neighbors)
            hit["phrase_score"] = query_phrase_score_with_context(
                query,
                core,
                hit.get("text") or "",
                neighbors,
            )
            hit["stem_score"] = core_stems_proximity_score(core, merged)

    def _pool_seed_per_file(
        self,
        candidates: dict[str, dict[str, Any]],
        per_file: int = 4,
    ) -> list[dict[str, Any]]:
        """Лучшие чанки с каждого файла — в пул до общей фильтрации."""
        if len(self._files) <= 1:
            return []

        by_file: dict[str, list[dict[str, Any]]] = {}
        for hit in candidates.values():
            fid = hit.get("file_id") or ""
            if fid:
                by_file.setdefault(fid, []).append(hit)

        seed: list[dict[str, Any]] = []
        for fid in self._files:
            pool = sorted(
                by_file.get(fid, []),
                key=lambda h: (
                    h.get("phrase_score", 0),
                    h.get("stem_score", 0),
                    h.get("core_matches", 0),
                    h["score"],
                ),
                reverse=True,
            )
            seed.extend(pool[:per_file])
        return seed

    def _inject_best_per_file(
        self,
        pool: list[dict[str, Any]],
        candidates: dict[str, dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """В пул — лучший чанк с каждого файла (фраза или близкие основы слов)."""
        if len(self._files) <= 1:
            return pool

        def _rank_key(h: dict[str, Any]) -> tuple:
            return (
                h.get("phrase_score", 0),
                h.get("stem_score", 0),
                h.get("core_matches", 0),
                h["score"],
            )

        best_by_file: dict[str, dict[str, Any]] = {}
        for hit in candidates.values():
            fid = hit.get("file_id") or ""
            if not fid:
                continue
            prev = best_by_file.get(fid)
            if prev is None or _rank_key(hit) > _rank_key(prev):
                best_by_file[fid] = hit

        seen = {_hit_key(h) for h in pool}
        prefix: list[dict[str, Any]] = []
        for fid in self._files:
            hit = best_by_file.get(fid)
            if not hit:
                continue
            key = _hit_key(hit)
            if key in seen:
                continue
            if hit.get("phrase_score", 0) <= 0 and hit.get("stem_score", 0) < 20:
                sem = float(hit.get("semantic_score") or 0)
                if hit.get("core_matches", 0) < 2 and sem < LAWYER_SEMANTIC_MIN_SCORE * 0.85:
                    continue
            prefix.append(hit)
            seen.add(key)

        prefix_keys = {_hit_key(p) for p in prefix}
        combined = prefix + [h for h in pool if _hit_key(h) not in prefix_keys]
        return combined[: max(limit * 3, len(self._files) * 4)]

    def search(self, query: str, top_k: int = CONTEXT_K) -> list[dict[str, Any]]:
        if self._collection.count() == 0:
            return []

        self._load_file_registry()
        query_tokens = expand_query_tokens(query)
        core = core_query_tokens(query)
        retrieve_k = self._effective_retrieve_k()
        candidates = self._semantic_candidates(query, retrieve_k)
        kw = self._keyword_candidates(
            query_tokens, limit=max(60, retrieve_k // 2), core=core
        )
        phrase_hits = self._phrase_contains_candidates(query, core)

        for cid, hit in phrase_hits.items():
            if cid in candidates:
                candidates[cid]["semantic_score"] = max(
                    candidates[cid]["semantic_score"],
                    hit["semantic_score"],
                )
            else:
                candidates[cid] = hit

        for cid, hit in kw.items():
            if cid in candidates:
                candidates[cid]["keyword_score"] = max(
                    candidates[cid]["keyword_score"],
                    hit["keyword_score"],
                )
            else:
                candidates[cid] = hit

        self._attach_neighbor_chunks(candidates, core, query_tokens)
        self._refresh_phrase_scores(candidates, query, core)

        for hit in candidates.values():
            if "score" not in hit:
                hit["semantic_score"] = float(hit.get("semantic_score") or 0.0)
            text = hit.get("text") or ""
            ps = float(hit.get("phrase_score") or 0.0)
            ss = float(hit.get("stem_score") or 0.0)
            core_kw = keyword_score_core(core, text) if core else 0.0
            hit["phrase_score"] = max(ps, ss)
            hit["stem_score"] = ss
            hit["stem_matches"] = stem_match_count(core, text)
            hit["keyword_score"] = (
                core_kw
                + phrase_bonus(core, text)
                + min(float(hit.get("keyword_score") or 0.0), 12.0)
            )
            core_n = count_core_matches(core, text)
            hit["core_matches"] = core_n
            ratio = (core_n / len(core)) if core else 0.0
            hit["score"] = combined_score(
                hit["semantic_score"],
                hit["keyword_score"],
                ratio,
                phrase_score=ps,
                semantic_weight=LAWYER_SEMANTIC_WEIGHT,
            )

        sem_ranked = sorted(
            candidates.values(),
            key=lambda h: h["semantic_score"],
            reverse=True,
        )
        kw_ranked = sorted(
            candidates.values(),
            key=lambda h: h["keyword_score"],
            reverse=True,
        )
        rrf = reciprocal_rank_fusion(
            [[h["id"] for h in sem_ranked], [h["id"] for h in kw_ranked]]
        )
        for hit in candidates.values():
            hit["rrf"] = rrf.get(hit["id"], 0.0)

        ranked = sorted(
            candidates.values(),
            key=lambda h: (
                h.get("semantic_score", 0),
                h.get("phrase_score", 0),
                h.get("stem_score", 0),
                h.get("stem_matches", 0),
                h.get("core_matches", 0) >= len(core) if core else False,
                h.get("core_matches", 0),
                h["score"],
                h.get("rrf", 0.0),
            ),
            reverse=True,
        )

        phrase_strong = [
            h
            for h in ranked
            if h.get("phrase_score", 0) >= 20 or h.get("stem_score", 0) >= 28
        ]
        if phrase_strong:
            ranked = phrase_strong + [h for h in ranked if h not in phrase_strong]
        elif len(core) >= 2:
            strong = sorted(
                [h for h in ranked if h.get("core_matches", 0) >= len(core)],
                key=lambda h: (h.get("phrase_score", 0), h["score"]),
                reverse=True,
            )
            if strong:
                ranked = strong + [h for h in ranked if h not in strong]

        # Топ по semantic не должен теряться из-за phrase/core фильтров
        sem_top = sem_ranked[: max(LAWYER_SEMANTIC_TOP_K, top_k)]
        sem_keys = {_hit_key(h) for h in sem_top}
        ranked = sem_top + [h for h in ranked if _hit_key(h) not in sem_keys]

        min_core = min_core_matches_required(core)
        sem_floor = max(0.35, LAWYER_SEMANTIC_MIN_SCORE * 0.85)
        pool_seed = self._pool_seed_per_file(candidates) if LAWYER_BALANCE_FILES else []
        seen_seed = {_hit_key(h) for h in pool_seed}
        pool = list(pool_seed) + [
            h
            for h in ranked
            if _hit_key(h) not in seen_seed
            and (
                h.get("core_matches", 0) >= min_core
                or h.get("stem_score", 0) >= 25
                or float(h.get("semantic_score") or 0) >= sem_floor
                or (
                    h["score"] >= MIN_COMBINED_SCORE
                    and (
                        h.get("core_matches", 0) >= 1
                        or float(h.get("keyword_score") or 0) >= 3.0
                        or float(h.get("semantic_score") or 0) >= 0.38
                    )
                )
            )
        ]
        if not pool and ranked:
            pool = [
                h
                for h in ranked[: max(top_k * 3, 12)]
                if h.get("core_matches", 0) >= 1
                or float(h.get("keyword_score") or 0) >= 2.0
                or float(h.get("semantic_score") or 0) >= sem_floor
            ]
        if not pool and ranked:
            pool = ranked[: max(top_k * 2, len(self._files) * 3)]
        else:
            pool = pool[: max(top_k * 5, len(self._files) * 8, retrieve_k // 3)]

        pool = self._inject_best_per_file(pool, candidates, top_k) if LAWYER_BALANCE_FILES else pool

        pool.sort(
            key=lambda h: (
                h.get("semantic_score", 0),
                h.get("phrase_score", 0),
                h.get("stem_score", 0),
                h.get("core_matches", 0),
                h.get("keyword_score", 0),
                h["score"],
                h.get("rrf", 0.0),
            ),
            reverse=True,
        )

        filtered = _balance_hits_by_file(pool, list(self._files.keys()), top_k)

        best = filtered[0] if filtered else {}
        files_in_result = {h.get("file_id") for h in filtered}
        logger.info(
            "Поиск «%s»: core=%s, retrieve_k=%d, кандидатов=%d, в контекст=%d, "
            "файлов в базе=%d, в ответе=%d, sem=%.3f, score=%.3f, phrase=%.0f, stem=%.0f, kw=%.1f, core=%s/%s",
            query[:50],
            core,
            retrieve_k,
            len(candidates),
            len(filtered),
            len(self._files),
            len(files_in_result),
            best.get("semantic_score", 0),
            best.get("score", 0),
            best.get("phrase_score", 0),
            best.get("stem_score", 0),
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
                "semantic_score": h.get("semantic_score", 0.0),
                "phrase_score": h.get("phrase_score", 0.0),
                "stem_score": h.get("stem_score", 0.0),
                "core_matches": h.get("core_matches", 0),
            }
            for h in filtered
        ]
