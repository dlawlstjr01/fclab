"""항목 메타데이터 만들기 — 넥슨 Open API + 위키백과 첫 문단 → data/players.json  (F2001 · NF2007)

    python build_items.py              # 전체 (넥슨 + 위키백과)
    python build_items.py --wiki-only  # 위키백과 단계만 다시 (매칭 규칙을 고쳤을 때)

1. 넥슨 메타데이터에서 settings.toml 의 시즌(26 TOTS · ICON) 카드를 고른다.
2. 카드마다 28개 포지션의 랭커 스탯을 받아 '랭커가 주로 쓰는 포지션'과 그 포지션의 경기당 평균을 남긴다.
3. 선수 이름으로 한국어 위키백과 문서를 찾아 첫 문단(국적 · 포지션 · 소속 소개)과 원문 URL 을 붙인다.
   이 문서는 나중에 '선수 자체 문서'로 항상 색인된다(F2004). 동명이인은 규칙으로 거른다(player_builder.plausible).

LLM 은 쓰지 않는다. 넥슨 키(NEXON_API_KEY)만 있으면 된다. WIKI_CONTACT 가 없으면 위키백과 호출이 느리다(분당 10회).
나머지 카드(88,359장 중 이 306장 밖)는 앱의 '전체 카드에서 찾기'에서 고를 때 같은 방법으로 그 자리에서 만든다.
"""

from __future__ import annotations

import json
import sys
from datetime import date

import config
from data_prep.nexon_api import NexonClient, split_spid
from data_prep.player_builder import CardCatalog, attach_wikipedia
from data_prep.wiki_api import WikiClient


def _print_dropped(dropped: list[tuple[str, str, str]]) -> None:
    print(f"  버린 위키 후보 {len(dropped)}건 (동명이인 · 동음이의 등)")
    for name, title, reason in dropped:
        print(f"    {name} ✗ {title} — {reason}")


def wiki_only(settings) -> None:
    """이미 만든 players.json 에 위키백과 단계만 다시 한다 (넥슨 호출 없음)."""
    players = json.loads(settings.items_path.read_text(encoding="utf-8"))
    wiki = WikiClient(config.wiki_contact(), interval=settings.wiki_interval)
    dropped = attach_wikipedia(players, wiki, on_progress=lambda m: print("  " + m))
    settings.items_path.write_text(json.dumps(players, ensure_ascii=False, indent=1), encoding="utf-8")
    report_path = settings.data_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["with_wikipedia"] = sum(1 for p in players if p["wiki_title"])
    report["wikipedia_rejected"] = [{"card": n, "title": t, "reason": r} for n, t, r in dropped]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n위키백과 연결 {report['with_wikipedia']}/{len(players)}")
    _print_dropped(dropped)


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    settings = config.load_settings()
    if "--wiki-only" in sys.argv:
        wiki_only(settings)
        return
    nexon = NexonClient(config.nexon_key())
    if not nexon.api_key:
        sys.exit("NEXON_API_KEY 가 없습니다. .env 에 넣고 다시 실행하세요.")

    print("① 넥슨 메타데이터")
    catalog = CardCatalog(nexon, settings.storage_dir / "meta")
    chosen = [c["id"] for c in catalog.cards if split_spid(c["id"])[0] in settings.seasons]
    print(f"  카드 {len(catalog.cards):,}장 중 시즌 {list(settings.seasons)} → {len(chosen)}장")

    print("② 랭커 스탯 (카드 × 28포지션)")
    by_card = catalog.ranker_rows(chosen, settings.matchtype)
    newest = max((r["date"] for rows in by_card.values() for r in rows), default=date.today())
    print(f"  기록이 있는 카드 {len(by_card)}장 (최신 집계 {newest})")
    if nexon.skipped:
        print(f"  랭커 스탯을 지원하지 않는 카드 {len(nexon.skipped)}장: {nexon.skipped}")

    print("③ 카드 정보 · 이미지")
    players = []
    for i, spid in enumerate(chosen, start=1):
        players.append(catalog.make_player(spid, by_card.get(spid, []), newest))
        if i % 50 == 0 or i == len(chosen):
            print(f"  {i}/{len(chosen)}")

    print("④ 한국어 위키백과 첫 문단")
    wiki = WikiClient(config.wiki_contact(), interval=settings.wiki_interval)
    if not wiki.has_contact:
        print("  [안내] WIKI_CONTACT 가 없어 분당 10회로 천천히 호출합니다. .env 에 영문 이메일을 넣으면 빨라집니다.")
    dropped = attach_wikipedia(players, wiki, on_progress=lambda m: print("  " + m))

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.items_path.write_text(json.dumps(players, ensure_ascii=False, indent=1), encoding="utf-8")
    report = {
        "built_at": date.today().isoformat(),
        "seasons": {sid: catalog.seasons.get(sid, {}).get("className") for sid in settings.seasons},
        "players": len(players),
        "with_ranker_stats": sum(1 for p in players if p["ranker"]),
        "with_wikipedia": sum(1 for p in players if p["wiki_title"]),
        "with_image": sum(1 for p in players if p["image"]),
        "ranker_stats_date": newest.isoformat(),
        "ranker_stats_skipped": nexon.skipped,
        "wikipedia_rejected": [{"card": n, "title": t, "reason": r} for n, t, r in dropped],
        "sources": {
            "넥슨 Open API (FC 온라인)": "선수 카드 · 시즌 · 포지션 메타데이터, 랭커 스탯, 선수 이미지 — Data based on NEXON Open API",
            "한국어 위키백과": "선수 소개 첫 문단 · 원문 URL — CC BY-SA 4.0",
        },
    }
    (settings.data_dir / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n완료 → {settings.items_path}")
    print(json.dumps({k: v for k, v in report.items() if k not in ("sources", "wikipedia_rejected")}, ensure_ascii=False, indent=1))
    _print_dropped(dropped)


if __name__ == "__main__":
    main()
