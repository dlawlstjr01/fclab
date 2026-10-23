"""🤖 AI 로 찾기 — 조건마다 맞는 길로 (F2011). 화면(views/home.py)은 결과만 그린다.

길 네 가지 (route):
- ranker  랭커 기록으로 줄 세우는 조건 — '랭커가 골을 많이 넣는 윙어' · '유효슈팅이 많은 스트라이커' · '블록을 많이 하는 센터백' ·
          '패스 성공률이 높은 중앙 미드필더'. FC 온라인 랭커 경기 기록으로 — 분석해 둔 카드(카드별 최근 최대 20경기 평균)와
          어제 데일리 기록(랭커 상위 1만 명) TOP. 나무위키 · LLM 은 이 기록을 모른다.
- ability 카드 능력치로 줄 세우는 조건 — '드리블이 좋은 윙어' · '발 빠른 한국 윙어' · '태클이 좋은 수비형 미드필더'.
          데이터센터 조건 검색(국적 · 포지션 …) + 모아 둔 능력치 높은 순.
- facts   국적 · 리그 · 클럽 · 포지션 · 레전드 · 현역 · 특성처럼 데이터로 다 말할 수 있는 조건 — 데이터센터 조건 검색 바로
          (빠짐없고 정확하다 — 나무위키를 거치면 20 ~ 80초 걸리고, 여자 국가대표팀 문서 · 이름만 아는 선수가 섞였다).
- namu    실제 세상 지식이 필요한 조건 (수상 · 우승 · 기록 · '최고의' · 트레블 · 월드컵 …) — 1차 나무위키 → 2차 AI 명단 →
          3차 FC 온라인 DB (services/namufind.py). 그 지식이 팀컬러(트레블 · 갈락티코 …)로도 있으면 그 팀컬러 카드를 '그 밖의 카드'로.
현역 = 최근 두 해의 현역 시즌(LIVE · TOTS · PL …) 카드가 있는 선수, 레전드 = 아이콘 카드가 있는 선수 (services/finder.py).
"""

from __future__ import annotations

import logging
from dataclasses import replace

from data_prep.datacenter import DAILY_STATS, USAGE_GROUP
from data_prep.players import GROUP_OF, STATS
from services.explore import parse_condition, ranker_stat
from services.finder import Filters
from services.insights import leaderboard
from services.namufind import NamuFinder

log = logging.getLogger("fclab.aisearch")

RANKER_MIN = 5   # 랭커 기록 순위에 넣을 최소 경기 수 (적으면 평균이 흔들린다)
# 데이터(국적 · 포지션 …)로는 말할 수 없는 실제 세상 지식 — 이 말이 있으면 나무위키 → AI 명단으로
KNOWLEDGE_WORDS = ("최고", "최강", "베스트", "역대", "전설적", "TOP", "에이스", "우승", "득점왕", "도움왕", "어시스트왕", "MVP",
                   "수상", "받은", "올해의", "월드컵", "유로", "올림픽", "챔스", "챔피언스리그", "트레블", "황금", "세대", "갈락티코",
                   "무패", "발롱", "주장", "캡틴", "원클럽", "유스", "임대", "이적", "듀오", "트리오", "콤비", "라이벌", "형제",
                   "국가대표", "대표팀", "최다", "기록", "결승", "4강", "메달", "명예", "전성기", "출전", "데뷔", "은퇴한")
RANKER_SHOWN = 60
STAT_UNITS = {key: (name, unit) for key, name, unit in STATS}
DAILY_ORDER = {"goal": "GOAL", "assist": "ASSIST", "effective_shoot": "EFFSHOOT", "shoot": "NORMALSHOOT", "pass_rate": "PASS",
               "dribble_rate": "DRIBBLE", "tackle": "TACKLE", "block": "BLOCK"}
# 데이터센터 조건 검색으로도 찾을 만한 사실 — 이 중 하나라도 있으면 AI 명단 밖의 카드도 '그 밖의 카드'로 보인다
FACTS = ("nation", "league", "club", "teamcolor", "teamcolor_any", "teamcolor_also", "player_teamcolors", "traits", "traits_any",
         "seasons", "physical", "skill_moves", "reputation")


BEST_WORDS = ("최고", "최강", "베스트", "에이스", "TOP")   # '최고의 …' — 실제 세상 평가(AI 명단) + 게임 안 평가(랭커가 많이 쓰는 카드)


def best_usage(condition: str, filters: Filters | None, finder, daily) -> dict | None:
    """'현역 최고의 스트라이커'처럼 최고 + 포지션이면 랭커가 그 자리에 가장 많이 쓰는 카드 (어제 데일리 차트, 자리마다 16장) —
    현역만 · 레전드만이면 그대로 거른다. 게임 안의 '최고'는 랭커가 실제로 쓰는 카드라서."""
    plain = condition.replace(" ", "").upper()
    if filters is None or not filters.positions or not any(w in plain for w in BEST_WORDS) or not getattr(daily, "enabled", False):
        return None
    codes = list(dict.fromkeys(USAGE_GROUP.get(p, p) for p in filters.positions))[:2]
    keep = finder.legends() if filters.legend else (finder.actives() if filters.legend is False else None)
    rows, date = [], ""
    for code in codes:
        try:
            data = daily.usage(code, wait=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("AI 로 찾기 — 포지션 이용 차트 받기 실패: %s", exc)
            continue
        if not data:
            continue
        date = data["date"]
        rows += [{**r, "at": code} for r in data["rows"] if keep is None or r["spid"] % 1_000_000 in keep]
    if not rows:
        return None
    rows.sort(key=lambda r: -r["share"])
    return {"date": date, "codes": codes, "rows": rows[:16]}


def knowledge(condition: str) -> bool:
    """실제 세상 지식(수상 · 우승 · 기록 · '최고' …)이 필요한 조건인가."""
    plain = condition.replace(" ", "").upper()
    return any(w.replace(" ", "").upper() in plain for w in KNOWLEDGE_WORDS)


def route(condition: str, filters: Filters | None) -> str:
    """'ranker' · 'ability' · 'facts' · 'namu' — 랭커 스탯 말 + 많은 · 높은 · 랭커 · 성공률이면 랭커 기록, 실제 세상 지식 말이 있으면
    나무위키 → AI 명단, 능력치 말(드리블 · 빠른 · 태클 …)이면 카드 능력치, 데이터로 다 말할 수 있으면 데이터센터 조건 검색."""
    if ranker_stat(condition):
        return "ranker"
    if knowledge(condition) or filters is None or filters.empty():
        return "namu"
    return "ability" if filters.ability else "facts"


def facts(filters: Filters | None, condition: str) -> Filters | None:
    """AI 명단과 함께 데이터센터 조건 검색으로도 찾을 조건 — 국적 · 리그 · 클럽 · 팀컬러(트레블 · 듀오) · 특성 같은 사실이 있을 때.
    수상 · 업적 말로 읽은 시즌 팀컬러('발롱도르' → Ballon d'Or 시즌 카드)는 수상자와 다르다(후보도 든다) — '팀컬러'라고 말했을
    때만 쓴다. 포지션 · 레전드 · 현역만이면 None (전체에서 OVR 높은 카드일 뿐이라)."""
    if filters is None or filters.empty():
        return None
    f = filters if "팀컬러" in condition.replace(" ", "") else replace(filters, player_teamcolors=())
    return f if any(getattr(f, name) for name in FACTS) else None


def ranker_result(condition: str, key: str, players: list[dict], finder, daily) -> dict:
    """랭커 기록 순위 — 분석해 둔 카드(최소 RANKER_MIN 경기) 중 조건의 포지션 · 구분(현역 · 레전드), 그 스탯 높은 순 +
    어제 데일리 기록의 그 스탯 TOP 에서 같은 포지션 · 구분."""
    cond = parse_condition(condition)
    groups, kind = set(cond["groups"]), cond["kind"] or "전체"
    legends, actives = finder.legends(), finder.actives()
    rows = leaderboard(players, None, kind, key, RANKER_MIN, legends, actives)
    if groups:
        rows = [r for r in rows if r["player"].get("position_group") in groups]
    name, unit = STAT_UNITS[key]
    out = {"mode": "ranker", "condition": condition, "stat": key, "stat_name": name, "unit": unit, "groups": sorted(groups),
           "kind": kind, "pool": len(rows),
           "rows": [{"spid": r["player"]["spid"], "value": r[key], "matches": r["match_count"]} for r in rows[:RANKER_SHOWN]],
           "daily": None}
    order = DAILY_ORDER.get(key)
    if order and getattr(daily, "enabled", False):
        try:
            data = daily.stats(order, wait=True)
        except Exception as exc:  # noqa: BLE001 — 데일리를 못 받으면 분석 카드 순위만
            log.warning("AI 로 찾기 — 데일리 기록 받기 실패: %s", exc)
            data = None
        if data:
            keep = []
            for r in data["rows"]:
                pid = r["spid"] % 1_000_000
                if groups and GROUP_OF.get(r["position"]) not in groups:
                    continue
                if (kind == "레전드" and pid not in legends) or (kind == "현역" and pid not in actives):
                    continue
                keep.append(r)
            out["daily"] = {"date": data["date"], "order": order, "label": DAILY_STATS[order], "rows": keep}
    return out


def confirm(result: dict, members: set[int], fact: Filters) -> list[dict]:
    """지식 조건이 FC 온라인 팀컬러로도 있으면(트레블 · 갈락티코 …) AI 명단을 그 팀컬러 카드가 있는 선수로 확인한다 — 없는 선수는
    '뺀 선수'로 (AI 가 잘못 넣은 것일 수 있다 — 트레블에 벤제마). result 를 고쳐 쓰고 남은 선수를 돌려준다."""
    names = [n for _, n in ([fact.teamcolor] if fact.teamcolor else []) + list(fact.teamcolor_any)]
    label = " · ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")
    kept, out = [], []
    for p in result["players"]:
        (kept if p["pid"] in members else out).append(p)
    result["players"] = kept
    result["dropped"] = result.get("dropped", []) + [
        {"name": p["name"], "reason": f"팀컬러({label})에 이 선수의 카드가 없음 — AI 가 잘못 넣었을 수 있음"} for p in out]
    return kept


def search(condition: str, *, finder, model, namu, db, catalog, players: list[dict], daily, progress=None) -> dict:
    """조건 → 결과 {mode: 'ranker' | 'finder' | 'namu' | 'none', …}. progress(글)로 지금 단계를 알린다.
    'none' 이면 화면이 예전 방식(메타데이터 색인 후보 → LLM 선별)으로 찾는다."""
    say = progress or (lambda _text: None)
    say("조건 읽기 — 국적 · 포지션 · 레전드 · 능력치 같은 칸 (AI)")
    filters, unknown = finder.parse(condition, model) if finder.enabled else (None, [])
    way = route(condition, filters)
    if way == "ranker":
        key = ranker_stat(condition)
        say(f"랭커 기록으로 — {STAT_UNITS[key][0]} 높은 순 (분석해 둔 카드 · 어제 데일리 기록)")
        return {**ranker_result(condition, key, players, finder, daily), "filters": filters, "unknown": unknown}
    if way in ("ability", "facts") and finder.enabled:
        say("카드 능력치로 — 데이터센터 조건 검색 → 모아 둔 능력치 높은 순" if way == "ability"
            else "데이터센터 조건 검색 — 국적 · 리그 · 클럽 · 포지션 · 레전드 · 현역 같은 사실은 FC 온라인 데이터로")
        rows = finder.search(filters)
        if rows:
            return {"mode": "finder", "condition": condition, "filters": filters, "unknown": unknown,
                    "spids": [r["spid"] for r in rows], "way": way}
    result = None
    try:
        result = NamuFinder(namu, db, catalog, finder, model).run(condition, filters, progress=say)
    except Exception as exc:  # noqa: BLE001 — AI 명단을 못 만들면 데이터센터 조건 검색으로
        log.exception("AI 로 찾기 (나무위키 → AI 명단) 실패")
        say(f"AI 명단을 만들지 못했습니다 — {exc.__class__.__name__}")
    players_found = (result or {}).get("players", [])
    extra = []
    fact = facts(filters, condition)
    if fact is not None and players_found and not (fact.teamcolor or fact.teamcolor_any or fact.teamcolor_also
                                                   or fact.player_teamcolors):
        fact = None   # 지식 조건에 붙은 국적 · 클럽만으로는 '그 밖의 카드'가 그 나라 · 클럽 전체라 — 팀컬러로 된 지식일 때만
    if fact is None and not players_found and filters is not None and not filters.empty():
        fact = filters   # AI 명단이 비면 읽은 조건 그대로라도
    if fact is not None and finder.enabled:
        say("데이터센터 조건 검색 — 같은 조건의 다른 카드")
        rows = finder.search(fact)
        if players_found and (fact.teamcolor or fact.teamcolor_any):
            players_found = confirm(result, {r["spid"] % 1_000_000 for r in rows}, fact)
        shown = {p["pid"] for p in players_found}
        extra = [r["spid"] for r in rows if r["spid"] % 1_000_000 not in shown]
    usage = best_usage(condition, filters, finder, daily)
    if usage:
        say(f"랭커가 {' · '.join(usage['codes'])} 에 가장 많이 쓰는 카드 — 어제 데일리 차트")
    if not players_found and not extra and not usage:
        return {"mode": "none", "condition": condition, "filters": filters, "unknown": unknown}
    return {"mode": "namu", "condition": condition, "filters": filters, "unknown": unknown, "namu": result, "extra": extra,
            "usage": usage}
