"""RAG 파이프라인 — LangGraph 로 검색(retrieve)과 생성(generate)을 노드로 나눈다. (F2006 · F2007)

    START → retrieve → generate → END

retrieve  선택한 선수(비교면 두 명)의 메타데이터 청크(검색 없이 항상) + 선수별로 범위를 좁힌 위키 청크를
          합치고, 중복을 지우고, [근거 1]부터 번호를 붙인다.
generate  프롬프트 3 으로 근거만 써서 답한다.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from data_prep.prompts import SOURCE_LABELS, answer_messages
from llm import reply_text

log = logging.getLogger("fclab.rag")


class RagState(TypedDict, total=False):
    players: list[dict]    # 1명(Q&A · 소개) 또는 2명(비교)
    question: str          # 프롬프트 3 에 넣을 사용자 요청
    task: str              # "qa" | "intro" | "compare"
    search_query: str      # 근거 검색에 쓸 문장 (없으면 question)
    evidence: list[dict]
    diagnostics: dict      # 근거 구성 진단: 메타 n · 위키 n · 중복 제거 n (F2006)
    answer: str


def merge_evidence(meta_docs: list[Document], wiki_docs: list[Document]) -> list[dict]:
    """두 컬렉션의 결과를 합치고 중복 청크를 지운 뒤 번호를 붙인다. 메타데이터가 항상 앞 번호. (F2006)

    중복 판정: 청크 ID 가 같거나, 본문이 글자 그대로 같으면 같은 청크로 본다.
    (비교할 두 선수가 같은 문서 — 예) 같은 클럽 — 를 모으면 내용이 같은 청크가 두 번 나온다)
    """
    evidence, seen = [], set()
    for doc in list(meta_docs) + list(wiki_docs):
        keys = {"text:" + hashlib.sha1(doc.page_content.encode("utf-8")).hexdigest()}
        if doc.id:
            keys.add("id:" + doc.id)
        if keys & seen:
            continue
        seen |= keys
        source_type = doc.metadata.get("source_type", "wiki")
        evidence.append({
            "number": len(evidence) + 1,
            "source_type": source_type,
            "source_label": SOURCE_LABELS.get(source_type, source_type),
            "title": doc.metadata.get("title", ""),
            "url": doc.metadata.get("url", ""),
            "player_id": doc.metadata.get("player_id", ""),
            "text": doc.page_content,
        })
    return evidence


def build_graph(store, model, settings):
    def retrieve(state: RagState) -> dict:
        players = state["players"]
        query = state.get("search_query") or state["question"]
        k = math.ceil(settings.top_k / len(players))   # 비교면 선수마다 나눠 가져와 한쪽으로 쏠리지 않게 한다
        meta_docs = [doc for p in players if (doc := store.get_meta(p["player_id"]))]
        wiki_docs = [doc for p in players for doc in store.search_wiki(p["player_id"], query, k=k)]
        evidence = merge_evidence(meta_docs, wiki_docs)
        diagnostics = {
            "meta": len(meta_docs), "wiki": len(wiki_docs), "evidence": len(evidence),
            "removed_duplicates": len(meta_docs) + len(wiki_docs) - len(evidence),
            "top_k_per_player": k, "search_type": settings.search_type,
            "wiki_titles": sorted({d.metadata.get("title", "") for d in wiki_docs}),
        }
        log.info("retrieve %s", diagnostics)
        return {"evidence": evidence, "diagnostics": diagnostics}

    def generate(state: RagState) -> dict:
        messages = answer_messages(state["players"], state["question"], state["evidence"], state.get("task", "qa"))
        return {"answer": reply_text(model.invoke(messages)).strip()}

    graph = StateGraph(RagState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
