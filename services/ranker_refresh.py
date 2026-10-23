"""랭커 기록 다시 받기 — 분석해 둔 카드의 대표 포지션 랭커 기록을 정해진 시간마다 (기본 4시간 — 하루 6번).

넥슨 랭커 스탯(ranker-stats)은 넥슨이 날마다 새로 집계한다 (createDate). 미리 분석한 432장은 build_items.py 를 다시
돌리기 전까지 그때 받은 기록에 멈춰 있어, 홈의 '랭커 집계일'과 랭커 경기당 기록이 바뀌지 않았다.

- 카드마다 대표 포지션 한 칸만 묻는다 — 카드 40장이 요청 1번 (583장이면 15번, 하루 6번이면 약 90번 · 테스트 키 하루 1,000회)
- 오늘 남은 호출이 reserve 아래면 이번 차례는 쉰다 (사용자가 누르는 동작에 남겨 둔다). 키가 없으면 돌지 않는다
- 받은 기록은 SQLite(ranker_latest)에 두고, 화면(views/common.boot)은 카드의 ranker 를 더 새 것으로 바꿔 쓴다
  (data/players.json 은 그대로). 차례마다 ranker_refresh_log 에 언제 · 몇 장 · 가장 새 집계일을 남긴다
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

log = logging.getLogger("fclab.ranker_refresh")


class RankerRefresher:
    def __init__(self, catalog, db, matchtype: int, hours: float = 4, reserve: int = 300, first_delay: float = 60,
                 check_every: float = 600):
        self.catalog, self.db, self.matchtype = catalog, db, matchtype
        self.hours, self.reserve = hours, reserve
        self.first_delay, self.check_every = first_delay, check_every
        self.enabled = hours > 0 and bool(getattr(catalog.nexon, "api_key", None))
        self.running = False
        self._thread: threading.Thread | None = None

    def due(self, now: datetime | None = None) -> bool:
        """마지막으로 받은 지 hours 시간이 지났나 (한 번도 안 받았으면 True)."""
        last = self.db.last_ranker_refresh()
        if last is None:
            return True
        return (now or datetime.now()) - datetime.fromisoformat(last["at"]) >= timedelta(hours=self.hours)

    def run_once(self, players: list[dict]) -> dict:
        """대표 포지션 랭커 기록을 한 번 다시 받는다 — {cards, updated, newest, calls} 또는 {skipped: 이유}."""
        remaining = self.catalog.nexon.remaining
        if remaining is not None and remaining < self.reserve:
            log.info("랭커 기록 다시 받기 쉼 — 오늘 남은 넥슨 호출 %d회 (기준 %d회)", remaining, self.reserve)
            return {"skipped": f"남은 호출 {remaining}회"}
        pairs = list({p["spid"]: (p["spid"], p["ranker"]["position"]) for p in players
                      if (p.get("ranker") or {}).get("position")}.values())
        if not pairs:
            return {"skipped": "분석한 카드 없음"}
        self.running = True
        try:
            rows = self.catalog.ranker_at(pairs, self.matchtype)
        finally:
            self.running = False
        updated = self.db.put_ranker_latest(rows)
        newest = max((r["date"] for r in rows.values()), default=None)
        calls = -(-len(pairs) // 40)
        self.db.log_ranker_refresh(len(pairs), updated, newest, calls)
        log.info("랭커 기록 다시 받음 — %d장 중 %d장 · 가장 새 집계일 %s · 요청 약 %d번", len(pairs), updated, newest, calls)
        return {"cards": len(pairs), "updated": updated, "newest": newest, "calls": calls}

    def start(self, players_fn) -> None:
        """뒤에서 돈다 — 켜진 뒤 first_delay 초에 한 번 보고, 그다음 check_every 초마다 차례가 됐는지 본다."""
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return

        def loop() -> None:
            time.sleep(self.first_delay)   # 앱이 뜨는 동안의 요청과 겹치지 않게 잠깐 기다린다
            while True:
                try:
                    if self.due():
                        self.run_once(players_fn())
                except Exception as exc:  # noqa: BLE001 — 한 차례 실패해도 다음 차례에 다시
                    log.warning("랭커 기록 다시 받기 실패: %s", exc)
                time.sleep(self.check_every)

        self._thread = threading.Thread(target=loop, name="ranker-refresh", daemon=True)
        self._thread.start()


def with_latest(players: list[dict], latest: dict[int, dict]) -> list[dict]:
    """카드의 ranker 를 다시 받은 기록으로 — 집계일이 같거나 더 새 것일 때만 바꾼다 (원래 목록은 고치지 않고 새 dict 로)."""
    out = []
    for p in players:
        new, old = latest.get(p["spid"]), p.get("ranker")
        if new and (not old or new.get("date", "") >= old.get("date", "")):
            p = {**p, "ranker": new}
        out.append(p)
    return out
