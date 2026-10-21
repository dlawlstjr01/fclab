"""실 연결 점검 — GMS 키 · 넥슨 키 · 위키백과로 실제 흐름을 한 번 돌린다. (키를 받은 뒤 실행)

    python check_live.py                 # 연결 확인 + 손흥민으로 질문 · 소개 한 번씩
    python check_live.py --player 메시    # 다른 선수로

순서: ① 키 ② 채팅 모델 ③ 임베딩 ④ 넥슨 API ⑤ 위키백과 ⑥ 메타데이터 색인 ⑦ 질문 → 수집 → 답변 ⑧ 같은 질문 다시(수집 캐시)
      ⑩ 문서에 없는 질문(F2007) ⑪ 조건 탐색에 조건과 맞지 않는 선수가 섞이지 않는지(F2011)
      ⑨ 전체 카드에서 미리 분석한 카드 밖의 카드 불러오기 (색인에는 넣지 않고 만들어지는지만 본다)
"""

from __future__ import annotations

import argparse
import sys
import time

import config
import llm
from data_prep.collector import Collector
from data_prep.nexon_api import NexonClient
from data_prep.player_builder import CardCatalog, load_card
from data_prep.players import load_players, player_label
from data_prep.wiki_api import WikiClient
from rag.citations import cited_evidence
from rag.graph import build_graph
from services.answering import run_task
from services.explore import explore_players
from vector_store import PlayerStore


def line(ok: bool, message: str) -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + message)


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="손흥민")
    parser.add_argument("--question", default="프로 데뷔는 언제, 어느 팀에서 했고 이후 어떤 클럽을 거쳤어?")
    parser.add_argument("--card", default="박지성", help="전체 카드에서 불러올 선수 이름")
    args = parser.parse_args()
    settings = config.load_settings()

    print("① 키")
    line(bool(config.gms_key()), "GMS_KEY")
    line(bool(config.nexon_key()), "NEXON_API_KEY")
    line(bool(config.wiki_contact()), "WIKI_CONTACT (없어도 동작하지만 분당 10회로 느리다)")
    if not config.gms_key():
        sys.exit("GMS_KEY 가 없어 여기서 멈춥니다.")

    print(f"② 채팅 모델 ({settings.chat_model})")
    model = llm.make_chat_model(settings)
    t0 = time.time()
    reply = llm.reply_text(model.invoke("한 단어로만 답해: 축구공은 무슨 모양?"))
    line(bool(reply.strip()), f"응답 '{reply.strip()[:40]}' ({time.time() - t0:.1f}s)")

    print(f"③ 임베딩 ({settings.embedding_model})")
    embeddings = llm.make_embeddings(settings)
    vector = embeddings.embed_query("손흥민")
    line(len(vector) > 0, f"벡터 차원 {len(vector)}")

    print("④ 넥슨 Open API")
    rows = NexonClient(config.nexon_key()).ranker_stats([(866200104, 25)])
    line(bool(rows), f"랭커 스탯 {len(rows)}건")

    print("⑤ 위키백과")
    wiki = WikiClient(config.wiki_contact(), interval=settings.wiki_interval)
    hits = wiki.search("손흥민", limit=1)
    line(bool(hits), f"검색 '{hits[0]['title'] if hits else '-'}'")

    print("⑥ 메타데이터 색인")
    store = PlayerStore(settings, embeddings)
    players = load_players(settings.items_path)
    line(store.meta_count() >= len(players), f"색인 {store.meta_count()}건 / 미리 분석 {len(players)}건 (+ 앱에서 연 카드)")
    if store.meta_count() == 0:
        sys.exit("색인이 비어 있습니다. python build_index.py 를 먼저 실행하세요.")

    target = next((p for p in players if p["name"] == args.player), None)
    if not target:
        sys.exit(f"'{args.player}' 선수가 players.json 에 없습니다.")
    print(f"⑦ 질문 → 수집 → 답변  [{player_label(target)}] {args.question}")
    collector = Collector(settings, model, wiki)
    graph = build_graph(store, model, settings)
    t0 = time.time()
    result = run_task("qa", [target], args.question, collector, store, graph, on_step=lambda m: print("     ·", m))
    used = cited_evidence(result["answer"], result["evidence"])
    print("\n" + result["answer"] + "\n")
    for e in used:
        print(f"     [근거 {e['number']}] {e['title']} · {e['source_label']} · {e['url']}")
    line(bool(result["answer"]), f"답변 생성 ({time.time() - t0:.1f}s)")
    line(bool(used), f"인용한 근거 {len(used)}건 / 검색된 근거 {len(result['evidence'])}건")

    print("⑧ 같은 질문 다시 — 수집이 다시 일어나지 않아야 한다 (NF2002)")
    t0 = time.time()
    again = run_task("qa", [target], args.question, collector, store, graph)
    report = again["reports"][target["player_id"]]
    line(report.cached, f"새 수집 {len(report.new_docs)}건 ({time.time() - t0:.1f}s)")

    print("⑩ 문서에 없는 질문 — 추측하지 않아야 한다 (F2007)")
    unknown = run_task("qa", [target], "이 선수가 가장 좋아하는 음식과 반려동물 이름은?", collector, store, graph)
    print("     " + unknown["answer"][:160].replace("\n", " "))
    line("확인되지 않" in unknown["answer"], "근거에 없는 내용은 '문서에서 확인되지 않습니다'로 답한다")

    print("⑪ 조건 기반 탐색 — 조건과 맞지 않는 선수가 섞이지 않아야 한다 (F2011)")
    by_id = {p["player_id"]: p for p in players}
    found = explore_players("대한민국 출신 선수", store, model, by_id, settings)["items"]
    wrong = [by_id[f["player_id"]]["name"] for f in found if "대한민국" not in (by_id[f["player_id"]].get("intro") or "")]
    print("     " + ", ".join(by_id[f["player_id"]]["name"] for f in found))
    line(bool(found) and not wrong, f"결과 {len(found)}명, 소개에 '대한민국'이 없는 선수 {wrong or '없음'}")

    print(f"⑨ 전체 카드에서 불러오기 — '{args.card}' (306장 밖의 카드)")
    catalog = CardCatalog(NexonClient(config.nexon_key()), settings.storage_dir / "meta")
    curated = {p["player_id"] for p in players}
    hits = [h for h in catalog.search(args.card) if str(h["spid"]) not in curated]
    line(bool(hits), f"전체 {len(catalog.cards):,}장 중 '{args.card}' 카드 {len(hits)}장")
    if hits:
        t0 = time.time()
        card = load_card(hits[0]["spid"], catalog, wiki, settings.matchtype)
        line(True, f"{player_label(card)} · {card['position_group'] or '랭커 기록 없음'} · "
                   f"위키 {card['wiki_title'] or '없음'} ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
