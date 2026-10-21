"""선수 자체 문서 미리 수집 · 색인 — 첫 질문의 기다림을 줄인다. (F2004 · F2005 · NF2001)

    python prefetch_wiki.py

players.json 의 선수마다 위키백과 '선수 자체 문서'(wiki_title)를 받아 저장하고 청킹 · 색인해 둔다.
선수 자체 문서는 어차피 항상 색인해야 하는 문서라(F2004), 질문 때 받을 이유가 없다.
질문 때는 프롬프트 1 · 2 로 고른 '추가' 문서만 받는다. 이미 받은 문서는 건너뛴다(NF2002).
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import config
import llm
from data_prep.collector import load_stored_docs, save_doc
from data_prep.players import load_players
from data_prep.wiki_api import WikiClient
from vector_store import PlayerStore


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    settings = config.load_settings()
    players = [p for p in load_players(settings.items_path) if p.get("wiki_title")]
    todo = [p for p in players if not any(d.get("is_self") for d in load_stored_docs(settings.docs_dir, p["player_id"]))]
    print(f"위키 문서가 있는 선수 {len(players)}명 중 아직 안 받은 {len(todo)}명")

    wiki = WikiClient(config.wiki_contact(), interval=settings.wiki_interval)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=settings.parallel) as pool:
        pages = list(pool.map(lambda p: wiki.fetch(p["wiki_title"]), todo))
    for player, page in zip(todo, pages):
        if page:
            save_doc(settings.docs_dir, player, page, player["wiki_title"], True, wiki.name)
    print(f"  수집 {sum(1 for p in pages if p)}건 ({time.time() - t0:.0f}s)")

    store = PlayerStore(settings, llm.make_embeddings(settings))
    t0 = time.time()
    added = 0
    for i, player in enumerate(players, start=1):
        added += store.index_wiki_docs(player["player_id"], load_stored_docs(settings.docs_dir, player["player_id"]))
        if i % 50 == 0 or i == len(players):
            print(f"  색인 {i}/{len(players)} — 새 청크 {added}개")
    print(f"완료: {settings.wiki_collection} 청크 {store.wiki_count()}개 ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
