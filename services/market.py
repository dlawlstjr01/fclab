"""강화 단계별 시세를 백그라운드에서 받는다 — 화면은 SQLite 에 있는 것을 바로 그리고, 빈 칸은 다 받는 대로 채운다.

- 요청은 스레드 하나가 차례로 (데이터센터 요청 간격을 지키려고 동시에 보내지 않는다).
- 우선순위: 지금 화면에서 보는 카드(0) → 앱을 다시 켰을 때 밀린 카드 따라잡기(1).
- 다른 카드를 열면 앞 카드의 '아직 안 보낸' 요청은 버린다 — 보고 있는 카드가 먼저.
- 앱이 꺼져 있던 동안의 시세: 켤 때 catch_up() 이 30일 안에 본 카드 중 오래된 것을 다시 받고,
  응답에 든 최근 약 4개월치 일별 시세로 빈 날짜를 채운다 (FcDb.put_price).
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
from datetime import datetime, timedelta

from data_prep.market import MAX_GRADE, MarketBlocked

log = logging.getLogger("fclab.market")

VIEW, CATCH_UP = 0, 1


class MarketService:
    def __init__(self, client, db, ttl_hours: float = 6, enabled: bool = True):
        self.client, self.db = client, db
        self.ttl = ttl_hours
        self.enabled = enabled and client is not None
        self.blocked = False
        self.errors: dict[int, str] = {}
        self.filled: dict[tuple[int, int], int] = {}   # (카드, 강화) → 앱이 꺼져 있던 동안 새로 채운 날짜 수
        self._queue: list = []
        self._queued: set[tuple[int, int]] = set()
        self._running: tuple[int, int] | None = None
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None

    # ── 화면이 부르는 것 ─────────────────────────────────────────

    def table(self, spid: int) -> dict[int, dict]:
        return self.db.prices(spid, self.ttl)

    def want(self, spids: list[int], grades=range(1, MAX_GRADE + 1), force: bool = False) -> None:
        """이 카드들의 시세를 받아 둔다 (오래됐거나 없는 단계만). 다른 카드의 보기 요청은 뒤로 미루지 않고 버린다.

        force = '지금 다시 받기' — 6시간이 안 됐어도 다시 받되, 10분 안에 받은 단계는 건너뛴다 (연타 방지).
        """
        if not self.enabled or self.blocked:
            return
        have = {spid: self.table(spid) for spid in spids}
        if force:
            recent = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
            for rows in have.values():
                for row in rows.values():
                    row["fresh"] = row["fetched_at"] >= recent
        with self._cv:
            # 다른 카드의 보기 요청은 버리고, 이 카드의 따라잡기 요청은 보기 우선순위로 다시 넣는다
            self._queue = [item for item in self._queue
                           if (item[0] == VIEW and item[2] in spids) or (item[0] == CATCH_UP and item[2] not in spids)]
            heapq.heapify(self._queue)
            self._queued = {(s, g) for _, _, s, g in self._queue}
            for grade in grades:          # 단계 → 카드 순서로 섞어 두 카드 비교가 같이 채워지게
                for spid in spids:
                    row = have[spid].get(grade)
                    if (row and row["fresh"] and not force) or (spid, grade) in self._queued or (spid, grade) == self._running:
                        continue
                    heapq.heappush(self._queue, (VIEW, next(self._seq), spid, grade))
                    self._queued.add((spid, grade))
            self._start()
            self._cv.notify()

    def pending(self, spid: int) -> int:
        with self._cv:
            return sum(1 for s, _ in self._queued if s == spid) + (1 if self._running and self._running[0] == spid else 0)

    def catch_up(self, limit: int = 60) -> int:
        """앱을 켤 때 — 최근 30일 안에 본 카드 중 오래된 (카드, 강화)를 뒤쪽 우선순위로 다시 받는다."""
        if not self.enabled:
            return 0
        stale = self.db.stale_prices(self.ttl, limit)
        with self._cv:
            for spid, grade in stale:
                if (spid, grade) not in self._queued:
                    heapq.heappush(self._queue, (CATCH_UP, next(self._seq), spid, grade))
                    self._queued.add((spid, grade))
            if stale:
                self._start()
                self._cv.notify()
        return len(stale)

    # ── 백그라운드 ───────────────────────────────────────────────

    def _start(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, name="market", daemon=True)
            self._worker.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    if not self._cv.wait(timeout=60):
                        self._worker = None
                        return
                _, _, spid, grade = heapq.heappop(self._queue)
                self._queued.discard((spid, grade))
                self._running = (spid, grade)
            try:
                data = self.client.price(spid, grade)
                added = self.db.put_price(spid, grade, data)
                if added > 1:
                    self.filled[(spid, grade)] = added
                self.errors.pop(spid, None)
            except MarketBlocked as exc:
                self.blocked = True
                self.errors[spid] = str(exc)
                with self._cv:
                    self._queue.clear()
                    self._queued.clear()
            except Exception as exc:  # noqa: BLE001 — 한 칸 실패가 나머지를 막지 않게
                log.warning("시세 받기 실패 %s +%s: %s", spid, grade, exc)
                self.errors[spid] = "시세를 받지 못한 단계가 있습니다. 잠시 뒤 다시 시도하세요."
            finally:
                with self._cv:
                    self._running = None
