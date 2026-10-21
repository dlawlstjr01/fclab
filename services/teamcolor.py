"""팀컬러 — 데이터센터 팀컬러 목록을 SQLite 에 두고(새 팀컬러가 생기면 '처음 본 날'과 함께 더한다), 스쿼드의 팀컬러를 센다.

- 목록(약 800개 · 한 번에 받는다): 하루가 지나면 뒤에서 다시 받는다. 단계별 조건 · 효과는 팀컬러마다 따로 받아 30일 둔다.
- 카드가 어떤 팀컬러에 드는지는 데이터센터 '선수 정보'의 소속 · 관계 팀컬러 목록 (services/cardinfo.py 가 받는다) —
  클럽 팀컬러는 선수의 클럽 경력 기준이다.
- 강화 팀컬러(동빛 · 은빛 · 금빛 · 백금빛 물결)는 카드가 아니라 강화 등급으로 정해진다 ('8강 이상의 선수들로…').
- 공식 스쿼드 메이커처럼 소속 · 강화 · 관계마다 하나씩 적용된다 — 여기서는 효과가 가장 큰 것(같으면 단계 · 인원 순).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from data_prep.market import MarketBlocked

log = logging.getLogger("fclab.teamcolor")

GROUPS = ("소속", "강화", "특성")   # 공식 스쿼드 메이커의 이름 — '특성'은 데이터센터 선수 정보의 '관계 팀컬러'(연도별 · 듀오 등)
SOURCE = {"소속": "소속", "특성": "관계"}   # 카드 상세(teamcolors)에 담긴 이름
LIST_MAX_AGE = timedelta(hours=24)
DETAIL_MAX_AGE_DAYS = 30
RETRY_SECONDS = 600


class TeamColorService:
    def __init__(self, client, db, enabled: bool = True, clubs=lambda: set()):
        self.client, self.db = client, db
        self.enabled = enabled and client is not None
        self.clubs = clubs   # 선수 검색의 클럽 이름들 — 4xxxx 번호 중 클럽을 가려낸다
        self.new_ids: list[int] = []   # 마지막으로 목록을 받을 때 새로 생긴 팀컬러
        self._queue: list[int] = []
        self._pending: set[int] = set()
        self._failed: dict[int, float] = {}
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None
        self._refreshing = False
        self._memo: tuple[float, list[dict], str | None] | None = None   # (읽은 때, 목록, 받은 때) — 화면마다 800개를 다시 풀지 않게

    # ── 목록 ────────────────────────────────────────────────────

    def all(self, wait: bool = False) -> list[dict]:
        """팀컬러 전부 [{id, name, kind, max_level, max_count, effects, icon, first_seen, detail}].
        하루가 지났으면 뒤에서 새로 받는다 (wait=True 이고 하나도 없으면 지금 받는다)."""
        if self._memo and time.monotonic() - self._memo[0] < 60:
            rows, at = self._memo[1], self._memo[2]
        else:
            rows, at = self.db.teamcolors()
            self._memo = (time.monotonic(), rows, at)
        stale = at is None or datetime.now() - datetime.fromisoformat(at) > LIST_MAX_AGE
        if self.enabled and stale:
            if not rows and wait:
                self._refresh()
                rows, _ = self.db.teamcolors()
                self._memo = None
            else:
                self._refresh_async()
        return rows

    def _refresh_async(self) -> None:
        with self._cv:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh, name="teamcolors", daemon=True).start()

    def _refresh(self) -> None:
        try:
            rows = self.client.teamcolors()
            clubs = self.clubs()
            for tc in rows:   # 4xxxx 의 '시즌' · '관계' 중 선수 검색의 클럽 이름과 같으면 클럽
                if str(tc["id"]).startswith("4") and tc["name"] in clubs:
                    tc["kind"] = "클럽"
            if rows:
                self.new_ids = self.db.put_teamcolors(rows)
                self._memo = None
                if self.new_ids:
                    log.info("새 팀컬러 %d개: %s", len(self.new_ids), self.new_ids[:10])
        except MarketBlocked:
            self.enabled = False
        except Exception as exc:  # noqa: BLE001
            log.warning("팀컬러 목록 받기 실패: %s", exc)
        finally:
            self._refreshing = False

    # ── 단계별 조건 · 효과 ─────────────────────────────────────────

    def details(self, ids: list[int]) -> dict[int, dict]:
        return self.db.teamcolor_details(list(ids), DETAIL_MAX_AGE_DAYS)

    def detail_now(self, teamcolor_id: int) -> dict | None:
        """지금 받는다 (AI 추천 스쿼드에서 팀컬러를 골랐을 때)."""
        have = self.details([teamcolor_id])
        if teamcolor_id in have or not self.enabled:
            return have.get(teamcolor_id)
        detail = self.client.teamcolor_detail(teamcolor_id)
        if detail:
            self.db.put_teamcolor_detail(teamcolor_id, detail)
        return detail

    def want(self, ids: list[int]) -> None:
        if not self.enabled:
            return
        have = self.details(ids)
        now = time.monotonic()
        with self._cv:
            for i in ids:
                if i in have or i in self._pending or now - self._failed.get(i, -RETRY_SECONDS) < RETRY_SECONDS:
                    continue
                self._pending.add(i)
                self._queue.append(i)
            if self._queue:
                if self._worker is None or not self._worker.is_alive():
                    self._worker = threading.Thread(target=self._loop, name="teamcolor-detail", daemon=True)
                    self._worker.start()
                self._cv.notify()

    def pending(self, ids: list[int]) -> int:
        with self._cv:
            return sum(1 for i in ids if i in self._pending)

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    if not self._cv.wait(timeout=60):
                        self._worker = None
                        return
                teamcolor_id = self._queue.pop(0)
            try:
                detail = self.client.teamcolor_detail(teamcolor_id)
                if detail:
                    self.db.put_teamcolor_detail(teamcolor_id, detail)
                    self._memo = None
                else:
                    self._failed[teamcolor_id] = time.monotonic()
            except MarketBlocked:
                self.enabled = False
            except Exception as exc:  # noqa: BLE001
                log.warning("팀컬러 상세 받기 실패 %s: %s", teamcolor_id, exc)
                self._failed[teamcolor_id] = time.monotonic()
            finally:
                with self._cv:
                    self._pending.discard(teamcolor_id)


def levels_of(tc: dict, detail: dict | None) -> list[dict] | None:
    """단계 [{level, count, effects}] — 상세가 없으면 한 단계짜리만 목록 정보로 (여러 단계는 상세가 있어야 안다)."""
    if detail:
        return detail["levels"]
    if tc.get("max_level") == 1:
        return [{"level": 1, "count": tc["max_count"], "effects": tc.get("effects", [])}]
    return None


def squad_teamcolors(members: list[dict], teamcolors: list[dict], details: dict[int, dict]) -> dict:
    """스쿼드의 팀컬러.

    members: [{name, grade, code(자리, 선택), teamcolors: {소속: [{id, name}], 관계: [...]} 또는 None(아직 모름)}]
    돌려주는 값: {"groups": {소속 · 강화 · 특성: [팀컬러, ...] (적용되는 것 먼저)}, "unknown": 팀컬러를 아직 모르는 선수 수,
                 "need_detail": 단계 조건을 받아야 하는 팀컬러 번호}
    팀컬러: {id, name, icon, kind, count, members[이름], codes[자리], level(0 = 아직), effects, next(다음 단계 인원) 또는 None,
            known}
    """
    by_id = {tc["id"]: tc for tc in teamcolors}
    counted: dict[str, dict[int, list[dict]]] = {g: {} for g in GROUPS}
    for m in members:
        for group, source in SOURCE.items():
            for t in (m.get("teamcolors") or {}).get(source, []):
                counted[group].setdefault(t["id"], []).append(m)
    for tc in teamcolors:   # 강화 팀컬러 — 등급으로
        min_grade = (tc.get("detail") or details.get(tc["id"]) or {}).get("min_grade")
        if tc["kind"] == "강화" and min_grade:
            chosen = [m for m in members if (m.get("grade") or 1) >= min_grade]
            if chosen:
                counted["강화"][tc["id"]] = chosen
    need: list[int] = []
    groups: dict[str, list[dict]] = {}
    for group, ids in counted.items():
        entries = []
        for i, joined in ids.items():
            names = [m["name"] for m in joined]
            tc = by_id.get(i, {"id": i, "name": next((t["name"] for m in members for t in (m.get("teamcolors") or {})
                                                      .get(SOURCE.get(group, group), []) if t["id"] == i), str(i)),
                               "kind": group, "max_level": 0, "max_count": 0})
            levels = levels_of(tc, tc.get("detail") or details.get(i))
            if levels is None and len(names) >= 2:
                need.append(i)
            reached = [lv for lv in levels or [] if lv["count"] <= len(names)]
            upcoming = [lv for lv in levels or [] if lv["count"] > len(names)]
            entries.append({
                "id": i, "name": tc["name"], "icon": tc.get("icon", ""), "kind": tc.get("kind", group), "count": len(names),
                "members": names, "codes": [m.get("code") for m in joined], "level": reached[-1]["level"] if reached else 0,
                "effects": reached[-1]["effects"] if reached else [], "next": upcoming[0] if upcoming else None,
                "known": levels is not None,
            })
        entries.sort(key=lambda e: (-_strength(e["effects"]), -e["level"], -e["count"], e["name"]))
        groups[group] = entries
    unknown = sum(1 for m in members if m.get("teamcolors") is None)
    return {"groups": groups, "unknown": unknown, "need_detail": need}


def effect_bonus(effects: list[str]) -> tuple[int, dict[str, int]]:
    """팀컬러 효과 문구 → (모든 능력치에 더할 값, {능력치: 더할 값}). 예) '전체 능력치 +4' · '짧은 패스 +3'."""
    every, each = 0, {}
    for effect in effects:
        name, _, value = effect.rpartition("+")
        if not value.strip().isdigit():
            continue
        if name.strip().startswith("전체"):
            every += int(value)
        else:
            each[name.strip()] = each.get(name.strip(), 0) + int(value)
    return every, each


def _strength(effects: list[str]) -> int:
    """효과의 세기 — '전체 능력치 +4' 는 4 × 34, 개별 능력치 +n 은 n (같은 단계끼리 고를 때)."""
    total = 0
    for e in effects:
        value = int(e.rsplit("+", 1)[-1]) if "+" in e and e.rsplit("+", 1)[-1].isdigit() else 0
        total += value * 34 if e.startswith("전체") else value
    return total
