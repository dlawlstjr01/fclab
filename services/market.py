"""강화 단계별 시세를 백그라운드에서 받는다 — 화면은 SQLite 에 있는 것을 바로 그리고, 빈 칸은 받는 대로 채운다.

- 현재가: 데이터센터 선수 검색 1번이면 그 선수(이름)의 카드 전부 · 강화 1~13 현재가가 온다 (카드마다 13번 묻지 않는다).
  바뀐 시세만 시각과 함께 기록에 남는다 (FcDb.put_listing → market_snapshots).
- 일별 시세(그래프 · 사기/팔기 판단): 카드 · 강화 단계마다 1번씩 — 현재가가 먼저 보인 뒤 뒤이어 채운다.
- 요청은 스레드 하나가 차례로 (데이터센터 요청 간격을 지키려고 동시에 보내지 않는다).
- 우선순위: 지금 보는 카드의 현재가(0) → 그 카드의 일별 시세(1) → 밀린 것 따라잡기 · 주기적 갱신(2).
- 다른 카드를 열면 앞 카드의 '아직 안 보낸' 보기 요청은 버린다 — 보고 있는 카드가 먼저.
- 최근 본 카드 · 즐겨찾기는 refresh_minutes 마다 현재가를 다시 받는다 — 다음에 열 때 최신 시세가 바로 보인다.
- 앱이 꺼져 있던 동안의 시세: 켤 때 catch_up() 이 30일 안에 본 카드 중 오래된 것을 다시 받고,
  응답에 든 최근 약 4개월치 일별 시세로 빈 날짜를 채운다 (FcDb.put_price).
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from datetime import datetime, timedelta

from data_prep.market import MAX_GRADE, MarketBlocked

log = logging.getLogger("fclab.market")

VIEW, VIEW_HISTORY, BACKGROUND = 0, 1, 2
LIST, GRAPH = "list", "graph"


class MarketService:
    def __init__(self, client, db, ttl_hours: float = 6, enabled: bool = True, name_of=None,
                 current_ttl_minutes: float = 30, refresh_minutes: float = 60):
        self.client, self.db = client, db
        self.ttl = ttl_hours                      # 일별 시세
        self.current_ttl = current_ttl_minutes    # 현재가
        self.refresh_minutes = refresh_minutes
        self.name_of = name_of or (lambda _spid: None)   # spid → 카드 이름 (선수 검색에 쓴다)
        self.enabled = enabled and client is not None
        self.blocked = False
        self.errors: dict[int, str] = {}
        self.filled: dict[tuple[int, int], int] = {}   # (카드, 강화) → 앱이 꺼져 있던 동안 새로 채운 날짜 수
        self._queue: list = []                    # (우선순위, 순번, 종류, 키) — 키: 이름(LIST) 또는 (spid, 강화)(GRAPH)
        self._queued: set[tuple] = set()          # (종류, 키)
        self._running: tuple | None = None
        self._waiting: dict[str, set[int]] = {}   # 이름 → 그 검색으로 현재가를 기다리는 카드 (결과에 없으면 일별 시세로 대신)
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None
        self._refresher: threading.Thread | None = None

    # ── 화면이 부르는 것 ─────────────────────────────────────────

    def table(self, spid: int) -> dict[int, dict]:
        return self.db.prices(spid, self.ttl, self.current_ttl)

    def want(self, spids: list[int], grades=range(1, MAX_GRADE + 1), force: bool = False) -> None:
        """보는 카드 — 현재가는 바로(선수 검색), 일별 시세는 뒤이어. 다른 카드의 '아직 안 보낸' 보기 요청은 버린다.

        force = '지금 다시 받기' — 시간이 안 됐어도 다시 받되, 10분 안에 받은 것은 건너뛴다 (연타 방지).
        """
        if not self.enabled or self.blocked:
            return
        self.db.watch(spids)
        have = {spid: self.table(spid) for spid in spids}
        recent = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        with self._cv:
            names = {self.name_of(spid) for spid in spids} - {None}
            self._queue = [item for item in self._queue if item[0] == BACKGROUND
                           or (item[2] == LIST and item[3] in names) or (item[2] == GRAPH and item[3][0] in spids)]
            heapq.heapify(self._queue)
            self._queued = {(kind, key) for _, _, kind, key in self._queue}
            self._waiting = {name: waiting for name, waiting in self._waiting.items() if name in names}
            for spid in spids:
                rows = have[spid].values()
                fresh = any(r["fetched_at"] >= recent for r in rows) if force else any(r["fresh"] for r in rows)
                if fresh:
                    continue
                name = self.name_of(spid)
                if name:
                    self._waiting.setdefault(name, set()).add(spid)
                    self._push(VIEW, LIST, name)
                else:   # 이름을 모르면 일별 시세 응답의 현재가로
                    for grade in grades:
                        self._push(VIEW, GRAPH, (spid, grade))
            for grade in grades:          # 단계 → 카드 순서로 섞어 두 카드 비교가 같이 채워지게
                for spid in spids:
                    row = have[spid].get(grade)
                    if row and (row["current"] is None or (row["history_fresh"] and not force)):
                        continue
                    self._push(VIEW_HISTORY, GRAPH, (spid, grade))
            self._start()
            self._cv.notify()

    def want_history(self, spid: int, grade: int) -> None:
        """그래프로 고른 강화 단계를 먼저 받는다."""
        if not self.enabled or self.blocked:
            return
        with self._cv:
            self._queue = [item for item in self._queue if (item[2], item[3]) != (GRAPH, (spid, grade))]
            heapq.heapify(self._queue)
            self._queued.discard((GRAPH, (spid, grade)))
            self._push(VIEW, GRAPH, (spid, grade))
            self._start()
            self._cv.notify()

    def pending(self, spid: int) -> int:
        """이 카드를 위해 남은 요청 수 (보내는 중 포함)."""
        name = self.name_of(spid)
        with self._cv:
            items = self._queued | ({self._running} if self._running else set())
            return sum(1 for kind, key in items if (kind == GRAPH and key[0] == spid) or (kind == LIST and key == name))

    def loading_current(self, spid: int) -> bool:
        name = self.name_of(spid)
        with self._cv:
            return name is not None and ((LIST, name) in self._queued or self._running == (LIST, name))

    def catch_up(self, limit: int = 60) -> int:
        """앱을 켤 때 · 주기적으로 — 최근 본 카드 · 즐겨찾기의 현재가와 30일 안에 받은 일별 시세 중 오래된 것을 다시 받는다."""
        if not self.enabled or self.blocked:
            return 0
        watched = self.db.stale_current(self.db.watched(), self.current_ttl)
        histories = self.db.stale_histories(self.ttl, limit)
        with self._cv:
            before = len(self._queued)
            for spid in watched:
                name = self.name_of(spid)
                if name:
                    self._push(BACKGROUND, LIST, name)
            for spid, grade in histories:
                self._push(BACKGROUND, GRAPH, (spid, grade))
            queued = len(self._queued) - before
            if queued:
                self._start()
                self._cv.notify()
        self._start_refresher()
        return queued

    # ── 백그라운드 ───────────────────────────────────────────────

    def _push(self, priority: int, kind: str, key) -> None:
        if (kind, key) in self._queued or self._running == (kind, key):
            return
        heapq.heappush(self._queue, (priority, next(self._seq), kind, key))
        self._queued.add((kind, key))

    def _start(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, name="market", daemon=True)
            self._worker.start()

    def _start_refresher(self) -> None:
        if not self.refresh_minutes or (self._refresher and self._refresher.is_alive()):
            return

        def run() -> None:
            while not self.blocked:
                time.sleep(self.refresh_minutes * 60)
                try:
                    queued = self.catch_up()
                    if queued:
                        log.info("시세 주기 갱신 %d건 예약", queued)
                except Exception:  # noqa: BLE001 — 갱신이 한 번 실패해도 다음 주기에 다시
                    log.exception("시세 주기 갱신 실패")

        self._refresher = threading.Thread(target=run, name="market-refresh", daemon=True)
        self._refresher.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    if not self._cv.wait(timeout=60):
                        self._worker = None
                        return
                _, _, kind, key = heapq.heappop(self._queue)
                self._queued.discard((kind, key))
                self._running = (kind, key)
                cards = self._waiting.pop(key, set()) if kind == LIST else {key[0]}
            try:
                if kind == LIST:
                    self._fetch_list(key, cards)
                else:
                    self._fetch_graph(*key)
            except MarketBlocked as exc:
                self.blocked = True
                for spid in cards:
                    self.errors[spid] = str(exc)
                with self._cv:
                    self._queue.clear()
                    self._queued.clear()
            except Exception as exc:  # noqa: BLE001 — 한 칸 실패가 나머지를 막지 않게
                log.warning("시세 받기 실패 %s %s: %s", kind, key, exc)
                for spid in cards:
                    self.errors[spid] = "시세를 받지 못한 단계가 있습니다. 잠시 뒤 다시 시도하세요."
            finally:
                with self._cv:
                    self._running = None

    def _fetch_list(self, name: str, cards: set[int]) -> None:
        """이름 검색 1번 → 그 선수 카드 전부의 현재가. 기다리던 카드가 없으면(같은 이름이 많은 선수) 시즌을 좁혀 한 번 더,
        그래도 없으면 일별 시세 응답의 현재가로 대신 채운다."""
        rows = self.client.player_list(name)
        self.db.put_listing(rows)
        missing = cards - {r["spid"] for r in rows}
        if missing:
            rows = self.client.player_list(name, seasons=sorted({spid // 1_000_000 for spid in missing}))
            self.db.put_listing(rows)
            missing -= {r["spid"] for r in rows}
        with self._cv:
            for spid in missing:
                for grade in range(1, MAX_GRADE + 1):
                    self._push(VIEW, GRAPH, (spid, grade))
        for spid in cards - missing:
            self.errors.pop(spid, None)

    def _fetch_graph(self, spid: int, grade: int) -> None:
        added = self.db.put_price(spid, grade, self.client.price(spid, grade))
        if added > 1:
            self.filled[(spid, grade)] = added
        self.errors.pop(spid, None)
