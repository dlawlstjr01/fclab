"""검색 설정 실험 — 같은 선수 · 같은 질문으로 설정만 바꿔 근거 · 답변이 어떻게 달라지는지 기록한다.

    python experiments.py                     # 손흥민(26 TOTS), 질문 2개
    python experiments.py --player "사뮈엘 에토"

비교 (명세서 4.2 · 4.3 · 4.5 · 2.5)
  A. similarity vs MMR       (F2014 — 근거 다양성)
  B. Top-k 6 vs 3            (F2006)
  C. 청크 1000/200 vs 500/100 (F2005 — 저장해 둔 같은 문서를 다른 컬렉션에 다시 색인)

결과는 docs/05_검색설정_실험결과.md 에 쓴다. 실제 GMS · 저장 문서로 돌린 값만 적는다.
먼저 그 선수에게 질문을 한 번 해서(check_live.py 또는 앱) 위키 문서가 수집되어 있어야 한다.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from datetime import datetime

import config
import llm
from data_prep.collector import load_stored_docs
from data_prep.players import load_players, player_label
from rag.citations import cited_evidence
from rag.graph import build_graph
from vector_store import PlayerStore

QUESTIONS = ["프로 데뷔와 이후 이적한 클럽들을 알려 줘", "국가대표로서 가장 큰 기록이나 수상은?"]


def run(store, model, settings, player, question) -> dict:
    graph = build_graph(store, model, settings)
    t0 = time.time()
    result = graph.invoke({"players": [player], "question": question, "task": "qa", "search_query": question})
    wiki = [e for e in result["evidence"] if e["source_type"] == "wiki"]
    return {
        "seconds": time.time() - t0,
        "titles": [e["title"] for e in wiki],
        "unique_docs": len({e["title"] for e in wiki}),
        "chunks": len(wiki),
        "answer": result.get("answer", ""),
        "cited": [e["number"] for e in cited_evidence(result.get("answer", ""), result["evidence"])],
    }


def fmt_titles(titles: list[str]) -> str:
    counts: dict[str, int] = {}
    for t in titles:
        counts[t] = counts.get(t, 0) + 1
    return ", ".join(f"{t}×{n}" if n > 1 else t for t, n in counts.items()) or "—"


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="손흥민")
    args = parser.parse_args()

    base = config.load_settings()
    model = llm.make_chat_model(base)
    embeddings = llm.make_embeddings(base)
    players = load_players(base.items_path)
    player = next(p for p in players if p["name"] == args.player)
    docs = load_stored_docs(base.docs_dir, player["player_id"])
    if len(docs) < 2:
        sys.exit(f"{player_label(player)} 의 저장 문서가 {len(docs)}건입니다. 먼저 앱이나 check_live.py 로 질문해 문서를 모으세요.")
    print(f"선수 {player_label(player)} · 저장 문서 {len(docs)}건: {', '.join(d['title'] for d in docs)}")

    out = [f"# 검색 설정 실험 결과 — {player_label(player)}",
           "", f"실행: {datetime.now():%Y-%m-%d %H:%M} · `python experiments.py --player \"{args.player}\"` · 모델 {base.chat_model} · 임베딩 {base.embedding_model}",
           f"저장 문서 {len(docs)}건: {', '.join(d['title'] for d in docs)}", ""]

    # A · B: 같은 컬렉션(1000/200)에서 검색 방식 · Top-k 만 바꾼다
    configs = [
        ("similarity · Top-k 6 (기본)", replace(base, search_type="similarity", top_k=6)),
        ("MMR · Top-k 6 (fetch_k 20, λ 0.5)", replace(base, search_type="mmr", top_k=6)),
        ("similarity · Top-k 3", replace(base, search_type="similarity", top_k=3)),
    ]
    store = PlayerStore(base, embeddings)
    store.index_wiki_docs(player["player_id"], docs)
    out += ["## A · B. 검색 방식(similarity · MMR) · Top-k", "",
            "| 질문 | 설정 | 위키 청크 | 서로 다른 문서 | 근거로 온 문서 (×같은 문서 청크 수) | 답변이 인용한 근거 | 답변 길이 | 시간 |",
            "|---|---|---|---|---|---|---|---|"]
    answers = []
    for question in QUESTIONS:
        for label, settings in configs:
            store.settings = settings
            r = run(store, model, settings, player, question)
            print(f"[{label}] {question} → 문서 {r['unique_docs']}종 / 청크 {r['chunks']} · 인용 {r['cited']}")
            out.append(f"| {question} | {label} | {r['chunks']} | {r['unique_docs']} | {fmt_titles(r['titles'])} | "
                       f"{r['cited'] or '없음'} | {len(r['answer'])}자 | {r['seconds']:.1f}s |")
            answers.append((question, label, r["answer"]))
    store.settings = base

    # C: 청크 크기 — 같은 저장 문서를 500/100 컬렉션에 다시 색인
    small_settings = replace(base, chunk_size=500, chunk_overlap=100)
    small = PlayerStore(small_settings, embeddings)
    small.index_wiki_docs(player["player_id"], docs)

    def count(s) -> int:
        return len(s.wiki.get(where={"player_id": player["player_id"]}, include=[])["ids"])

    out += ["", "## C. 청크 크기 · 중복 길이", "",
            f"같은 문서 {len(docs)}건 → 1000/200: **{count(store)}청크**, 500/100: **{count(small)}청크** "
            f"(컬렉션 `{base.wiki_collection}` · `{small_settings.wiki_collection}`)", "",
            "| 질문 | 청크 설정 | 위키 청크 | 서로 다른 문서 | 근거로 온 문서 | 인용 | 답변 길이 |", "|---|---|---|---|---|---|---|"]
    for question in QUESTIONS:
        for label, s, st_ in (("1000/200", base, store), ("500/100", small_settings, small)):
            r = run(st_, model, s, player, question)
            print(f"[청크 {label}] {question} → 문서 {r['unique_docs']}종 / 청크 {r['chunks']} · 인용 {r['cited']}")
            out.append(f"| {question} | {label} | {r['chunks']} | {r['unique_docs']} | {fmt_titles(r['titles'])} | "
                       f"{r['cited'] or '없음'} | {len(r['answer'])}자 |")
            answers.append((question, f"청크 {label}", r["answer"]))

    out += ["", "## 실제 답변 (앞 300자)", ""]
    for question, label, answer in answers:
        out.append(f"- **{question} · {label}** — {answer[:300].replace(chr(10), ' ')}")
    path = base.storage_dir.parent / "docs" / "05_검색설정_실험결과.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n→ {path}")


if __name__ == "__main__":
    main()
