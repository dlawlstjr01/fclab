"""Vector Store 영역 — Chroma 컬렉션 두 개의 생성 · 조회, 청킹 · 임베딩 색인, 검색. (F2002 · F2005 · F2006)

    player_meta                  선수 카드 1장 = 청크 1개, 청크 ID = 선수 ID(spid)        (RAG 1)
    player_wiki_c{크기}_o{중복}   위키 문서 청크, 청크 ID = 선수 ID:문서 ID:순번             (RAG 2)

청크 ID 를 고정해 두었기 때문에 같은 것을 다시 색인해도 늘지 않고 덮어쓰기만 된다(upsert).
위키 청크 ID 에 선수 ID 를 넣은 이유: 여러 선수가 같은 문서(같은 클럽 · 같은 대회)를 모으면
문서 ID 만으로는 뒤에 색인한 선수의 청크가 오류 없이 조용히 덮어쓴다.
"""

from __future__ import annotations

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from data_prep.players import index_text, meta_metadata


def wiki_chunk_id(player_id: str, page_id: str, index: int) -> str:
    return f"{player_id}:{page_id}:{index}"


class PlayerStore:
    def __init__(self, settings, embeddings):
        self.settings = settings
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self.meta = Chroma(
            client=self._client, collection_name=settings.meta_collection, embedding_function=embeddings
        )
        self.wiki = Chroma(
            client=self._client, collection_name=settings.wiki_collection, embedding_function=embeddings
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
        )

    # ── 건수 ────────────────────────────────────────────────────────

    def meta_count(self) -> int:
        return self.meta._collection.count()

    def wiki_count(self) -> int:
        return self.wiki._collection.count()

    def collection_counts(self) -> dict[str, int]:
        return {c.name: c.count() for c in self._client.list_collections()}

    # ── RAG 1: 항목 메타데이터 ──────────────────────────────────────

    def index_players(self, players: list[dict], batch_size: int = 100, on_batch=None) -> int:
        """선수 카드마다 색인 텍스트 1개를 선수 ID 로 넣는다. 다시 실행해도 건수가 늘지 않는다. (F2002)"""
        for start in range(0, len(players), batch_size):
            batch = players[start : start + batch_size]
            self.meta.add_texts(
                [index_text(p) for p in batch],
                metadatas=[meta_metadata(p) for p in batch],
                ids=[p["player_id"] for p in batch],
            )
            if on_batch:
                on_batch(start + len(batch), len(players))
        return self.meta_count()

    def get_meta(self, player_id: str) -> Document | None:
        got = self.meta.get(ids=[player_id], include=["documents", "metadatas"])
        if not got["ids"]:
            return None
        return Document(id=got["ids"][0], page_content=got["documents"][0], metadata=got["metadatas"][0])

    def search_meta(self, query: str, k: int) -> list[Document]:
        return self.meta.similarity_search(query, k=k)

    # ── RAG 2: 외부 문서 ────────────────────────────────────────────

    def index_wiki_docs(self, player_id: str, docs: list[dict], batch_size: int = 500) -> int:
        """수집한 문서를 청킹해 색인한다. 이미 이 설정으로 색인한 문서는 건너뛴다. 새로 넣은 청크 수를 돌려준다. (F2005)

        여러 문서의 청크를 모아 한 번에 임베딩한다 (문서마다 따로 부르면 그만큼 API 왕복이 늘어 느리다).
        """
        if not docs:
            return 0
        first_ids = [wiki_chunk_id(player_id, d["page_id"], 0) for d in docs]
        indexed = set(self.wiki.get(ids=first_ids, include=[])["ids"])
        texts, metadatas, ids = [], [], []
        for doc, first in zip(docs, first_ids):
            if first in indexed:
                continue
            for i, piece in enumerate(self.splitter.split_text(doc["text"])):
                # 청크 앞에 문서 제목을 붙여 임베딩한다. 본문 중간 청크도 무엇에 대한 글인지 검색에 걸리게 하려는 것.
                texts.append(f"{doc['title']}\n{piece}")
                metadatas.append(self._wiki_metadata(doc, i))
                ids.append(wiki_chunk_id(player_id, doc["page_id"], i))
        for start in range(0, len(texts), batch_size):
            self.wiki.add_texts(texts[start : start + batch_size], metadatas=metadatas[start : start + batch_size],
                                ids=ids[start : start + batch_size])
        return len(texts)

    @staticmethod
    def _wiki_metadata(doc: dict, index: int) -> dict:
        return {
            "source_type": "wiki",
            "player_id": doc["player_id"],        # 검색 범위를 이 선수로 좁히는 필터. 빠지면 다른 선수 문서가 섞인다
            "page_id": doc["page_id"],
            "title": doc["title"],
            "url": doc["url"],
            "related_player": doc["related_player"],
            "search_query": doc["search_query"],
            "collected_at": doc["collected_at"],
            "source": doc["source"],
            "chunk_index": index,
        }

    def search_wiki(self, player_id: str, query: str, k: int) -> list[Document]:
        """이 선수로 모은 문서 청크만 질문과의 의미 유사도로 찾는다. 설정에 따라 MMR. (F2006 · F2014)"""
        where = {"player_id": player_id}
        if self.settings.search_type == "mmr":
            return self.wiki.max_marginal_relevance_search(
                query, k=k, fetch_k=max(k, self.settings.mmr_fetch_k),
                lambda_mult=self.settings.mmr_lambda, filter=where,
            )
        return self.wiki.similarity_search(query, k=k, filter=where)
