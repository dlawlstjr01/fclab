"""화면에 보여 줄 계산 — 포지션 내 백분위, 스카우트 보드, 랭커 포지션 사용, 내 경기 분석. (F2010)

LLM 을 쓰지 않는 순수 계산이라 빠르다. AI 기능(services/ai.py)은 이 결과를 근거로 받는다.
"""

from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from data_prep.nexon_api import split_spid
from data_prep.players import STATS, save_rate, season_short, stat_values

RECENT_DAYS = 45


# ── 선수 페이지 ─────────────────────────────────────────────────

def percentiles(player: dict, pool: list[dict]) -> dict[str, float]:
    """같은 포지션 그룹 선수들 사이에서 스탯마다 '상위 몇 %'인지 (0~100, 클수록 좋음). 비교 대상이 적으면 빈 dict."""
    mine = stat_values(player.get("ranker"))
    group = player.get("position_group")
    peers = [stat_values(p["ranker"]) for p in pool if p.get("ranker") and p.get("position_group") == group]
    if not mine or len(peers) < 5:
        return {}
    result = {}
    for key, _, _ in STATS:
        below = sum(1 for v in peers if v[key] < mine[key])
        same = sum(1 for v in peers if v[key] == mine[key])
        result[key] = round(100 * (below + 0.5 * same) / len(peers))
    return result


def position_usage(rows: list[dict]) -> list[dict]:
    """랭커가 이 카드를 어느 포지션에 쓰는지 — 최근 집계만, 경기 수가 많은 순."""
    if not rows:
        return []
    newest = max(r["date"] for r in rows)
    recent = [r for r in rows if r["date"] >= newest - timedelta(days=RECENT_DAYS)]
    recent.sort(key=lambda r: (-r["match_count"], -(r["goal"] + r["assist"])))
    return recent


# ── 스카우트 보드 ───────────────────────────────────────────────

def leaderboard(players: list[dict], group: str | None, kind: str, sort_key: str, min_matches: int = 5) -> list[dict]:
    """포지션 그룹 · 구분(현역/레전드)으로 거르고, 랭커 스탯 하나로 줄 세운다.

    선방률로 줄 세울 때는 선방 표본(상대 유효슈팅)이 충분한 골키퍼만 — 랭커 최소 경기 수 대신 그 기준을 쓴다.
    """
    rows = []
    for p in players:
        values = stat_values(p.get("ranker"))
        saves = save_rate(p)
        if not values or (sort_key == "save_rate" and saves is None):
            continue
        if sort_key != "save_rate" and p["ranker"]["match_count"] < min_matches:
            continue
        if group and p.get("position_group") != group:
            continue
        legend = "ICON" in p["season"]
        if (kind == "현역" and legend) or (kind == "레전드" and not legend):
            continue
        rows.append({"player": p, **values, "points": values["goal"] + values["assist"],
                     "match_count": p["ranker"]["match_count"], "save_rate": saves})
    rows.sort(key=lambda r: -r[sort_key])
    return rows


# ── 골키퍼 선방 재료 (경기 상세 1건 → 양 팀 골키퍼) ─────────────────────

def gk_games(detail: dict) -> list[dict]:
    """선방 = 상대 유효슈팅 − 실점. 랭커 API 에는 선방이 없어 경기 상세에서만 구할 수 있다.

    양 팀 선발 골키퍼(spPosition 0)를 모두 센다 — 내 골키퍼뿐 아니라 상대 골키퍼 카드의 표본도 쌓인다.
    몰수 · 기권 경기(선수 기록 없음)는 건너뛴다. 유저 식별자는 남기지 않는다.
    """
    info = detail.get("matchInfo") or []
    if len(info) != 2 or not detail.get("matchId"):
        return []
    rows = []
    for side, (team, other) in enumerate(((info[0], info[1]), (info[1], info[0]))):
        keeper = next((p for p in team.get("player") or [] if p.get("spPosition") == 0), None)
        if not keeper:
            continue
        shoot = other.get("shoot") or {}
        on_target = shoot.get("effectiveShootTotal", 0) or 0
        rows.append({"match_id": detail["matchId"], "side": side, "spid": keeper["spId"], "on_target": on_target,
                     "goals": min(shoot.get("goalTotal", 0) or 0, on_target),   # 자책골은 골키퍼가 막을 슈팅이 아니다
                     "match_date": detail.get("matchDate", "")[:10]})
    return rows


# ── 내 스쿼드 (최근 경기 선발) ─────────────────────────────────────

def latest_squad(nexon, catalog, ouid: str, matchtype: int = 50, look_back: int = 5) -> dict | None:
    """최근 경기의 내 선발 11명 — 포지션 · 강화 등급 · 평점. 넥슨 Open API 에 '현재 스쿼드' API 가 없어 경기 기록으로 대신한다.

    최근 경기부터 보며 선발이 11명인 첫 경기를 쓴다 (몰수 · 기권 경기는 선수 기록이 비어 있다).
    읽은 경기의 골키퍼 선방 재료(gk_games)도 함께 돌려준다 — 추가 호출 없이.
    """
    keepers = []
    for match_id in nexon.user_matches(ouid, matchtype, look_back):
        detail = nexon.match_detail(match_id)
        keepers += gk_games(detail)
        me = next((m for m in detail.get("matchInfo", []) if m.get("ouid") == ouid), None)
        starters = [p for p in (me or {}).get("player", []) if p.get("spPosition") != 28]
        if len(starters) >= 11:
            return {
                "match_date": detail.get("matchDate", "")[:16].replace("T", " "),
                "result": me.get("matchDetail", {}).get("matchResult", "-"),
                "players": [{
                    "spid": p["spId"],
                    "position": catalog.position_names.get(p["spPosition"], str(p["spPosition"])),
                    "grade": p.get("spGrade", 0),
                    "rating": (p.get("status") or {}).get("spRating", 0),
                } for p in starters],
                "gk_games": keepers,
            }
    return None


# ── 내 거래가 (계정 정보 조회 — 거래 기록) ─────────────────────────────
# 넥슨 Open API 에는 시세(시장가) API 가 없다. 대신 연결한 계정이 실제로 사고판 기록에 금액 · 강화 단계가 있다.

def trade_prices(trades: dict, spid: int) -> list[dict]:
    """이 카드의 내 거래 — 강화 단계별 가장 최근 거래가. [{grade, value, kind, date, count}]"""
    by_grade: dict[int, dict] = {}
    for kind, label in (("buy", "구매"), ("sell", "판매")):
        for t in trades.get(kind) or []:
            if t.get("spid") != spid:
                continue
            grade = t.get("grade", 0)
            row = {"grade": grade, "value": t.get("value", 0), "kind": label, "date": t.get("tradeDate", "")[:16].replace("T", " ")}
            prev = by_grade.get(grade)
            count = (prev["count"] if prev else 0) + 1
            if not prev or row["date"] > prev["date"]:
                by_grade[grade] = {**row, "count": count}
            else:
                prev["count"] = count
    return sorted(by_grade.values(), key=lambda r: r["grade"])


# ── 내 경기 분석 ────────────────────────────────────────────────

MY_STATS = ["goal", "assist", "shoot", "effective_shoot", "pass_rate", "dribble_rate", "tackle", "rating"]


def _accumulate(total: dict, status: dict) -> None:
    for key, src in (("goal", "goal"), ("assist", "assist"), ("shoot", "shoot"), ("effective_shoot", "effectiveShoot"),
                     ("pass_try", "passTry"), ("pass_success", "passSuccess"), ("dribble_try", "dribbleTry"),
                     ("dribble_success", "dribbleSuccess"), ("tackle", "tackle"), ("block", "block"),
                     ("rating", "spRating")):
        total[key] += status.get(src, 0) or 0


def _averages(total: dict, games: int) -> dict:
    avg = {k: total[k] / games for k in ("goal", "assist", "shoot", "effective_shoot", "tackle", "block", "rating")}
    avg["pass_rate"] = 100 * total["pass_success"] / total["pass_try"] if total["pass_try"] else 0.0
    avg["dribble_rate"] = 100 * total["dribble_success"] / total["dribble_try"] if total["dribble_try"] else 0.0
    return avg


def analyze_user(nexon, catalog, nickname: str, matchtype: int = 50, limit: int = 10, top: int = 11) -> dict | None:
    """닉네임 → 최근 경기 · 내가 많이 쓴 선수의 내 기록 vs 같은 카드 · 같은 포지션 랭커 평균. 없는 닉네임이면 None."""
    ouid = nexon.ouid(nickname.strip())
    if not ouid:
        return None
    basic = nexon.user_basic(ouid)
    best = {d["matchType"]: d for d in nexon.max_division(ouid)}
    match_ids = nexon.user_matches(ouid, matchtype, limit)
    with ThreadPoolExecutor(max_workers=4) as pool:
        details = list(pool.map(nexon.match_detail, match_ids))

    matches, usage = [], collections.defaultdict(lambda: collections.Counter())
    games = collections.Counter()
    ratings = []
    for detail in details:
        info = detail.get("matchInfo", [])
        me = next((m for m in info if m.get("ouid") == ouid), None)
        if not me:
            continue
        other = next((m for m in info if m.get("ouid") != ouid), {})
        matches.append({
            "date": detail.get("matchDate", "")[:10],
            "result": me.get("matchDetail", {}).get("matchResult", "-"),
            "score": f"{(me.get('shoot') or {}).get('goalTotal', 0)} : {(other.get('shoot') or {}).get('goalTotal', 0)}",
            "opponent": other.get("nickname", "-"),
        })
        if me.get("matchDetail", {}).get("averageRating"):
            ratings.append(me["matchDetail"]["averageRating"])
        opp_shoot = other.get("shoot") or {}
        for p in me.get("player", []):
            if p.get("spPosition") == 28:   # 교체 명단
                continue
            key = (p["spId"], p["spPosition"])
            games[key] += 1
            _accumulate(usage[key], p.get("status", {}))
            if p.get("spPosition") == 0:   # 골키퍼 선방률 재료 — 상대 유효슈팅 · 실점 (랭커 API 에는 없고 경기 기록에만 있다)
                usage[key]["opp_on_target"] += opp_shoot.get("effectiveShootTotal", 0) or 0
                usage[key]["opp_goals"] += opp_shoot.get("goalTotal", 0) or 0

    picked = sorted(games, key=lambda k: (-games[k], -usage[k]["rating"]))[:top]
    ranker = {}
    # 랭커 평균은 공식경기 · 감독모드에만 있다 — 친선경기는 공식경기 랭커 평균과 비교한다
    ranker_type = matchtype if matchtype in (50, 52) else 50
    if picked:
        for r in nexon.ranker_stats(list(picked), matchtype=ranker_type):
            s = r["status"]
            ranker[(r["spid"], r["spPosition"])] = _averages({
                "goal": s["goal"], "assist": s["assist"], "shoot": s["shoot"], "effective_shoot": s["effectiveShoot"],
                "pass_try": s["passTry"], "pass_success": s["passSuccess"], "dribble_try": s["dribbleTry"],
                "dribble_success": s["dribbleSuccess"], "tackle": s["tackle"], "block": s["block"], "rating": 0,
            }, 1) | {"match_count": s["matchCount"]}

    players = []
    for spid, position in picked:
        card = catalog.by_spid.get(spid, {"name": str(spid)})
        total = usage[(spid, position)]
        save = None
        if position == 0 and total["opp_on_target"]:
            save = {"rate": max(0.0, 100 * (1 - total["opp_goals"] / total["opp_on_target"])),
                    "on_target": total["opp_on_target"], "goals": total["opp_goals"]}
        players.append({
            "save": save,
            "spid": spid,
            "pid": split_spid(spid)[1],
            "name": card["name"],
            "season_short": season_short(catalog.season_name(spid)),
            "position": catalog.position_names.get(position, str(position)),
            "games": games[(spid, position)],
            "mine": _averages(usage[(spid, position)], games[(spid, position)]),
            "ranker": ranker.get((spid, position)),
        })

    record = collections.Counter(m["result"] for m in matches)
    division = best.get(matchtype)
    return {
        "nickname": basic.get("nickname", nickname),
        "level": basic.get("level"),
        "division": catalog.divisions.get(division["division"], str(division["division"])) if division else None,
        "division_date": division["achievementDate"][:10] if division else None,
        "matches": matches,
        "record": {"승": record.get("승", 0), "무": record.get("무", 0), "패": record.get("패", 0)},
        "avg_rating": sum(ratings) / len(ratings) if ratings else None,
        "players": players,
        "ranker_matchtype": ranker_type,
        "gk_games": [row for detail in details for row in gk_games(detail)],
        "fetched_at": date.today().isoformat(),
    }
