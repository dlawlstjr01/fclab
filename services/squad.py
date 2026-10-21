"""AI 추천 스쿼드 — 포메이션 · 모드(공식 · 친선 / 감독 모드) · 원하는 전술 · 꼭 넣을 선수 · 팀컬러 · 예산으로 11명을 고른다.

1) 후보 (자리마다, FC 온라인 데이터센터 — 하루 동안 SQLite 에 둔다)
   - 데일리 차트 '포지션별 이용 선수': 그 모드의 랭커(상위 1만 명)가 그 포지션에 많이 쓴 카드 16장 · 강화별 현재가
   - 팀컬러를 고르면: 그 팀컬러가 적용되는 카드 (선수 검색 teamcolorid — 클럽은 선수의 '클럽 경력' 기준), OVR 순.
     목표 단계(예: 4단계 = 11명)만큼 자리를 팀컬러 카드로 채운다 — 팀컬러 카드로 바꿔도 점수가 덜 떨어지는 자리부터
   - 꼭 넣을 선수: 이름 검색으로 시세 · 포지션을 받아 맞는 자리에 먼저 둔다
   강화 팀컬러(금빛 물결 등)는 카드가 아니라 강화 등급으로 맞춘다 — 목표 인원만큼 자리를 'N강 이상'으로만 고른다
2) 고르기 (코드 — 예산 셈을 LLM 에 맡기지 않는다): 예산 · 급여 한도 · 강화 범위 · 같은 선수는 한 번만.
   가장 싼 조합에서 시작해, 예산이 남는 동안 '오르는 점수 ÷ 더 드는 BP'가 가장 큰 바꿈을 하나씩 고른다.
   OVR 은 그 자리(포지션)에서의 OVR + 강화 — 카드의 포지션별 OVR 에 그 포지션이 없으면 대표 OVR 에서 5를 빼고 본다.
   점수 = 공식 · 친선: 랭커 이용 비율 55% + OVR 45% / 감독 모드: 이용 비율 35% + OVR 65%
   (공식 · 친선은 직접 조작해 랭커가 많이 쓰는 '체감' 좋은 카드가, 감독 모드는 AI 가 뛰어 능력치가 더 중요하다)
3) 설명 (LLM): 고른 11명이 모드 · 전술 · 팀컬러에 왜 맞는지와 운영 팁 — 근거 번호와 함께.
"""

from __future__ import annotations

from datetime import date

from data_prep.datacenter import USAGE_GROUP
from data_prep.enhance import GRADE_OVR
from data_prep.market import bp
from data_prep.players import GROUP_OF
from data_prep.prompts import squad_messages
from data_prep.tactics import FORMATIONS, ovr_at
from llm import reply_text

MODE_NAMES = {"official": "공식 · 친선 경기", "manager": "감독 모드"}
WEIGHTS = {"official": (0.55, 0.45), "manager": (0.35, 0.65)}   # (랭커 이용 비율, OVR)
PAY_CAP = 310   # 공식 스쿼드 메이커의 총 급여 한도 (넘으면 '초과')
OFF_POSITION = 5   # 카드의 포지션 목록에 없는 자리 — 대표 OVR 에서 이만큼 빼고 본다 (정확한 값은 카드 상세의 16칸)
SOURCE = "https://fconline.nexon.com/datacenter/dailysquad"


def _prices(card: dict) -> dict[int, int]:
    return {int(g): v for g, v in (card.get("prices") or {}).items() if v}   # SQLite(JSON)에서 오면 키가 문자열


def _pid(card: dict) -> int:
    return card["spid"] % 1_000_000


def slot_ovr(card: dict, code: str, grade: int) -> tuple[int, bool]:
    """그 자리에서의 OVR (강화 포함)과 정확한 값인지 — 선수 검색 카드는 포지션별 OVR, 데일리 차트 카드는 그 포지션의 OVR."""
    listed = card.get("positions") or ([(card["usage_position"], card.get("ovr"))] if card.get("usage_position") else [])
    value, exact = ovr_at(code, grade, card.get("position_ovr"), listed, card.get("ovr") or 100)
    return value, exact


def score(card: dict, grade: int, mode: str, code: str | None = None) -> float:
    share_weight, ovr_weight = WEIGHTS[mode]
    if code:
        ovr, exact = slot_ovr(card, code, grade)
        ovr -= 0 if exact else OFF_POSITION
    else:
        ovr = (card.get("ovr") or 100) + GRADE_OVR[grade]
    return share_weight * min((card.get("share") or 0) / 10, 1.5) + ovr_weight * (ovr - 100) / 30


def place(positions: list[str], free: list[str]) -> str | None:
    """카드의 포지션들 → 비어 있는 자리 하나 (같은 포지션 무리 → 같은 포지션 그룹 → 아무 필드 자리)."""
    groups = {USAGE_GROUP.get(p, p) for p in positions}
    for code in free:
        if USAGE_GROUP[code] in groups:
            return code
    wide = {GROUP_OF.get(p) for p in positions}
    for code in free:
        if GROUP_OF.get(code) in wide:
            return code
    if "GK" in positions:
        return "GK" if "GK" in free else None
    return next((c for c in free if c != "GK"), None)


def _start(options: dict[str, list[tuple]], fixed: dict[str, tuple], key) -> dict[str, tuple]:
    """시작 조합 — 후보가 적은 자리부터 key 가 가장 작은 카드 (같은 선수는 한 번만)."""
    chosen = dict(fixed)
    used = {_pid(o[0]) for o in fixed.values()}
    for code in sorted(options, key=lambda c: len(options[c])):
        pick = next((o for o in sorted(options[code], key=key) if _pid(o[0]) not in used), None)
        if pick:
            chosen[code] = pick
            used.add(_pid(pick[0]))
    return chosen


def optimize(options: dict[str, list[tuple]], fixed: dict[str, tuple], budget: int, pay_cap: int):
    """자리마다 고를 것 (카드, 강화, 시세, 점수) → 예산 · 급여 한도 안에서 점수 합이 큰 조합 (탐욕적 업그레이드).

    급여가 가장 낮은 조합에서 시작한다 (예산을 넘으면 가장 싼 조합에서). 바꿈마다 '남은 예산 · 남은 급여 중 더 많이
    쓰는 쪽의 비율' 대비 오르는 점수가 가장 큰 것을 고른다 — 급여 한도가 먼저 차서 예산을 못 쓰는 일을 줄인다.
    돌려주는 값: (고른 것 {자리: 옵션}, 총 시세, 총 급여). 가장 싼 조합도 예산을 넘으면 그 조합을 그대로 돌려준다.
    """
    chosen = _start(options, fixed, key=lambda o: (o[0].get("pay") or 0, o[2], -o[3]))
    if sum(o[2] for o in chosen.values()) > budget:
        chosen = _start(options, fixed, key=lambda o: (o[2], o[0].get("pay") or 0, -o[3]))
    used = {_pid(o[0]) for o in chosen.values()}
    total = sum(o[2] for o in chosen.values())
    pay = sum(o[0].get("pay") or 0 for o in chosen.values())
    if total > budget:
        return chosen, total, pay
    while True:
        best = None
        room_bp, room_pay = max(budget - total, 1), max(pay_cap - pay, 1)
        for code, opts in options.items():
            current = chosen.get(code)
            for o in opts:
                if current and (o is current or o[3] <= current[3]):
                    continue
                if _pid(o[0]) in used and not (current and _pid(o[0]) == _pid(current[0])):
                    continue
                cost = o[2] - (current[2] if current else 0)
                more_pay = (o[0].get("pay") or 0) - ((current[0].get("pay") or 0) if current else 0)
                if total + cost > budget or pay + more_pay > pay_cap:
                    continue
                gain = o[3] - (current[3] if current else 0)
                use = max(cost / room_bp, more_pay / room_pay, 1e-9)   # 남은 자원을 얼마나 쓰나 (더 빠듯한 쪽)
                value = gain / use
                if best is None or (value, gain) > (best[0], best[1]):
                    best = (value, gain, code, o, cost, more_pay)
        if best is None:
            break
        _, _, code, o, cost, more_pay = best
        if chosen.get(code):
            used.discard(_pid(chosen[code][0]))
        chosen[code] = o
        used.add(_pid(o[0]))
        total += cost
        pay += more_pay
    return chosen, total, pay


class SquadAdvisor:
    def __init__(self, client, db):
        self.client, self.db = client, db

    # ── 데이터센터 목록 (SQLite 에 둔다) — 팀컬러 목록 · 단계는 services/teamcolor.py ─────────────

    def usage(self, group: str, mode: str) -> list[dict]:
        key = f"usage:{mode}:{group}:{date.today().isoformat()}"   # 데일리 차트는 하루에 한 번 바뀐다
        rows = self.db.dc_get(key, 24)
        if rows is None:
            rows = self.client.position_usage(group, mode)
            self.db.dc_put(key, rows)
        return rows

    def team_cards(self, group: str, teamcolor_id: int) -> list[dict]:
        """그 팀컬러가 적용되는 그 포지션 무리의 카드 (OVR 순 60장) — 12시간 동안 다시 받지 않는다."""
        key = f"teamcards:{teamcolor_id}:{group}"
        rows = self.db.dc_get(key, 12)
        if rows is None:
            rows = self.client.player_list(positions=[group], teamcolor_id=teamcolor_id, order="ovr")[:60]
            self.db.dc_put(key, rows)
        return rows

    def card(self, spid: int, name: str) -> dict | None:
        """꼭 넣을 선수 — 이름 · 시즌으로 검색해 그 카드의 시세 · 포지션 · OVR · 급여."""
        rows = self.client.player_list(name.strip(), seasons=[spid // 1_000_000])
        self.db.put_listing(rows)
        return next((r for r in rows if r["spid"] == spid), None)

    # ── 추천 ────────────────────────────────────────────────────

    def recommend(self, req: dict, progress=lambda _message: None) -> dict:
        """req: formation, mode(official · manager), grades(낮은, 높은), budget(BP), pay_cap,
        teamcolor {id, name, kind, count(목표 인원), levels, min_grade(강화 팀컬러)} 또는 None, must [{spid, name, grade}].
        돌려주는 값: picks {자리: {card, grade, price, score}}, total, pay, team (팀컬러 인원 · 단계 · 효과), notes."""
        slots = FORMATIONS[req["formation"]]
        mode, (low, high) = req["mode"], req["grades"]
        teamcolor = req.get("teamcolor")
        notes: list[str] = []
        pools: dict[str, list[dict]] = {}
        for group in dict.fromkeys(USAGE_GROUP[c] for c in slots):
            progress(f"{group} 후보 — {MODE_NAMES[mode]} 랭커가 많이 쓰는 카드")
            used = {r["spid"]: r for r in self.usage(group, mode)}
            pool = [{**r, "team": False, "source": "랭커 이용", "usage_position": group} for r in used.values()]
            if teamcolor and not teamcolor.get("min_grade"):
                progress(f"{group} 후보 — 팀컬러 '{teamcolor['name']}' 카드 (클럽 경력 · 국적 기준)")
                eligible = self.team_cards(group, teamcolor["id"])
                ids = {r["spid"] for r in eligible}
                pool = [{**r, "share": used.get(r["spid"], {}).get("share", 0.0), "users": used.get(r["spid"], {}).get("users", 0),
                         "team": True, "source": "팀컬러"} for r in eligible] + [c for c in pool if c["spid"] not in ids]
            pools[group] = pool

        fixed: dict[str, tuple] = {}
        for must in req.get("must", []):
            progress(f"꼭 넣을 선수 — {must['name']}")
            card = self.card(must["spid"], must["name"])
            free = [c for c in slots if c not in fixed]
            if not card:
                notes.append(f"{must['name']}: 데이터센터에서 카드를 찾지 못해 넣지 못했습니다")
                continue
            code = place([p for p, _ in card.get("positions", [])], free)
            grade = must.get("grade") or low
            price = _prices(card).get(grade)
            if not code or price is None:
                notes.append(f"{must['name']}: 맞는 자리나 +{grade} 시세가 없어 넣지 못했습니다")
                continue
            eligible = any(c["spid"] == card["spid"] and c.get("team") for pool in pools.values() for c in pool)
            fixed[code] = ({**card, "team": eligible, "source": "꼭 넣을 선수"}, grade, price, score(card, grade, mode, code))

        options: dict[str, list[tuple]] = {}
        for code in slots:
            if code in fixed:
                continue
            opts = []
            for card in pools[USAGE_GROUP[code]]:
                for grade, price in _prices(card).items():
                    if low <= grade <= high:
                        opts.append((card, grade, price, score(card, grade, mode, code)))
            options[code] = opts
            if not opts:
                notes.append(f"{code}: 강화 +{low}~+{high} 안에 시세가 있는 후보가 없습니다")
        min_grade = (teamcolor or {}).get("min_grade")

        def in_team(o: tuple) -> bool:   # 강화 팀컬러는 등급으로, 나머지는 카드로
            return o[1] >= min_grade if min_grade else bool(o[0].get("team"))

        if teamcolor:   # 목표 인원만큼 자리를 팀컬러에 드는 것으로만 — 그렇게 바꿔도 점수가 덜 떨어지는 자리부터
            need = max(0, teamcolor["count"] - sum(1 for o in fixed.values() if in_team(o)))
            loss = {}
            for code, opts in options.items():
                team_best = max((o[3] for o in opts if in_team(o)), default=None)
                if team_best is not None:
                    loss[code] = max(o[3] for o in opts) - team_best
            for code in sorted(loss, key=loss.get)[:need]:
                options[code] = [o for o in options[code] if in_team(o)]
            if len(loss) < need:
                what = f"+{min_grade} 이상 시세가 있는" if min_grade else "팀컬러 카드가 있는"
                notes.append(f"{what} 자리가 {len(loss)}곳뿐이라 목표 {teamcolor['count']}명을 다 채우지 못할 수 있습니다")
            if min_grade and high < min_grade:
                notes.append(f"강화 범위(+{low}~+{high})가 '{teamcolor['name']}' 조건(+{min_grade} 이상)보다 낮습니다")
        budget, pay_cap = int(req["budget"]), int(req.get("pay_cap") or PAY_CAP)
        chosen, total, pay = optimize(options, fixed, budget, pay_cap)
        if total > budget:
            notes.insert(0, f"가장 싼 조합도 {bp(total)}라 예산 {bp(budget)}을 넘습니다 — 예산을 늘리거나 강화 범위를 낮춰 보세요")
        if pay > pay_cap:
            notes.append(f"총 급여 {pay}가 한도 {pay_cap}을 넘습니다")
        picks = {}
        for code, o in chosen.items():
            ovr, exact = slot_ovr(o[0], code, o[1])
            picks[code] = {"card": o[0], "grade": o[1], "price": o[2], "score": o[3], "ovr": ovr, "exact": exact,
                           "team": in_team(o)}
        team = None
        if teamcolor:
            members = sum(1 for p in picks.values() if p["team"])
            reached = [lv for lv in teamcolor.get("levels", []) if lv["count"] <= members]
            team = {"name": teamcolor["name"], "members": members, "target": teamcolor["count"],
                    "level": reached[-1]["level"] if reached else 0, "effects": reached[-1]["effects"] if reached else []}
        return {**req, "slots": slots, "picks": picks, "total": total, "pay": pay, "pay_cap": pay_cap, "team": team,
                "notes": notes}


def squad_evidence(result: dict) -> list[dict]:
    """[근거 1] 조건 · 고른 방법, [근거 2~] 자리마다 고른 카드 (OVR · 강화 · 시세 · 급여 · 랭커 이용 비율 · 팀컬러)."""
    reached = result.get("team")
    team = (f"{reached['name']} — 목표 {reached['target']}명, 결과 {reached['members']}명 · {reached['level']}단계"
            f"{' (' + ', '.join(reached['effects']) + ')' if reached['effects'] else ''}") if reached else "없음"
    must = ", ".join(m["name"] for m in result.get("must", [])) or "없음"
    evidence = [{
        "number": 1, "source_type": "datacenter", "source_label": "FC 온라인 데이터센터 데일리 차트 · 선수 검색",
        "title": "요청 조건과 고른 방법", "url": SOURCE,
        "text": (f"모드 {MODE_NAMES[result['mode']]} · 포메이션 {result['formation']} · 원하는 전술 {result.get('style') or '상관없음'}"
                 f"{' (' + result['wish'] + ')' if result.get('wish') else ''} · 팀컬러 {team} · 꼭 넣을 선수 {must}\n"
                 f"예산 {bp(result['budget'])} · 강화 +{result['grades'][0]}~+{result['grades'][1]} · 급여 한도 {result['pay_cap']}\n"
                 f"후보: 어제 데일리 차트에서 {MODE_NAMES[result['mode']]} 랭커(상위 1만 명)가 포지션마다 많이 쓴 카드"
                 f"{' + 팀컬러가 적용되는 카드(클럽 경력 · 국적 기준)' if reached else ''}. "
                 f"점수 = 랭커 이용 비율과 강화 OVR (모드별 비중). 예산 안에서 점수 합이 큰 조합.\n"
                 f"결과: 총 시세 {bp(result['total'])} · 총 급여 {result['pay']}/{result['pay_cap']}"),
    }]
    for code in result["slots"]:
        pick = result["picks"].get(code)
        if not pick:
            continue
        card, grade = pick["card"], pick["grade"]
        share = (f"{MODE_NAMES[result['mode']]} 랭커 {USAGE_GROUP[code]} 이용 {card.get('share') or 0:.1f}% ({card.get('users') or 0}명)"
                 if card.get("share") else "랭커 이용 상위 16장 밖")
        ovr = pick.get("ovr") or (card.get("ovr") or 0) + GRADE_OVR[grade]
        team = f" · 팀컬러 '{reached['name']}'에 듦" if reached and pick.get("team") else ""
        evidence.append({
            "number": len(evidence) + 1, "source_type": "datacenter", "source_label": "FC 온라인 데이터센터",
            "title": f"{code} 자리 — {card['name']} {card.get('season_code', '')} +{grade}", "url": SOURCE,
            "text": (f"{code} 자리 OVR {ovr}{'' if pick.get('exact', True) else ' (추정)'} (+1 대표 OVR {card.get('ovr') or '-'}) · "
                     f"시세 {bp(pick['price'])} · 급여 {card.get('pay') or '-'} · {share} · 후보: {card.get('source', '')}{team}"),
        })
    return evidence


def explain(result: dict, model) -> dict:
    """LLM 이 고른 스쿼드를 설명한다 (근거 번호와 함께). 근거 목록은 화면 표시에 필요한 것만 돌려준다."""
    evidence = squad_evidence(result)
    request = (f"모드 {MODE_NAMES[result['mode']]}, 포메이션 {result['formation']}, 원하는 전술 {result.get('style') or '상관없음'}"
               f"{' — ' + result['wish'] if result.get('wish') else ''}")
    answer = reply_text(model.invoke(squad_messages(request, evidence))).strip()
    return {"answer": answer, "evidence": [{k: e[k] for k in ("number", "source_type", "source_label", "title", "url")}
                                           for e in evidence]}
