"""카드 상세 — 특성(일반 · 신규) · 능력치 · 클럽 경력 · 급여 · 팀컬러 · 포지션별 OVR 을 데이터센터에서 받아
SQLite 에 두고 바로 쓴다. 카드 한 장에 두 번 묻는다: 미리보기(특성 · 능력치 · 클럽 경력) + 선수 정보(팀컬러 · 포지션별 OVR).

선수 페이지 · 스쿼드(특성 · 팀컬러) · 강화 시뮬레이터가 쓴다. 받는 것은 백그라운드 스레드 하나가 차례로 —
시세와 같은 데이터센터 창구라 요청 간격을 나눠 쓴다. 화면은 받아 둔 것을 바로 그리고, 없는 카드는
'불러오는 중'으로 두었다가 받는 대로 채운다. 특성 · 능력치는 자주 바뀌지 않아 7일 동안 다시 받지 않는다.
"""

from __future__ import annotations

import logging
import threading
import time

from data_prep.datacenter import ABILITY_GROUPS
from data_prep.enhance import GRADE_OVR
from data_prep.market import MarketBlocked
from services.teamcolor import effect_bonus

log = logging.getLogger("fclab.cardinfo")

RETRY_SECONDS = 600   # 못 받은 카드는 10분 뒤에 다시
KEEP_DAYS = 3650      # 받아 둔 상세는 오래돼도 바로 보여 주고, max_age 가 지나면 뒤에서 새로 받는다
VERSION = 2           # 저장 형식 — 예전 형식(팀컬러 없음)으로 받아 둔 카드는 다시 받는다


def bonus_stats(info: dict, grade: int, adapt: int, effects: list[list[str]]) -> dict:
    """추가 능력치를 바로 계산 — 강화(모든 능력치 + 강화 표) · 적응도(단계마다 +1, 5단계 = +4) · 팀컬러 효과를 더한다.

    데이터센터 값과 같다는 것을 확인했다 (마라도나 8강: 속력 +15, 적응도 5: +4 더, 바르셀로나 Lv.4: +4 · 짧은 패스 +3 더).
    포지션별 OVR 은 모든 능력치에 더한 만큼 오른다 — 특정 능력치만 오르는 효과가 OVR 에 미치는 정도는 포지션마다 달라
    exact=False 로 알리고, 정확한 값은 데이터센터 계산(exact)을 뒤에서 받는다.
    """
    every, each = GRADE_OVR[grade] + max(0, adapt - 1), {}
    for effect in effects:
        add, extra = effect_bonus(effect)
        every += add
        for name, value in extra.items():
            each[name] = each.get(name, 0) + value
    stats = {name: value + every + each.get(name, 0) for name, value in (info.get("stats") or {}).items()}
    summary = {}
    for group, value in (info.get("summary") or {}).items():
        names = ABILITY_GROUPS.get(group, ())
        extra = round(sum(each.get(n, 0) for n in names) / len(names)) if names else 0
        summary[group] = value + every + extra
    return {"stats": stats, "summary": summary, "ovr": (info.get("ovr") or 0) + every,
            "position_ovr": {pos: value + every for pos, value in (info.get("position_ovr") or {}).items()},
            "exact": not any(each.values())}


class CardInfoService:
    def __init__(self, client, db, enabled: bool = True, max_age_days: float = 7):
        self.client, self.db = client, db
        self.enabled = enabled and client is not None
        self.max_age = max_age_days
        self._queue: list[int] = []
        self._pending: set[int] = set()
        self._failed: dict[int, float] = {}
        self._exact: dict[int, tuple] = {}   # 카드 → 가장 최근에 고른 추가 능력치 조합 (아직 안 보낸 것만 — 앞의 것은 버린다)
        self._exact_running: tuple | None = None
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None

    def get(self, spids: list[int]) -> dict[int, dict]:
        """받아 둔 상세 — 오래된 것도 그대로 (새로 받는 것은 want 가 뒤에서)."""
        return {s: i for s, i in self.db.card_info(list(spids), KEEP_DAYS).items() if i.get("v") == VERSION}

    def want(self, spids: list[int]) -> None:
        """받아 둔 것이 없는 카드를 받는다 — 지금 보는 카드가 먼저 (줄 앞에, 보이는 순서대로)."""
        if not self.enabled:
            return
        have = self.get(spids)
        fresh = {s for s, i in self.db.card_info(list(spids), self.max_age).items() if i.get("v") == VERSION}
        now = time.monotonic()
        with self._cv:
            queued = set(self._queue)
            wanted = [s for s in dict.fromkeys(spids)   # 지금 받고 있는 카드(줄에서 빠졌지만 pending)는 다시 넣지 않는다
                      if s not in have and (s in queued or s not in self._pending)
                      and now - self._failed.get(s, -RETRY_SECONDS) >= RETRY_SECONDS]
            stale = [s for s in dict.fromkeys(spids) if s in have and s not in fresh and s not in queued
                     and now - self._failed.get(s, -RETRY_SECONDS) >= RETRY_SECONDS]
            # 없는 카드는 맨 앞에 ('받는 중'으로 센다), 오래된 카드는 맨 뒤에 (화면은 받아 둔 것을 그대로 보여 준다)
            self._queue = wanted + [s for s in self._queue if s not in wanted] + stale
            self._pending.update(wanted)
            if self._queue:
                self._start_worker()
                self._cv.notify()

    @staticmethod
    def _exact_key(spid: int, combo: tuple) -> str:
        grade, adapt, (tid, tlv), (eid, elv), feature = combo
        return f"ability2:{spid}:{grade}:{adapt}:{tid}:{tlv}:{eid}:{elv}:{feature}"

    def exact(self, spid: int, combo: tuple) -> dict | None:
        """데이터센터가 계산한 추가 능력치(받아 둔 것) — 없으면 뒤에서 받기 시작하고 None (화면은 bonus_stats 로 먼저).
        combo = (강화, 적응도, (소속 팀컬러, 단계), (강화 팀컬러, 단계), 특성 팀컬러)."""
        data = self.db.dc_get(self._exact_key(spid, combo), 24 * KEEP_DAYS)
        if data is None and self.enabled:
            with self._cv:
                if self._exact_running != (spid, combo):
                    self._exact[spid] = combo
                    self._start_worker()
                    self._cv.notify()
        return data

    def exact_waiting(self, spid: int, combo: tuple) -> bool:
        with self._cv:
            return self._exact.get(spid) == combo or self._exact_running == (spid, combo)

    def _start_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, name="cardinfo", daemon=True)
            self._worker.start()

    def pending(self, spids: list[int]) -> int:
        with self._cv:
            return sum(1 for spid in spids if spid in self._pending)

    def _run_exact(self, spid: int, combo: tuple) -> None:
        grade, adapt, teamcolor, enhance, feature = combo
        try:
            data = self.client.ability(spid, grade, adapt, teamcolor, enhance, feature)
            if data:
                self.db.dc_put(self._exact_key(spid, combo), data)
        except MarketBlocked:
            self.enabled = False
        except Exception as exc:  # noqa: BLE001 — 화면은 바로 계산한 값을 그대로 쓴다
            log.warning("추가 능력치 계산 받기 실패 %s: %s", spid, exc)
        finally:
            with self._cv:
                self._exact_running = None

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._exact:
                    if not self._cv.wait(timeout=60):
                        self._worker = None
                        return
                if self._exact:   # 사람이 고른 추가 능력치가 먼저
                    spid, combo = self._exact.popitem()
                    self._exact_running = (spid, combo)
                else:
                    combo = None
                    spid = self._queue.pop(0)
            if combo is not None:
                self._run_exact(spid, combo)
                continue
            try:
                info = self.client.preview(spid)
                ability = self.client.ability(spid) if info else None
                if info:
                    info.update(v=VERSION, teamcolors=(ability or {}).get("teamcolors"),
                                position_ovr=(ability or {}).get("position_ovr") or {})
                    self.db.put_card_info(spid, info)
                else:
                    self._failed[spid] = time.monotonic()
            except MarketBlocked:
                self.enabled = False
            except Exception as exc:  # noqa: BLE001 — 한 장 실패가 나머지를 막지 않게
                log.warning("카드 상세 받기 실패 %s: %s", spid, exc)
                self._failed[spid] = time.monotonic()
            finally:
                with self._cv:
                    self._pending.discard(spid)
