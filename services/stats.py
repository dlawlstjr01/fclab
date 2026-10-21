"""모든 카드의 능력치 · 급여 · OVR · 포지션 — 데이터센터 선수 검색으로 모아 SQLite(card_stats)에 둔다.

선수 검색은 한 번에 200장까지만 오고 페이지가 없다. 그래서 200장 안쪽 '조각'으로 나눠 묻는다.
- 시즌 하나가 200장 안쪽이면 시즌 전체가 한 조각.
- 넘으면 포지션별로, 포지션도 넘으면 OVR 구간으로 — OVR 높은 순으로 200장을 받아 가장 낮은 OVR 을 m 이라 하면
  m 보다 높은 카드는 모두 받은 것이다 (구간 [m+1, 위]). 다음은 m 이하로 다시 묻는다.
- 조각마다 능력치 칸(한 번에 4개)을 바꿔 9번 물으면 36칸(능력치 34 + 키 · 몸무게)이 다 모인다.
  첫 번(나누기)에 OVR · 급여 · 포지션과 속력 · 가속력 · 골 결정력 · 짧은 패스가 먼저 채워진다.

순서: 아직 안 나눈 시즌(새 시즌 포함) → 남은 능력치 칸 → refresh_days 가 지난 시즌 다시 나누기.
다시 나누면 급여 · OVR 이 갱신되고(로스터 패치 — 급여가 바뀌면 FcDb 가 기록), 카드나 OVR 이 바뀐 조각만 능력치를 다시 받는다.
카드 목록(넥슨 메타데이터)에 새 카드가 생겼는데 아직 없는 시즌은 하루에 한 번까지 다시 나눈다.
요청은 시세 · 카드 상세와 같은 창구(DatacenterClient)를 쓰고, 한 번 묻고 interval 초 쉰다 — 화면의 요청이 먼저 지나가게.
처음 전부 모으는 데는 요청 약 1만 번(몇 시간)이 걸린다. 앱을 끄면 멈췄다가 다음에 이어서 한다.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from data_prep.datacenter import POSITION_FILTER
from data_prep.db import STAT_PASSES
from data_prep.market import MarketBlocked

log = logging.getLogger("fclab.stats")

PAGE = 200   # 선수 검색 한 번에 오는 최대 장수
PASSES = (   # 능력치 칸 묶음 — 쓸모가 큰 것부터
    ("sprintspeed", "acceleration", "finishing", "shortpassing"),
    ("dribbling", "ballcontrol", "marking", "standingtackle"),
    ("strength", "stamina", "height", "weight"),
    ("shotpower", "longshots", "positioning", "vision"),
    ("crossing", "longpassing", "agility", "balance"),
    ("reactions", "interceptions", "headingaccuracy", "slidingtackle"),
    ("aggression", "jumping", "composure", "volleys"),
    ("penalties", "freekickaccuracy", "curve", "gkdiving"),
    ("gkhandling", "gkkicking", "gkreflexes", "gkpositioning"),
)
assert len(PASSES) == STAT_PASSES


def part_key(season: int, flt: dict) -> str:
    return f"{season}:{flt.get('position') or '*'}:{flt.get('ovr_min') or ''}-{flt.get('ovr_max') or ''}"


class StatsCrawler:
    def __init__(self, client, db, enabled: bool = True, interval: float = 2.5, refresh_days: float = 7):
        self.client, self.db = client, db
        self.enabled = enabled and client is not None
        self.interval = interval
        self.refresh = timedelta(days=refresh_days)
        self.current = ""   # 화면에 보일 지금 하는 일
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self.enabled and (self._worker is None or not self._worker.is_alive()):
            self._worker = threading.Thread(target=self._loop, name="stats-crawler", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {**self.db.stats_summary(), "running": bool(self._worker and self._worker.is_alive()), "current": self.current}

    # ── 요청 ────────────────────────────────────────────────────

    def _list(self, season: int, flt: dict, skills: tuple[str, ...]) -> list[dict]:
        rows = self.client.player_list(seasons=[season], positions=[flt["position"]] if flt.get("position") else None,
                                       ovr_min=flt.get("ovr_min"), ovr_max=flt.get("ovr_max"), skills=skills)
        self._stop.wait(self.interval)   # 화면이 보낸 요청(시세 · 카드 상세)이 먼저 지나가게
        return rows

    def _store(self, rows: list[dict]) -> bool:
        """저장하고, 새 카드나 OVR 이 바뀐 카드가 있었는지 돌려준다 (그 조각은 능력치를 다시 받는다)."""
        before = self.db.card_stats([r["spid"] for r in rows])
        changed = any(r["spid"] not in before or before[r["spid"]]["ovr"] != r.get("ovr") for r in rows)
        self.db.put_card_stats(rows)
        return changed

    # ── 나누기 (첫 번째 능력치 칸과 함께) ───────────────────────────

    def _windows(self, season: int, position: str | None) -> list[tuple[dict, list[dict]]]:
        """[(조각 필터, 그 조각의 첫 번 결과)] — OVR 높은 순 200장씩 내려가며."""
        out, top = [], None
        while not self._stop.is_set():
            flt = {"position": position, "ovr_max": top}
            rows = self._list(season, flt, PASSES[0])
            if len(rows) < PAGE:
                out.append(({**flt, "ovr_min": None}, rows))
                break
            low = min(r["ovr"] or 0 for r in rows)
            if low == max(r["ovr"] or 0 for r in rows):   # 같은 OVR 이 200장을 넘는다 — 이 OVR 은 받은 만큼만
                log.warning("시즌 %s %s OVR %s 카드가 %s장을 넘어 일부만 받았습니다", season, position, low, PAGE)
                out.append(({**flt, "ovr_min": low}, rows))
                top = low - 1
                continue
            out.append(({**flt, "ovr_min": low + 1}, [r for r in rows if (r["ovr"] or 0) > low]))
            top = low
        return out

    def _split(self, season: int) -> None:
        """시즌을 조각으로 나누고 첫 번째 능력치 칸을 채운다. 바뀐 것이 없는 조각은 모은 칸 수를 그대로 둔다."""
        self.current = f"시즌 {season} 나누는 중"
        old = {p["part"]: p for p in self.db.crawl_parts(season)}
        big = any(p["filter"].get("position") for p in old.values())   # 전에 포지션으로 나눈 시즌은 바로 포지션별로
        rows = [] if big else self._list(season, {}, PASSES[0])
        if not big and len(rows) < PAGE:
            windows = [({}, rows)]
        else:
            windows = []
            for position in POSITION_FILTER:
                windows += self._windows(season, position)
        if self._stop.is_set():
            return
        self.db.drop_crawl_parts(season)
        for flt, part_rows in windows:
            changed = self._store(part_rows)
            key = part_key(season, flt)
            kept = old.get(key, {}).get("passes", 1) if not changed else 1
            self.db.put_crawl_part(key, season, flt, len(part_rows), max(1, kept))

    def _fill(self, part: dict) -> None:
        """조각 하나의 다음 능력치 칸."""
        flt, done = part["filter"], part["passes"]
        self.current = f"시즌 {part['season']} 능력치 {done + 1}/{len(PASSES)}"
        rows = self._list(part["season"], flt, PASSES[done])
        if self._stop.is_set():
            return
        self.db.put_card_stats(rows)
        self.db.put_crawl_part(part["part"], part["season"], flt, part["cards"], done + 1)

    # ── 할 일 고르기 ─────────────────────────────────────────────

    def next_job(self):
        """('split', 시즌) · ('fill', 조각) 또는 None — 새 시즌 · 안 나눈 시즌 → 남은 칸 → 오래된 시즌 · 새 카드가 생긴 시즌."""
        parts = self.db.crawl_parts()
        split_at: dict[int, str] = {}
        for p in parts:
            split_at[p["season"]] = min(split_at.get(p["season"], p["updated_at"]), p["updated_at"])
        seasons = sorted(self.db.catalog_seasons(), reverse=True)   # 번호가 큰 것이 대체로 최근 시즌
        for season in seasons:
            if season not in split_at:
                return "split", season
        todo = [p for p in parts if p["passes"] < len(PASSES)]
        if todo:
            return "fill", max(todo, key=lambda p: (p["season"], -p["passes"]))
        now = datetime.now()
        new_cards = self.db.catalog_new_without_stats()   # {시즌: 능력치가 아직 없는 새 카드를 처음 본 때}
        for season in seasons:
            if now - datetime.fromisoformat(split_at[season]) > self.refresh or new_cards.get(season, "") > split_at[season]:
                return "split", season
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.next_job()
                if job is None:
                    self.current = ""
                    self._stop.wait(3600)   # 할 일이 없으면 한 시간 뒤에 새 시즌 · 오래된 시즌을 다시 본다
                elif job[0] == "split":
                    self._split(job[1])
                else:
                    self._fill(job[1])
            except MarketBlocked:
                self.enabled = False
                self.current = "robots.txt 가 허용하지 않아 멈췄습니다"
                return
            except Exception as exc:  # noqa: BLE001 — 한 번 실패로 멈추지 않는다 (잠시 쉬고 다시)
                log.warning("능력치 모으기 실패: %s", exc)
                self.current = "잠시 쉬는 중 (요청 실패)"
                self._stop.wait(120)


def main() -> None:
    """명령줄에서 따로 돌리기 — python -m services.stats [--once SEASON]."""
    import argparse

    from config import load_settings
    from data_prep.datacenter import DatacenterClient
    from data_prep.db import FcDb

    parser = argparse.ArgumentParser(description="FC 온라인 데이터센터에서 모든 카드의 능력치 · 급여를 모은다")
    parser.add_argument("--season", type=int, help="이 시즌만 (나누기 + 능력치 9칸)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = load_settings()
    db = FcDb(settings.storage_dir / "fclab.db")
    crawler = StatsCrawler(DatacenterClient(settings.market_interval), db, interval=settings.stats_interval,
                           refresh_days=settings.stats_refresh_days)
    if args.season:
        crawler._split(args.season)
        for part in db.crawl_parts(args.season):
            while part["passes"] < len(PASSES):
                crawler._fill(part)
                part = {**part, "passes": part["passes"] + 1}
        print(db.stats_summary())
        return
    crawler.start()
    try:
        while crawler._worker.is_alive():
            time.sleep(30)
            print(crawler.status(), flush=True)
    except KeyboardInterrupt:
        crawler.stop()


if __name__ == "__main__":
    main()
