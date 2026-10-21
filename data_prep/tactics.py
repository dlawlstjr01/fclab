"""FC 온라인 포메이션 · 전술 항목 — 도메인에 묶인 데이터. (NF2005)

⚠ 전술 항목 이름과 선택지는 게임 업데이트에 따라 바뀔 수 있다. 게임 화면과 다르면 이 파일만 고친다.
   AI 전술 추천은 여기 적힌 항목 · 선택지 안에서만 고르도록 프롬프트에 그대로 넣는다.
"""

from __future__ import annotations

import math

from data_prep.enhance import GRADE_OVR

# 포메이션 33개 — FC 온라인 공식 스쿼드 메이커의 포메이션 · 자리 배치 그대로 (4백 21 · 3백 7 · 5백 5).
# 자리마다 포지션:세로/가로 — 세로 0 = 상대 골문 쪽 공격 라인 ~ 115 = 골키퍼, 가로 0 = 왼쪽 ~ 100 = 오른쪽.
# 포지션은 공식 스쿼드 메이커가 자리 좌표로 정하는 방식(아래 포지션 구역)으로 읽었다.
# 3백의 양쪽 센터백만 바로잡았다 — 구역 계산으로는 RB · LB 로 나오지만 게임에서는 RCB · LCB 다.
_LAYOUTS = {
    # 4백
    "4-5-1": "GK:115/50 RCB:88/68 LCB:88/32 RB:85/91 LB:85/9 CM:55/50 RCM:47/68 LCM:47/32 RM:36/88 LM:36/12 ST:0/50",
    "4-4-2": "GK:115/50 RCB:88/65 LCB:88/35 RB:85/90 LB:85/10 RCM:43/65 LCM:43/35 RM:35/88 LM:35/13 RS:0/65 LS:0/34",
    "4-4-2(2)": "GK:115/50 RCB:88/68 LCB:88/32 RB:85/93 LB:85/7 RCM:47/68 LCM:47/32 RM:36/88 LM:36/12 CF:21/50 ST:0/50",
    "4-4-1-1": "GK:115/50 RCB:88/68 LCB:88/32 RB:85/91 LB:85/9 RCM:47/68 LCM:47/32 RM:36/88 LM:36/12 CAM:28/50 ST:0/50",
    "4-3-3": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/94 LB:85/6 RCM:53/73 LCM:53/27 CM:58/50 RW:4/78 LW:4/22 ST:0/50",
    "4-3-3(2)": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/94 LB:85/6 RCM:53/73 LCM:53/27 CM:58/50 RW:4/88 LW:4/12 ST:6/50",
    "4-3-2-1": "GK:115/50 RCB:100/69 LCB:100/31 RB:85/91 LB:85/11 RM:49/80 LM:49/20 CM:47/50 RAM:29/70 LAM:29/30 ST:0/50",
    "4-3-1-2": "GK:115/50 RCB:88/68 LCB:88/32 RB:85/91 LB:85/9 CM:57/50 LCM:44/35 RCM:44/65 CAM:30/50 RS:0/62 LS:0/38",
    "4-2-4": "GK:115/50 RCB:88/68 LCB:88/32 RB:85/91 LB:85/9 RCM:52/68 LCM:52/32 RW:23/87 LW:23/13 RS:0/68 LS:0/32",
    "4-2-3-1": "GK:115/50 RCB:100/68 LCB:100/32 RB:88/95 LB:88/4 RDM:72/66 LDM:72/34 CAM:36/50 RAM:32/78 LAM:32/22 ST:0/50",
    "4-2-2-2": "GK:115/50 RCB:100/63 LCB:100/38 RB:85/91 LB:85/11 RDM:58/63 LDM:58/38 RM:38/85 LM:38/15 RS:0/60 LS:0/40",
    "4-2-2-2(2)": "GK:115/50 RCB:100/63 LCB:100/38 RB:85/91 LB:85/11 RDM:68/63 LDM:68/38 RAM:31/75 LAM:34/25 RS:0/60 LS:0/40",
    "4-2-2-1-1": "GK:115/50 RCB:88/68 LCB:88/32 RB:85/91 LB:85/9 RDM:60/67 LDM:60/32 RM:36/88 LM:36/13 CAM:29/50 ST:0/50",
    "4-2-1-3": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/92 LB:85/8 RCM:53/69 LCM:53/32 CAM:40/50 RW:4/88 LW:4/12 ST:0/50",
    "4-1-4-1": "GK:115/50 RCB:90/63 LCB:90/36 RB:88/91 LB:88/9 CDM:65/50 RCM:47/64 LCM:47/36 RM:37/91 LM:37/9 ST:6/50",
    "4-1-3-2": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/91 LB:85/11 CDM:70/50 CM:43/50 RM:33/88 LM:33/12 RS:0/58 LS:0/42",
    "4-1-2-3": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/91 LB:85/11 CDM:65/50 RCM:53/69 LCM:53/32 ST:8/50 RW:4/88 LW:4/12",
    "4-1-1-1-3": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/91 LB:85/11 CDM:65/50 CM:50/50 CAM:30/50 LW:8/12 RW:8/88 ST:0/50",
    "4-1-2-3(2)": "GK:115/50 RCB:88/69 LCB:88/32 RB:85/91 LB:85/9 CDM:70/50 RCM:50/69 LCM:50/32 RW:10/90 LW:10/10 ST:12/50",
    "4-1-2-1-2": "GK:115/50 RCB:90/64 LCB:90/36 RB:88/91 LB:88/9 CDM:64/50 RM:40/91 LM:40/9 CAM:35/50 RS:0/65 LS:0/35",
    "4-1-2-1-2(2)": "GK:115/50 RCB:90/64 LCB:90/36 RB:88/91 LB:88/9 CDM:64/50 RCM:46/65 LCM:46/35 CAM:35/50 RS:0/65 LS:0/35",
    # 3백
    "3-4-3": "GK:115/50 RCB:100/75 LCB:100/26 CB:86/50 RCM:43/63 LCM:43/38 RM:40/88 LM:40/12 RW:6/90 LW:6/10 ST:6/50",
    "3-4-3(2)": "GK:115/50 RCB:100/75 LCB:100/26 CB:86/50 RCM:43/63 LCM:43/38 RM:40/100 LM:40/0 RW:12/88 LW:12/12 ST:0/50",
    "3-4-1-2": "GK:115/50 CB:86/50 RCB:90/73 LCB:90/26 RCM:51/64 LCM:51/37 RM:47/88 LM:47/12 CAM:28/50 RS:0/60 LS:0/40",
    "3-2-3-2": "GK:115/50 RCB:100/75 LCB:100/26 CB:86/50 RDM:66/65 LDM:66/35 CM:48/50 RM:35/90 LM:35/10 CF:15/50 ST:0/50",
    "3-2-2-1-2": "GK:115/50 RCB:100/75 LCB:100/25 CB:90/50 RDM:70/60 LDM:70/40 RM:59/90 LM:59/10 CAM:37/50 RS:0/60 LS:0/40",
    "3-1-2-1-3": "GK:115/50 RCB:100/75 LCB:100/25 CB:86/50 CDM:60/50 RM:52/90 LM:52/10 CAM:37/50 RW:2/90 LW:2/12 ST:0/50",
    "3-1-4-2": "GK:115/50 RCB:100/75 LCB:100/26 CB:86/50 CDM:60/50 RCM:49/63 LCM:49/38 RM:40/90 LM:40/10 RS:0/65 LS:0/35",
    # 5백
    "5-4-1": "GK:115/50 RCB:90/72 CB:90/50 LCB:90/28 RWB:71/88 LWB:71/12 RCM:50/70 LCM:50/30 RM:35/85 LM:35/15 ST:0/50",
    "5-3-2": "GK:115/50 RCB:93/72 CB:90/50 LCB:93/28 RWB:71/88 LWB:71/12 RCM:50/71 LCM:50/29 CM:52/50 RS:10/63 LS:10/37",
    "5-2-3": "GK:115/50 RCB:90/72 CB:90/50 LCB:90/28 RWB:71/88 LWB:71/12 RCM:50/65 LCM:50/35 RW:4/88 LW:4/12 ST:0/50",
    "5-2-1-2": "GK:115/50 RCB:90/72 CB:90/50 LCB:90/28 RWB:71/88 LWB:71/12 RCM:50/64 LCM:50/36 CAM:33/50 RS:10/63 LS:10/37",
    "5-1-2-1-1": "GK:115/50 RCB:92/68 LCB:92/32 CB:85/50 RWB:82/90 LWB:82/10 CDM:64/50 RM:35/90 LM:35/10 CAM:28/50 ST:0/50",
}
FORMATION_GROUPS = {"4백": list(_LAYOUTS)[:21], "3백": list(_LAYOUTS)[21:28], "5백": list(_LAYOUTS)[28:]}

# 포지션 구역 (가로, 세로, 너비, 높이) — 끌어 놓은 자리의 포지션을 정한다. 공식 스쿼드 메이커와 같은 구역 · 같은 규칙.
ZONES: dict[str, tuple[float, float, float, float]] = {
    "SW": (35, 80, 30, 20), "RWB": (75, 60, 20, 40), "RB": (75, 77, 20, 23), "RCB": (50, 80, 25, 20), "CB": (25, 80, 50, 20),
    "LCB": (25, 80, 25, 20), "LB": (5, 77, 20, 23), "LWB": (5, 60, 20, 40), "RDM": (50, 55, 25, 20), "CDM": (25, 55, 50, 25),
    "LDM": (25, 55, 25, 20), "RM": (75, 30, 20, 30), "RCM": (50, 40, 25, 20), "CM": (25, 40, 50, 20), "LCM": (25, 40, 25, 20),
    "LM": (5, 30, 20, 30), "RAM": (50, 25, 35, 20), "CAM": (25, 25, 50, 20), "LAM": (15, 25, 35, 20), "RF": (50, 15, 25, 20),
    "CF": (25, 10, 50, 20), "LF": (25, 15, 25, 20), "RW": (75, 0, 20, 35), "RS": (50, 0, 25, 15), "ST": (25, 0, 50, 15),
    "LS": (25, 0, 25, 15), "LW": (5, 0, 20, 35), "GK": (30, 105, 40, 15),
}
ZONE_PRIORITY = {"CB": 0, "RCB": 1, "LCB": 2, "SW": 3}   # 거리가 같을 때 먼저
# 목록 순서 — 골키퍼 → 수비 → 수비형 → 중앙 · 측면 → 공격형 → 공격, 같은 줄은 왼쪽부터
LINE = {"GK": 0, "LWB": 1, "LB": 1, "LCB": 1, "CB": 1, "SW": 1, "RCB": 1, "RB": 1, "RWB": 1, "LDM": 2, "CDM": 2, "RDM": 2,
        "LM": 3, "LCM": 3, "CM": 3, "RCM": 3, "RM": 3, "LAM": 4, "CAM": 4, "RAM": 4,
        "LW": 5, "LF": 5, "LS": 5, "CF": 5, "ST": 5, "RS": 5, "RF": 5, "RW": 5}

# 포메이션 → 자리별 (세로, 가로) · 11자리 포지션 (목록 순서)
LAYOUT: dict[str, dict[str, tuple[float, float]]] = {
    name: {code: (float(top), float(left)) for code, top, left in (s.replace(":", "/").split("/") for s in spec.split())}
    for name, spec in _LAYOUTS.items()
}
FORMATIONS: dict[str, list[str]] = {
    name: sorted(slots, key=lambda code, slots=slots: (LINE[code], slots[code][1])) for name, slots in LAYOUT.items()
}


def zone_center(code: str) -> tuple[float, float]:
    left, top, width, height = ZONES[code]
    return top + height / 2, left + width / 2


def position_at(top: float, left: float, occupied: set[str] = frozenset()) -> str:
    """경기장 위 한 점 → 포지션. 그 점을 품은 구역 중 중심이 가장 가까운 것, 품은 구역이 없으면 가장 가까운 구역.
    스위퍼(SW)는 센터백(CB)이 이미 있을 때만 — 공식 스쿼드 메이커와 같다."""
    codes = [c for c in ZONES if c != "SW" or "CB" in occupied]

    def distance(code: str) -> float:
        center_top, center_left = zone_center(code)
        return math.hypot(top - center_top, left - center_left)

    inside = [c for c in codes if ZONES[c][0] <= left <= ZONES[c][0] + ZONES[c][2]
              and ZONES[c][1] <= top <= ZONES[c][1] + ZONES[c][3]]
    return min(inside or codes, key=lambda c: (round(distance(c), 3), ZONE_PRIORITY.get(c, 9)))


def formation_of(codes: list[str], prefer: str | None = None) -> str | None:
    """11자리 포지션 → 같은 포지션으로 된 포메이션 (같은 구성이 둘이면 prefer 먼저). 없으면 None — 사용자 지정."""
    matches = [name for name, slots in FORMATIONS.items() if sorted(slots) == sorted(codes)]
    return prefer if prefer in matches else (matches[0] if matches else None)


def slot_xy(code: str, formation: str | None) -> tuple[float, float]:
    """포지션의 (세로, 가로) — 그 포메이션의 자리, 사용자 지정이면 그 포지션이 있는 첫 포메이션의 자리 또는 구역 가운데."""
    if formation in LAYOUT and code in LAYOUT[formation]:
        return LAYOUT[formation][code]
    return next((slots[code] for slots in LAYOUT.values() if code in slots), zone_center(code))


# 자리(28개 포지션) → 데이터센터 '포지션별 오버롤'의 16칸 (선수 정보 화면 · 스쿼드 메이커와 같다)
BASE_POSITION = {"ST": "ST", "LS": "ST", "RS": "ST", "CF": "CF", "LF": "CF", "RF": "CF", "LW": "LW", "RW": "RW",
                 "CAM": "CAM", "LAM": "CAM", "RAM": "CAM", "CM": "CM", "LCM": "CM", "RCM": "CM", "CDM": "CDM",
                 "LDM": "CDM", "RDM": "CDM", "LM": "LM", "RM": "RM", "CB": "CB", "LCB": "CB", "RCB": "CB", "SW": "SW",
                 "LB": "LB", "RB": "RB", "LWB": "LWB", "RWB": "RWB", "GK": "GK"}
OVR_GRID = ("ST", "LW", "CF", "RW", "CAM", "LM", "CM", "RM", "CDM", "LWB", "CB", "RWB", "LB", "SW", "RB", "GK")   # 화면 순서


def ovr_at(code: str, grade: int | None, grid: dict | None = None, listed=None, main: int | None = None) -> tuple[int | None, bool]:
    """그 자리에서 뛸 때의 OVR (강화 포함)과 정확한 값인지.

    grid: 선수 정보의 포지션별 OVR 16칸 (+1 기준) · listed: 선수 검색의 [(포지션, OVR)] (주 · 부 포지션만) ·
    main: 대표 OVR — 앞의 둘에 그 포지션이 없을 때 대신 쓰고, 정확하지 않다고 알린다.
    """
    bonus = GRADE_OVR[grade or 1]
    base = BASE_POSITION.get(code, code)
    if grid and base in grid:
        return grid[base] + bonus, True
    for position, value in listed or []:
        if position == base:
            return value + bonus, True
    return (main + bonus if main is not None else None), False

PLAY_STYLES = ["상관없음 (선수에 맞춰 추천)", "점유율 · 빌드업", "빠른 역습", "측면 공략 · 크로스", "중앙 침투 · 스루패스", "강한 전방 압박", "내려앉아 지키기"]

# 팀 전술 — (항목, 선택지 또는 범위, 설명). 설명은 AI 가 근거로 인용한다.
TEAM_TACTICS: list[tuple[str, str, str]] = [
    ("수비 스타일", "밸런스 / 공 소유자 압박 / 공 잃은 후 압박 / 지속적 압박 / 후퇴 수비",
     "공을 잃었을 때 어떻게 되찾을지. 압박 계열은 체력 소모가 크고, 후퇴 수비는 뒷공간을 줄인다."),
    ("수비 폭", "1~10", "작을수록 중앙을 좁게 막고, 클수록 측면까지 넓게 막는다."),
    ("수비 깊이", "1~10", "클수록 수비 라인을 올려 높은 위치에서 압박하고, 작을수록 내려서 뒷공간을 막는다."),
    ("빌드업 플레이", "밸런스 / 느린 빌드업 / 롱볼", "공격을 시작하는 방식. 느린 빌드업은 짧은 패스, 롱볼은 전방으로 길게."),
    ("찬스 메이킹", "밸런스 / 점유율 / 직선 패스 / 전방 침투", "골 기회를 만드는 방식. 전방 침투는 공격수의 뒷공간 침투가 많아진다."),
    ("공격 폭", "1~10", "클수록 공격 시 측면을 넓게 쓰고, 작을수록 중앙에 모인다."),
    ("박스 안 선수 수", "1~10", "크로스 · 컷백 때 페널티 박스 안으로 들어가는 선수 수."),
    ("코너킥 · 프리킥 인원", "1~5", "세트피스 때 박스 안으로 들어가는 선수 수."),
]

# 포지션 그룹별 개인 전술 — (항목, 선택지)
PLAYER_INSTRUCTIONS: dict[str, list[tuple[str, str]]] = {
    "스트라이커": [("공격 시", "침투 / 연계 플레이 / 타겟맨"), ("수비 시", "수비 가담 / 공격 대기")],
    "윙어": [("공격 시", "측면 유지 / 중앙 침투"), ("수비 시", "수비 가담 / 공격 대기")],
    "공격형 미드필더": [("공격 시", "자유로운 움직임 / 침투 / 자리 지키기"), ("수비 시", "수비 가담 / 공격 대기")],
    "중앙 미드필더": [("공격 시", "공격 가담 / 밸런스 / 자리 지키기"), ("수비 시", "수비 위치 유지 / 압박")],
    "수비형 미드필더": [("공격 시", "자리 지키기 / 공격 가담"), ("수비 시", "센터백 보호 / 전진 압박")],
    "풀백": [("공격 시", "오버래핑 / 수비 위치 유지 / 언더래핑"), ("수비 시", "수비 위치 유지")],
    "센터백": [("공격 시", "수비 위치 유지 / 세트피스 가담"), ("수비 시", "라인 유지 / 적극적 견제")],
    "골키퍼": [("빌드업", "짧은 배급 / 긴 배급"), ("스위퍼 키퍼", "일반 / 전진")],
}


def tactics_guide() -> str:
    """AI 전술 추천에 [근거] 로 넣는 전술 항목 설명."""
    team = "\n".join(f"- {name} ({options}): {desc}" for name, options, desc in TEAM_TACTICS)
    personal = "\n".join(f"- {group}: " + " · ".join(f"{k}({v})" for k, v in items)
                         for group, items in PLAYER_INSTRUCTIONS.items())
    return f"[팀 전술 항목]\n{team}\n\n[포지션별 개인 전술 항목]\n{personal}"
