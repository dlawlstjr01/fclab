"""질문 하나를 처리하는 흐름 — 수집(프롬프트 1 · 2) → 색인 → LangGraph(retrieve → generate).

화면(app.py)과 실 연결 점검(check_live.py)이 같은 함수를 쓴다.

    qa       선수 역사 Q&A    — 사용자 질문으로 수집 · 검색
    intro    선수 소개       — 정해 둔 주제(출생 · 경력 · 수상 …)로 수집 · 검색
    compare  두 선수 비교     — 선수마다 따로 수집하고, 검색도 선수마다 나눠 한다
"""

from __future__ import annotations

from typing import Callable

from data_prep.prompts import TASK_REQUESTS, TASK_TOPICS


def run_task(task: str, players: list[dict], user_text: str, collector, store, graph,
             on_step: Callable[[str], None] | None = None) -> dict:
    step = on_step or (lambda _message: None)
    topics = user_text if task == "qa" else TASK_TOPICS[task]
    question = user_text if task == "qa" else TASK_REQUESTS[task]

    reports = {}
    for player in players:
        if len(players) > 1:
            step(f"**{player['name']}**")
        collect_query = topics if task == "qa" else f"{player['name']} 선수의 {topics}"
        reports[player["player_id"]] = collector.collect(player, collect_query, on_step=step)
        added = store.index_wiki_docs(player["player_id"], collector.stored_docs(player["player_id"]))
        if added:
            step(f"새 청크 {added}개를 색인했습니다.")

    step("근거를 검색하고 답변을 만드는 중... (retrieve → generate)")
    result = graph.invoke({"players": players, "question": question, "task": task, "search_query": topics})
    diag = result.get("diagnostics") or {}
    if diag:
        step(f"근거 구성: 선수 메타데이터 {diag['meta']} · 위키 청크 {diag['wiki']} → 중복 제거 "
             f"{diag['removed_duplicates']} → 근거 {diag['evidence']}건 ({diag['search_type']}, 선수당 Top-{diag['top_k_per_player']})")
    return {"answer": result.get("answer", ""), "evidence": result.get("evidence", []), "reports": reports,
            "diagnostics": diag}
