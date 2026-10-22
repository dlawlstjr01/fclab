"""짝 추천 — 이 카드를 자기 포지션에 쓸 때 옆에 누구를 두면 좋은가 (선수 상세).

- 짝의 자리: 가운데 자리(스트라이커 · 공격형 · 중앙 · 수비형 미드필더 · 센터백)는 같은 자리의 다른 한 명,
  측면(LW↔RW · LM↔RM · LB↔RB · LWB↔RWB)은 반대쪽. 골키퍼는 한 명이라 짝이 없다.
- 혼자 쓰는 자리인가: 데일리 차트의 랭커 포메이션 이용률로 — 그 자리가 한 명뿐인 포메이션(4-2-3-1 의 원톱 등)의 비율.
  60% 이상이면 '혼자 세워도 충분하다'고 적는다.
- 후보: 짝 자리에 랭커가 많이 쓴 카드 16장(데일리 차트). 같은 선수의 다른 카드는 뺀다.
  점수 = 랭커 이용 비율(60%) + 이 카드의 약한 능력치를 메워 주는 정도(40%, 모은 능력치 — 그 자리에 중요한 6개 중
  후보가 더 높은 것 두 개의 차이). 이유에 숫자를 그대로 적는다.
"""

from __future__ import annotations

from data_prep.datacenter import STAT_CODES, USAGE_GROUP
from data_prep.tactics import FORMATIONS

# 가운데 자리 묶음 — 포메이션 자리(28개) → 묶음
CENTRAL = {"ST": "스트라이커", "CF": "스트라이커", "CAM": "공격형 미드필더", "CM": "중앙 미드필더", "CDM": "수비형 미드필더",
           "CB": "센터백"}
MIRROR = {"LW": "RW", "RW": "LW", "LM": "RM", "RM": "LM", "LB": "RB", "RB": "LB", "LWB": "RWB", "RWB": "LWB"}
# 묶음 → 그 묶음의 데이터센터 포지션 무리 (USAGE_GROUP 의 값)
GROUP_POSITIONS = {"스트라이커": ("ST", "CF"), "공격형 미드필더": ("CAM",), "중앙 미드필더": ("CM",),
                   "수비형 미드필더": ("CDM",), "센터백": ("CB",)}
# 자리마다 짝을 볼 때 중요한 능력치 (STAT_CODES 의 키)
KEY_ABILITIES = {
    "스트라이커": ("sprintspeed", "finishing", "strength", "headingaccuracy", "shortpassing", "dribbling"),
    "공격형 미드필더": ("shortpassing", "vision", "dribbling", "finishing", "longshots", "sprintspeed"),
    "중앙 미드필더": ("shortpassing", "vision", "standingtackle", "stamina", "interceptions", "longshots"),
    "수비형 미드필더": ("standingtackle", "interceptions", "strength", "shortpassing", "stamina", "marking"),
    "센터백": ("sprintspeed", "standingtackle", "marking", "strength", "headingaccuracy", "shortpassing"),
    "측면 공격": ("sprintspeed", "dribbling", "crossing", "finishing", "shortpassing", "acceleration"),
    "측면 수비": ("sprintspeed", "crossing", "standingtackle", "stamina", "marking", "acceleration"),
}
SOLO_SHARE = 0.6
NAME_TO_CODE = {name: code for code, name in STAT_CODES.items()}


def pair_plan(position: str | None) -> dict | None:
    """{kind: 'central' | 'mirror', group(묶음 이름), partner(짝 자리의 데이터센터 포지션 무리), abilities} 또는 None(골키퍼)."""
    base = USAGE_GROUP.get(position or "")
    if not base or base == "GK":
        return None
    if base in MIRROR:
        wide = "측면 공격" if base in ("LW", "RW", "LM", "RM") else "측면 수비"
        return {"kind": "mirror", "group": wide, "partner": MIRROR[base], "abilities": KEY_ABILITIES[wide]}
    group = CENTRAL.get(base)
    if not group:
        return None
    return {"kind": "central", "group": group, "partner": base, "abilities": KEY_ABILITIES[group]}


def teamcolor_partners(card: dict, position: str | None, rows: list[dict], k: int = 6) -> list[dict]:
    """같은 팀컬러를 가진 선수 중의 짝 — rows = 그 팀컬러가 적용되는 카드(데이터센터 선수 검색). 선수마다 OVR 이 가장 높은
    카드 한 장, 이 선수(card = {spid, pid})는 뺀다. 짝 자리(반대쪽 측면 · 같은 줄의 다른 한 명)에 서는 카드가 먼저, 그다음
    다른 자리 — 같은 묶음 안에서는 OVR 높은 순. 돌려주는 값: [{spid, name, ovr, position, pair}] k 장."""
    plan = pair_plan(position)
    partner = set()
    if plan:
        partner = {plan["partner"]} if plan["kind"] == "mirror" else set(GROUP_POSITIONS[plan["group"]])
    best: dict[int, dict] = {}
    for r in rows:
        pid = r["spid"] % 1_000_000
        if pid != card["pid"] and (pid not in best or (r.get("ovr") or 0) > (best[pid].get("ovr") or 0)):
            best[pid] = r
    out = []
    for r in best.values():
        main = (r.get("positions") or [(None, None)])[0][0]   # 데이터센터 선수 검색의 첫 포지션 = 대표 포지션
        out.append({"spid": r["spid"], "name": r["name"], "ovr": r.get("ovr"), "position": main,
                    "pair": USAGE_GROUP.get(main or "") in partner})
    out.sort(key=lambda c: (not c["pair"], -(c["ovr"] or 0)))
    return out[:k]


def solo_share(group: str, formations: list[dict]) -> tuple[float | None, list[str]]:
    """그 묶음이 한 명뿐인 포메이션의 이용 비율 (그 묶음이 있는 포메이션 중) · 그런 포메이션 이름(이용 많은 순)."""
    members = set(GROUP_POSITIONS.get(group, ()))
    alone, total, names = 0.0, 0.0, []
    for row in formations:
        slots = [p for p in FORMATIONS.get(row["formation"], []) if USAGE_GROUP.get(p) in members]
        if not slots:
            continue
        total += row["share"]
        if len(slots) == 1:
            alone += row["share"]
            names.append(row["formation"])
    return (alone / total if total else None), names


def _abilities(spid: int, stats_db: dict, info: dict | None) -> dict[str, float]:
    """카드의 능력치 {코드: 값} — 모은 능력치(card_stats) 먼저, 없으면 카드 상세의 능력치 이름을 코드로."""
    have = dict((stats_db.get(spid) or {}).get("stats") or {})
    for name, value in ((info or {}).get("stats") or {}).items():
        code = NAME_TO_CODE.get(name)
        if code and code not in have:
            have[code] = value
    return have


def recommend(card: dict, info: dict | None, plan: dict, usage_rows: list[dict], stats_db: dict, k: int = 3) -> list[dict]:
    """짝 후보 [{spid, name, share, grade, score, gains[(능력치 이름, +차이)], reason}] — 점수 높은 순 k 장.
    card = {spid, pid}, usage_rows = 짝 자리의 데일리 이용 카드, stats_db = card_stats(후보 + 이 카드)."""
    mine = _abilities(card["spid"], stats_db, info)
    pool = [r for r in usage_rows if r["spid"] % 1_000_000 != card["pid"]]
    if not pool:
        return []
    top_share = max(r["share"] for r in pool) or 1.0
    scored = []
    for r in pool:
        theirs = _abilities(r["spid"], stats_db, None)
        gains = sorted(((theirs[c] - mine[c], c) for c in plan["abilities"] if c in theirs and c in mine and theirs[c] > mine[c]),
                       reverse=True)[:2]
        scored.append((r, gains, sum(g for g, _ in gains)))
    top_gain = max((g for _, _, g in scored), default=0) or 1.0
    out = []
    for r, gains, gain in scored:
        score = 0.6 * r["share"] / top_share + (0.4 * gain / top_gain if mine else 0)
        parts = [r.get("note") or f"랭커 {r['share']:.1f}% 사용"]
        if gains:
            parts.append(" · ".join(f"{STAT_CODES[c]} +{g:.0f}" for g, c in gains) + " 로 이 카드를 메워 줌")
        out.append({"spid": r["spid"], "name": r["name"], "share": r["share"], "grade": r.get("grade"), "score": score,
                    "gains": [(STAT_CODES[c], g) for g, c in gains], "reason": " · ".join(parts)})
    out.sort(key=lambda x: -x["score"])
    return out[:k]


def solo_text(name: str, group: str, share: float, formations: list[str]) -> str:
    """혼자 쓰는 자리일 때의 문구."""
    examples = " · ".join(formations[:3])
    return (f"🙆 <b>혼자서도 충분합니다</b> — 랭커들은 {group} 자리에 보통 한 명만 세웁니다 "
            f"(그런 포메이션 이용 {share * 100:.0f}% · {examples}). {name}은(는) 짝 없이 이 자리를 맡겨도 됩니다. "
            "두 명을 세우는 포메이션이라면 아래 짝을 참고하세요.")
