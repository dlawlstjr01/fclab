"""데일리 기록 · 차트 (FC 온라인 데이터센터) — 파워 랭킹 · 홈 · 짝 추천이 쓴다.

- 선수 기록 TOP (/datacenter/dailyrecord 의 '선수 기록'): 어제 랭커(상위 1만 명)가 쓴 카드의 출전 · 공격 포인트 · 득점 ·
  도움 · 패스 · 태클 · 평점 … 기록마다 18장 — 지금 실제로 쓰이는 카드가 시즌 · 강화 · 포지션 OVR 과 함께 온다.
- 포메이션 이용률 · 포지션별 이용 카드 (데일리 차트).
매일 12시에 전날 기록이 올라온다. 받은 것은 SQLite(dc_cache)에 두고 그날 다시 묻지 않는다. 화면은 받아 둔 것을 바로 그리고,
없으면 뒤에서 받기 시작한 뒤 '불러오는 중'으로 둔다 (홈이 데이터센터를 기다리지 않게).
"""

from __future__ import annotations

import logging
import threading
import time

from data_prep.datacenter import daily_date
from data_prep.market import MarketBlocked

log = logging.getLogger("fclab.daily")

RETRY_SECONDS = 600


class DailyBoard:
    def __init__(self, client, db):
        self.client, self.db = client, db
        self.enabled = client is not None
        self._pending: set[tuple] = set()
        self._failed: dict[tuple, float] = {}
        self._lock = threading.Lock()

    # ── 받기 ─────────────────────────────────────────────────────

    def stats(self, order: str = "ATTACKPOINT", mode: str = "official", wait: bool = False) -> dict | None:
        """선수 기록 TOP {"date", "rows": parse_daily_stats} — 아직 없으면 None (뒤에서 받는 중)."""
        return self._get(("stats", order, mode), lambda day: self.client.daily_stats(order, mode, day), wait)

    def formations(self, mode: str = "official", wait: bool = False) -> dict | None:
        """포메이션 이용률 {"date", "rows": [{formation, users, share}]}."""
        return self._get(("formations", mode), lambda day: self.client.formations(mode, day), wait)

    def usage(self, position: str, mode: str = "official", wait: bool = False) -> dict | None:
        """그 포지션에 랭커가 많이 쓴 카드 16장 {"date", "rows": parse_usage} (날짜를 고를 수 없어 늘 최신)."""
        return self._get(("usage", position, mode), lambda day: self.client.position_usage(position, mode), wait,
                         retry_old_day=False)

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _key(self, job: tuple) -> str:
        return "daily:" + ":".join(map(str, job)) + ":" + daily_date()

    def _get(self, job: tuple, fetch, wait: bool, retry_old_day: bool = True) -> dict | None:
        data = self.db.dc_get(self._key(job), 36)
        if data is not None or not self.enabled:
            return data
        if wait:
            return self._fetch(job, fetch, retry_old_day)
        with self._lock:
            if job in self._pending or time.monotonic() - self._failed.get(job, -RETRY_SECONDS) < RETRY_SECONDS:
                return None
            self._pending.add(job)
        threading.Thread(target=self._fetch, args=(job, fetch, retry_old_day, True), name="daily", daemon=True).start()
        return None

    def _fetch(self, job: tuple, fetch, retry_old_day: bool, background: bool = False) -> dict | None:
        try:
            day = daily_date()
            rows = fetch(day)
            if not rows and retry_old_day:   # 그날 기록이 아직 안 올라왔으면 하루 전
                day = daily_date(back=1)
                rows = fetch(day)
            data = {"date": day, "rows": rows}
            if rows:
                self.db.dc_put(self._key(job), data)
            else:   # 빈 응답 — 화면이 다시 그려질 때마다 다시 묻지 않게 잠시 쉰다
                with self._lock:
                    self._failed[job] = time.monotonic()
            return data
        except MarketBlocked:
            self.enabled = False
            return None
        except Exception as exc:  # noqa: BLE001 — 화면은 '불러오지 못함'으로 계속
            log.warning("데일리 기록 받기 실패 %s: %s", job, exc)
            with self._lock:
                self._failed[job] = time.monotonic()
            return None
        finally:
            if background:
                with self._lock:
                    self._pending.discard(job)
