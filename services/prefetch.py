"""백그라운드 미리 불러오기 — 화면에 보이는 카드를 사용자가 누르기 전에 분석해 SQLite 에 넣어 둔다. (NF2001)

    검색 결과 · 다른 시즌 카드가 화면에 보이는 순간  → schedule(spids)
    사용자가 카드를 누르면                          → get(spid): 끝났으면 즉시, 진행 중이면 그 결과를 기다림
    앱이 처음 뜰 때                                 → warm_ranker(미리 분석한 432장): 포지션 사용 차트용 기록

스레드는 네트워크(넥슨 · 위키) + SQLite 만 다룬다. 화면(st.*)과 Chroma 색인은 건드리지 않는다
(색인은 카드를 연 뒤 화면 스레드에서 한다).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date

from data_prep.player_builder import attach_wikipedia

log = logging.getLogger("fclab.prefetch")


class Prefetcher:
    def __init__(self, catalog, wiki, db, matchtype: int, wiki_hints: dict[int, dict], workers: int = 3):
        self.catalog, self.wiki, self.db, self.matchtype = catalog, wiki, db, matchtype
        self.wiki_hints = wiki_hints          # 미리 분석한 카드의 pid → 위키 문서 (같은 선수의 다른 카드에 다시 쓴다)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fclab-prefetch")
        self.pending: dict[int, Future] = {}
        self.lock = threading.Lock()
        self.warming = False

    # ── 카드 1장 분석 ──────────────────────────────────────────

    def rows(self, spid: int) -> list[dict]:
        """랭커 포지션별 기록 — DB 에 있으면 0회, 없으면 넥슨 1회 후 저장."""
        rows = self.db.ranker_rows(spid)
        if rows is None:
            by_card = self.catalog.ranker_rows([spid], self.matchtype)
            self.db.put_ranker_rows(by_card, [spid])
            rows = by_card.get(spid, [])
        return rows

    def _work(self, spid: int) -> dict:
        cached = self.db.get_player(str(spid))
        if cached:
            return cached
        rows = self.rows(spid)
        newest = max((r["date"] for r in rows), default=date.today())
        player = self.catalog.make_player(spid, rows, newest, check_image=False)
        hint = self.wiki_hints.get(player["pid"]) or self.db.player_with_wiki(player["pid"])
        if hint:
            player.update({k: hint[k] for k in ("wiki_title", "wiki_url", "intro")})
        else:
            attach_wikipedia([player], self.wiki)
        self.db.put_player(player)
        return player

    # ── 예약 · 받기 ────────────────────────────────────────────

    def quota_ok(self, reserve: int = 200) -> bool:
        """오늘 남은 넥슨 호출이 넉넉한가 — 모자라면 미리 불러오기는 쉬고, 남은 호출은 사용자가 누르는 동작에 쓴다."""
        remaining = self.catalog.nexon.remaining
        return remaining is None or remaining > reserve

    def schedule(self, spids: list[int], limit: int = 6) -> None:
        """보이는 카드 중 앞의 limit 장만 미리 분석한다 (카드 1장 = 넥슨 1회 · 하루 한도 1,000회)."""
        if not self.quota_ok():
            return
        with self.lock:
            for spid in spids[:limit]:
                if spid in self.pending or self.db.get_player(str(spid)):
                    continue
                self.pending[spid] = self.pool.submit(self._work, spid)

    def get(self, spid: int, timeout: float = 30) -> dict:
        with self.lock:
            future = self.pending.get(spid)
        if future is None:
            return self._work(spid)
        try:
            return future.result(timeout=timeout)
        except Exception:
            with self.lock:   # 실패한 예약은 지워 다음에 다시 시도할 수 있게
                self.pending.pop(spid, None)
            raise

    def warm_ranker(self, spids: list[int]) -> None:
        """미리 분석한 카드들의 포지션별 기록을 한 번에 받아 둔다 (백그라운드, 이미 있으면 건너뜀)."""
        with self.lock:
            if self.warming:
                return
            self.warming = True

        def run() -> None:
            todo = [s for s in spids if s not in self.db.fresh_spids(spids)]
            failures = 0
            start = 0
            while start < len(todo):
                batch = todo[start : start + 30]
                try:
                    self.db.put_ranker_rows(self.catalog.ranker_rows(batch, self.matchtype), batch)
                    start += 30
                    failures = 0
                    time.sleep(1)   # 화면에서 바로 보내는 요청이 한도에 걸리지 않게 미리 받기는 천천히
                except Exception:  # noqa: BLE001 — 미리 받기는 실패해도 화면이 그 자리에서 받는다
                    failures += 1
                    if failures >= 3:
                        log.warning("랭커 기록 미리 받기를 멈춤 (요청 제한이 계속됨) — 화면이 필요할 때 받는다")
                        return
                    log.warning("랭커 기록 미리 받기: 요청 제한 — 30초 쉬고 이어서")
                    time.sleep(30)

        self.pool.submit(run)
