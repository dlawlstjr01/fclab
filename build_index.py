"""항목 메타데이터 색인 스크립트 (F2001 · F2002). 다시 실행해도 같은 선수는 덮어쓰기만 된다.

    python build_index.py                 # data/players.json 전체를 player_meta 컬렉션에 색인
    python build_index.py --show 손흥민    # 선수 1명의 색인 텍스트 출력 (검증 4.1 — 키 필요 없음)

먼저 build_items.py 로 data/players.json 을 만들어 두어야 한다.
"""

from __future__ import annotations

import argparse
import sys

import config
import llm
from data_prep.db import FcDb
from data_prep.players import index_text, load_players
from vector_store import PlayerStore


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", help="색인 텍스트를 볼 선수 이름 또는 선수 ID")
    args = parser.parse_args()

    settings = config.load_settings()
    if not settings.items_path.exists():
        sys.exit(f"{settings.items_path} 가 없습니다. 먼저 python build_items.py 를 실행하세요.")
    players = load_players(settings.items_path)
    print(f"항목 메타데이터 로드: {len(players)}건 ({settings.items_path})")

    if args.show:
        matches = [p for p in players if args.show in (p["name"], p["player_id"])]
        for p in matches or []:
            print("-" * 60)
            print(index_text(p))
        if not matches:
            print(f"'{args.show}' 선수를 찾지 못했습니다.")
        return

    try:
        store = PlayerStore(settings, llm.make_embeddings(settings))
    except llm.MissingKeyError as exc:
        sys.exit(str(exc))
    before = store.meta_count()
    after = store.index_players(players, on_batch=lambda done, total: print(f"  색인 {done}/{total}"))
    # 앱에서 열어 분석한 카드(SQLite storage/fclab.db)도 같은 컬렉션에 있을 수 있다
    extra_ids = {p["player_id"] for p in FcDb(settings.storage_dir / "fclab.db").all_players()}
    extra_ids = {pid for pid in extra_ids if store.get_meta(pid)}
    expected = len({p["player_id"] for p in players} | extra_ids)
    print(f"색인 전 {before}건 → 색인 후 {after}건 (컬렉션 {settings.meta_collection}, 앱에서 더한 카드 {len(extra_ids)}장 포함)")
    if after == expected:
        print("항목 수와 색인된 청크 수가 같습니다. 다시 실행해도 늘지 않습니다.")
    else:
        print(f"주의: 항목 {expected}건과 색인 {after}건이 다릅니다.")


if __name__ == "__main__":
    main()
