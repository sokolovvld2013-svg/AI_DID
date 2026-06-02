"""RAG-индексация и поиск по плану и справочнику статей."""

import logging

import uuid

from typing import Any



import chromadb

import pandas as pd



from config import CHROMA_PERSIST_DIR

from core.embedding import get_embedder

from economist.data_loader import (

    ARTICLE_NAME_COL,

    CODE_COL,

    DESCRIPTION_COL,

    catalog_row_to_text,

    row_to_text,

)



logger = logging.getLogger(__name__)



COLLECTION_PLAN = "economist_plan"

COLLECTION_CATALOG = "economist_catalog"





class EconomistRAG:

    def __init__(self, collection_name: str):

        self._collection_name = collection_name

        CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))

        self._collection = self._client.get_or_create_collection(

            name=collection_name,

            metadata={"hnsw:space": "cosine"},

        )

        self._embedder = get_embedder()



    def clear(self) -> None:

        try:

            self._client.delete_collection(self._collection_name)

        except Exception:

            pass

        self._collection = self._client.get_or_create_collection(

            name=self._collection_name,

            metadata={"hnsw:space": "cosine"},

        )



    def index_dataframe(self, df: pd.DataFrame, source_file: str) -> int:

        """Индексация строк планового DataFrame."""

        self.clear()

        if df.empty:

            return 0



        ids, documents, metadatas = [], [], []

        for idx, row in df.iterrows():

            text = row_to_text(row)

            if not text.strip():

                continue

            article_name = str(

                row.get(ARTICLE_NAME_COL, row.get("Статья", ""))

            )

            meta = {

                "source": source_file,

                "row_index": int(idx),

                "article": article_name,

                "code": str(row.get(CODE_COL, "")),

                "group": str(row.get("Группа статьи", "")),

                "object": str(row.get("Объект", "")),

            }

            ids.append(str(uuid.uuid4()))

            documents.append(text)

            metadatas.append(meta)



        return self._add_batches(ids, documents, metadatas)



    def index_catalog(self, df: pd.DataFrame, source_file: str) -> int:

        """Индексация справочника статей по колонке «Описание статьи»."""

        self.clear()

        if df.empty:

            return 0



        ids, documents, metadatas = [], [], []

        for idx, row in df.iterrows():

            text = catalog_row_to_text(row)

            if not text.strip():

                continue

            meta = {

                "source": source_file,

                "row_index": int(idx),

                "article": str(row.get(ARTICLE_NAME_COL, "")),

                "code": str(row.get(CODE_COL, "")),

                "group": str(row.get("Группа статьи", "")),

            }

            ids.append(str(uuid.uuid4()))

            documents.append(text)

            metadatas.append(meta)



        count = self._add_batches(ids, documents, metadatas)

        logger.info("Проиндексировано %d статей справочника", count)

        return count



    def _add_batches(

        self,

        ids: list[str],

        documents: list[str],

        metadatas: list[dict],

    ) -> int:

        if not documents:

            return 0



        batch_size = 64

        for i in range(0, len(documents), batch_size):

            batch_docs = documents[i : i + batch_size]

            batch_ids = ids[i : i + batch_size]

            batch_meta = metadatas[i : i + batch_size]

            embeddings = self._embedder.embed(batch_docs)

            self._collection.add(

                ids=batch_ids,

                documents=batch_docs,

                metadatas=batch_meta,

                embeddings=embeddings,

            )

        return len(documents)



    def search_by_description(self, description: str, top_k: int = 5) -> list[dict[str, Any]]:

        """Поиск наиболее подходящей статьи по описанию."""

        if self._collection.count() == 0:

            return []



        query_emb = self._embedder.embed_query(description)

        results = self._collection.query(

            query_embeddings=[query_emb],

            n_results=min(top_k, self._collection.count()),

            include=["documents", "metadatas", "distances"],

        )



        hits = []

        if results and results["documents"]:

            for doc, meta, dist in zip(

                results["documents"][0],

                results["metadatas"][0],

                results["distances"][0],

            ):

                hits.append({

                    "text": doc,

                    "article": meta.get("article", ""),

                    "code": meta.get("code", ""),

                    "group": meta.get("group", ""),

                    "object": meta.get("object", ""),

                    "source": meta.get("source", ""),

                    "row_index": meta.get("row_index"),

                    "score": 1 - dist,

                })

        return hits


