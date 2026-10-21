"""선수 항목 메타데이터 — 로드, 색인 텍스트 구성, 스탯 계산. (F2001)

항목 1건 = FC 온라인 선수 카드 1장 (26 TOTS 또는 ICON). 같은 사람이 두 시즌에 모두 있지는 않다.
"""

from __future__ import annotations

import json
from pathlib import Path

# 랭커가 쓴 포지션(spposition 의 desc)을 사람이 읽는 포지션 그룹으로 묶는다. 앞에 있는 것이 대표 포지션.
POSITION_GROUPS = {
    "스트라이커": ["ST", "CF", "RS", "LS", "RF", "LF"],
    "윙어": ["LW", "RW", "LM", "RM"],
    "공격형 미드필더": ["CAM", "RAM", "LAM"],
    "중앙 미드필더": ["CM", "RCM", "LCM"],
    "수비형 미드필더": ["CDM", "RDM", "LDM"],
    "풀백": ["LB", "RB", "LWB", "RWB"],
    "센터백": ["CB", "RCB", "LCB", "SW"],
    "골키퍼": ["GK"],
}
GROUP_OF = {pos: group for group, positions in POSITION_GROUPS.items() for pos in positions}

# 화면 · 프롬프트에 보이는 스탯 (키, 이름, 단위). 랭커 스탯은 모두 '경기당 평균'이다.
STATS = [
    ("goal", "골", ""),
    ("assist", "어시스트", ""),
    ("shoot", "슈팅", ""),
    ("effective_shoot", "유효슈팅", ""),
    ("pass_rate", "패스 성공률", "%"),
    ("dribble_rate", "드리블 성공률", "%"),
    ("tackle", "태클", ""),
    ("block", "블록", ""),
]


# 포지션 그룹마다 카드 아래에 보여 줄 대표 스탯 두 개. 골키퍼는 key_stat_line 이 따로 선방률을 보여 준다
# (아래 '골키퍼 선방률'). 여기 골키퍼 항목은 랭커 스탯 막대의 순서 · 조건 검색의 기본 스탯에만 쓴다.
KEY_STATS = {
    "스트라이커": ["goal", "assist"],
    "윙어": ["goal", "assist"],
    "공격형 미드필더": ["goal", "assist"],
    "중앙 미드필더": ["assist", "pass_rate"],
    "수비형 미드필더": ["tackle", "pass_rate"],
    "풀백": ["tackle", "assist"],
    "센터백": ["tackle", "block"],
    "골키퍼": ["pass_rate", "match_count"],
}
SHORT_NAMES = {"goal": "골", "assist": "도움", "shoot": "슈팅", "effective_shoot": "유효슈팅", "pass_rate": "패스",
               "dribble_rate": "드리블", "tackle": "태클", "block": "블록", "match_count": "랭커"}

# 포지션을 고르면 그 포지션에 맞는 정렬만 보여 준다.
SORT_STATS = {
    None: ["points", "goal", "assist", "tackle", "block", "pass_rate", "dribble_rate", "shoot", "effective_shoot", "match_count"],
    "스트라이커": ["goal", "effective_shoot", "shoot", "assist", "dribble_rate"],
    "윙어": ["points", "goal", "assist", "dribble_rate", "shoot", "pass_rate"],
    "공격형 미드필더": ["points", "assist", "goal", "pass_rate", "dribble_rate", "shoot"],
    "중앙 미드필더": ["pass_rate", "assist", "tackle", "dribble_rate", "goal"],
    "수비형 미드필더": ["tackle", "block", "pass_rate", "assist"],
    "풀백": ["tackle", "block", "assist", "pass_rate", "dribble_rate"],
    "센터백": ["tackle", "block", "pass_rate"],
    # 랭커 경기 수가 먼저 — 선방률은 연결한 계정이 불러온 경기에서만 모여, 기본 정렬로 쓰면 그 경기에 나온 골키퍼만 앞에 선다
    "골키퍼": ["match_count", "save_rate", "pass_rate"],
}
SORT_LABELS = {key: name for key, name, _ in STATS} | {"points": "공격 포인트(골+도움)", "match_count": "랭커 경기 수",
                                                       "save_rate": "선방률(경기 기록)"}

# ── 골키퍼 선방률 ───────────────────────────────────────────────
# 넥슨 랭커 API 에는 선방 항목이 없다 (골키퍼 카드도 블록 · 태클이 0). 경기 상세의 '상대 유효슈팅 · 실점'으로 계산한다.
# 화면이 SQLite 의 gk_games 집계(최근 30일)를 player["gk_saves"] = {games, on_target, goals} 로 붙여 준다.
SAVE_MIN_SHOTS = 10   # 상대 유효슈팅이 이보다 적으면 한두 골에 크게 흔들려 보여 주지 않는다


def save_rate(player: dict) -> float | None:
    """선방률(%) = 1 − 실점 / 상대 유효슈팅. 표본이 모자라면 None."""
    s = player.get("gk_saves")
    if not s or s["on_target"] < SAVE_MIN_SHOTS:
        return None
    return max(0.0, 100 * (1 - s["goals"] / s["on_target"]))


def keeper_line(player: dict) -> str | None:
    """골키퍼 카드 아래 한 줄 — 선방률이 있으면 선방률, 없으면 랭커 경기 수와 '선방 기록 부족'."""
    rate, s, ranker = save_rate(player), player.get("gk_saves"), player.get("ranker")
    if rate is not None:
        return f"선방 <b>{rate:.0f}%</b> · <b>{s['games']}경기</b> 기준"
    if not ranker:
        return None
    return f"랭커 <b>{ranker['match_count']}경기</b> · 선방 기록 부족"


def sort_value(player: dict, key: str) -> float:
    """정렬 값 — 기록이 없으면 맨 뒤로."""
    if key == "save_rate":
        rate = save_rate(player)
        return -1 if rate is None else rate
    values = stat_values(player.get("ranker"))
    if not values:
        return -1
    if key == "points":
        return values["goal"] + values["assist"]
    if key == "match_count":
        return player["ranker"]["match_count"]
    return values[key]


def key_stat_line(player: dict) -> str | None:
    """카드 아래 한 줄 — 포지션에 맞는 대표 스탯 두 개 (HTML). 랭커 기록이 없으면 None."""
    if player.get("position_group") == "골키퍼":
        return keeper_line(player)
    values = stat_values(player.get("ranker"))
    if not values:
        return None
    values["match_count"] = player["ranker"]["match_count"]
    parts = []
    for key in KEY_STATS.get(player.get("position_group") or "", ["goal", "assist"]):
        v = values[key]
        shown = f"{v:.0f}%" if key.endswith("_rate") else (f"{v}경기" if key == "match_count" else f"{v:.2f}")
        parts.append(f"{SHORT_NAMES[key]} <b>{shown}</b>")
    return " · ".join(parts)


def load_players(path: str | Path) -> list[dict]:
    """players.json 을 읽어 이름순으로 돌려준다. player_id 는 문자열(spid)이다."""
    players = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted(players, key=lambda p: (p["name"], p["season_id"]))


def player_label(player: dict) -> str:
    return f"{player['name']} · {player['season_short']}"


def season_short(class_name: str) -> str:
    """'26 TOTS (26 Team Of The Season)' → '26 TOTS'"""
    return class_name.split(" (")[0].strip()


def stat_values(ranker: dict | None) -> dict[str, float]:
    """랭커 스탯 원본에서 화면용 값(성공률 포함)을 만든다. 기록이 없으면 빈 dict."""
    if not ranker:
        return {}

    def rate(success: str, tries: str) -> float:
        return round(100 * ranker[success] / ranker[tries], 1) if ranker.get(tries) else 0.0

    return {
        "goal": ranker["goal"],
        "assist": ranker["assist"],
        "shoot": ranker["shoot"],
        "effective_shoot": ranker["effective_shoot"],
        "pass_rate": rate("pass_success", "pass_try"),
        "dribble_rate": rate("dribble_success", "dribble_try"),
        "tackle": ranker["tackle"],
        "block": ranker["block"],
    }


def stat_line(player: dict) -> str:
    ranker = player.get("ranker")
    if not ranker:
        return "랭커 경기 기록 없음"
    values = stat_values(ranker)
    parts = [f"{name} {values[key]:.2f}{unit}" if not unit else f"{name} {values[key]:.0f}{unit}"
             for key, name, unit in STATS]
    return (
        f"공식경기 {ranker['position']} 포지션, 최근 {ranker['match_count']}경기 평균 ({ranker['date']} 집계): "
        + " · ".join(parts)
    )


def index_text(player: dict) -> str:
    """선수 카드 1장 → 색인 텍스트 1개. (F2001)

    사용자가 선수를 찾을 때 쓰는 말을 모두 넣는다. 빠진 속성으로는 검색이 걸리지 않는다.
    - 위키백과 첫 문단(소개)이 있어야 "대한민국 출신 윙어", "프리미어리그 수비수" 같은 조건 검색이 된다.
    - 랭커 스탯이 있어야 "랭커들이 골을 많이 넣는 공격수" 같은 FC 온라인 관점의 조건 검색이 된다.
    """
    positions = ", ".join(player["positions"]) or "기록 없음"
    return "\n".join([
        f"선수: {player['name']}",
        f"FC 온라인 카드: {player['season']} (이 선수의 FC 온라인 카드는 모두 {player['card_count']}장)",
        f"구분: {'레전드(ICON)' if 'ICON' in player['season'] else '현역 시즌 베스트'}",
        f"랭커 주 포지션: {player['position_group'] or '기록 없음'} ({positions})",
        f"랭커 평균 기록: {stat_line(player)}",
        f"소개: {player.get('intro') or '정보 없음'}",
    ])


def meta_metadata(player: dict) -> dict:
    """메타데이터 청크에 함께 저장할 필드. Chroma 메타데이터는 문자열 · 숫자만 받는다."""
    return {
        "source_type": "player_meta",
        "player_id": player["player_id"],
        "name": player["name"],
        "title": player_label(player),
        "url": player.get("wiki_url") or "https://openapi.nexon.com/game/fconline/",
        "season": player["season_short"],
        "position_group": player["position_group"] or "",
    }


def player_brief(player: dict, intro_chars: int = 160) -> str:
    """프롬프트에 넣을 선수 한 줄 요약."""
    values = stat_values(player.get("ranker"))
    stats = f"경기당 골 {values['goal']:.2f}, 어시스트 {values['assist']:.2f}" if values else "랭커 기록 없음"
    intro = (player.get("intro") or "정보 없음")[:intro_chars]
    return (
        f"{player_label(player)} | 랭커 주 포지션: {player['position_group'] or '기록 없음'} | {stats} | 소개: {intro}"
    )
