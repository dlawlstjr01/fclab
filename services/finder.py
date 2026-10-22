"""조건으로 카드 찾기 — 선수 찾기의 '상세 조건'과 홈의 '🤖 AI로 찾기'가 같이 쓴다. (F2011)

국적 · 리그 · 클럽(클럽 경력까지) · 팀컬러 · 포지션 · 시즌 · 보유 특성 · 주발 · 약발 · 개인기 · 명성 · OVR · 급여를
데이터센터 선수 검색 한 번으로 거른다 — 사실(국적 · 포지션 …)은 데이터가 정하고, AI(LLM)는 사람의 말을 이 조건으로
바꾸는 데만 쓴다. 예전처럼 LLM 이 후보마다 국적을 판단하면 '호나우지뉴 · 프랑스 국적' 같은 잘못이 생겼다.

- 레전드 = 아이콘(ICON · ICON TM · ICONS MATCH · TKI …) 카드가 있는 선수 — 그 선수의 모든 시즌 카드가 레전드 카드다
  (아이콘 시즌만으로 한정하지 않는다).
- 검색 한 번은 OVR 높은 순 200장까지 (데이터센터 한계). 같은 조건은 하루 동안 다시 묻지 않는다 (SQLite dc_cache).
- 능력치로 고르는 말('드리블이 좋은', '빠른')은 모은 능력치(card_stats)로 다시 줄 세운다.
- 수상 · 업적은 팀컬러로 찾는다 — '발롱도르' = Ballon d'Or 팀컬러, '트레블' = 트레블 팀컬러 8개 중 하나라도 ('팀컬러'라는
  말이 없어도). 데이터센터는 팀컬러 · 특성을 하나씩만 받아, '하나라도'는 따로 묻고 합친다. 쉼표로 적은 이름('손흥민, 케인')도.
- 시즌 팀컬러(발롱도르 · 주장 …)는 그 시즌 카드에만 붙는다 — 수상은 선수의 것이라, 그 카드가 있는 선수의 모든 시즌 카드로
  찾는다 (player_teamcolors). 카드마다의 조건(OVR · 급여 · 포지션 · 주발)은 모아 둔 능력치(card_stats)로 거른다.
- 리그는 지금 소속 리그라 은퇴한 레전드가 없다 — '세리에 A 레전드'는 그 리그 클럽마다 클럽 경력으로 묻는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field, replace

from data_prep.datacenter import FOOT, POSITION_FILTER, REPUTATION, STAT_CODES, fetch_search_options, search_cards
from data_prep.enhance import GRADE_OVR
from data_prep.nexon_api import split_spid
from data_prep.prompts import find_messages
from llm import parse_json, reply_text

log = logging.getLogger("fclab.finder")

OPTIONS_KEY = "search_options:v2"   # 선수 검색 화면의 목록 (국적 · 리그 · 클럽 · 특성) — 일주일
CACHE_HOURS = 24
PAGE_LIMIT = 200   # 데이터센터 선수 검색 한 번의 최대 장수
SPLIT_MAX = 8      # 쉼표로 적은 이름 · '하나라도' 특성을 따로 물을 수 상한
TC_UNION_MAX = 16  # '하나라도' 팀컬러(트레블 · 황금세대 …)를 따로 물을 수 상한

# 포지션 묶음 → 데이터센터 검색 포지션 (POSITION_FILTER 의 키)
GROUP_CODES = {
    "스트라이커": ("ST", "CF"), "윙어": ("LW", "RW", "LM", "RM"), "공격형 미드필더": ("CAM",), "중앙 미드필더": ("CM",),
    "수비형 미드필더": ("CDM",), "풀백": ("LB", "RB", "LWB", "RWB"), "센터백": ("CB",), "골키퍼": ("GK",),
    # 넓은 말
    "공격수": ("ST", "CF", "LW", "RW"), "미드필더": ("CAM", "CM", "CDM", "LM", "RM"), "수비수": ("CB", "LB", "RB", "LWB", "RWB"),
}
GROUP_WORDS = {   # 말 → 포지션 묶음 (긴 말부터 맞춘다 — '공격형 미드필더'가 '미드필더'보다 먼저)
    "공격형 미드필더": "공격형 미드필더", "공미": "공격형 미드필더", "CAM": "공격형 미드필더", "플레이메이커": "공격형 미드필더",
    "중앙 미드필더": "중앙 미드필더", "중미": "중앙 미드필더", "박스 투 박스": "중앙 미드필더",
    "수비형 미드필더": "수비형 미드필더", "수미": "수비형 미드필더", "CDM": "수비형 미드필더", "홀딩": "수비형 미드필더",
    "스트라이커": "스트라이커", "원톱": "스트라이커", "투톱": "스트라이커", "골잡이": "스트라이커", "센터 포워드": "스트라이커",
    "윙어": "윙어", "측면 공격수": "윙어", "윙포워드": "윙어", "풀백": "풀백", "윙백": "풀백", "측면 수비": "풀백",
    "센터백": "센터백", "중앙 수비": "센터백", "골키퍼": "골키퍼", "키퍼": "골키퍼",
    "공격수": "공격수", "미드필더": "미드필더", "수비수": "수비수",
}
CODE_WORDS = {code: code for code in POSITION_FILTER}   # 'ST' · 'LW' 처럼 포지션 이름을 그대로 적은 것
NATION_ALIASES = {"한국": "대한민국", "우리나라": "대한민국", "국내": "대한민국", "영국": "잉글랜드", "네덜란드": "네덜란드",
                  "코트디부아르": "코트디부아르"}
LEAGUE_ALIASES = {
    "프리미어리그": "잉글랜드 프리미어리그", "프리미어 리그": "잉글랜드 프리미어리그", "EPL": "잉글랜드 프리미어리그",
    "라리가": "라리가 EA스포츠", "분데스리가": "독일 분데스리가", "세리에 A": "세리에 A 에니라이브", "세리에A": "세리에 A 에니라이브",
    "세리에": "세리에 A 에니라이브", "리그 1": "리그 1 맥도날드", "리그앙": "리그 1 맥도날드", "리그1": "리그 1 맥도날드",
    "K리그": "대한민국 K리그 1", "케이리그": "대한민국 K리그 1", "MLS": "미국 MLS", "사우디": "로샨 사우디 리그",
    "에레디비시": "네덜란드 에레디비지", "에레디비지": "네덜란드 에레디비지", "챔피언스 리그": "UEFA 챔피언스 리그",
}
CLUB_ALIASES = {"맨유": "맨체스터 유나이티드", "맨시티": "맨체스터 시티", "레알": "레알 마드리드", "바르샤": "FC 바르셀로나",
                "바르셀로나": "FC 바르셀로나", "뮌헨": "바이에른 뮌헨", "바이언": "바이에른 뮌헨", "토트넘": "토트넘 홋스퍼",
                "스퍼스": "토트넘 홋스퍼", "아틀레티코": "아틀레티코 마드리드", "도르트문트": "보루시아 도르트문트",
                "돌문": "보루시아 도르트문트", "PSG": "파리 생제르맹", "파리": "파리 생제르맹",
                # FC 온라인의 가명 클럽 (이탈리아) — 실제 이름으로 찾아도 되게
                "인테르": "롬바르디아 FC", "인터 밀란": "롬바르디아 FC", "AC 밀란": "밀라노 FC", "AC밀란": "밀라노 FC", "밀란": "밀라노 FC",
                "라치오": "라티움", "아탈란타": "베르가모 칼초",
                # 줄여 부르는 이름 · 다르게 적는 이름
                "유베": "유벤투스", "나폴리": "SSC 나폴리", "로마": "AS 로마", "아스날": "아스널", "뉴캐슬": "뉴캐슬 유나이티드",
                "웨스트햄": "웨스트 햄 유나이티드", "울버햄튼": "울버햄프턴 원더러스", "브라이튼": "브라이턴 호브 앨비언",
                "레버쿠젠": "바이엘 04 레버쿠젠", "라이프치히": "RB 라이프치히", "프랑크푸르트": "아인트라흐트 프랑크푸르트",
                "세비야": "세비야 FC", "발렌시아": "발렌시아 CF", "비야레알": "비야레알 CF", "모나코": "AS 모나코",
                "리옹": "올랭피크 리옹", "마르세유": "올랭피크 마르세유"}
CLUB_NICKNAMES: dict[str, list[str]] = {}   # 클럽 → 부르는 이름 (고르기 칸에 같이 보인다 — 이름에 이미 든 말은 빼고)
for _alias, _real in CLUB_ALIASES.items():
    _seen = [a.replace(" ", "") for a in CLUB_NICKNAMES.get(_real, [])]
    if _alias.replace(" ", "") not in _real.replace(" ", "") and _alias.replace(" ", "") not in _seen:
        CLUB_NICKNAMES.setdefault(_real, []).append(_alias)
# 수상 · 업적을 말하는 말 → 팀컬러 이름 ('팀컬러'라는 말이 없어도 그 팀컬러로 찾는다). 여럿이면 그중 하나라도 든 카드
TEAMCOLOR_WORDS = {
    "발롱도르": ("Ballon d'Or",), "주장": ("Captain",), "캡틴": ("Captain",), "센추리 클럽": ("Century Club",),
    "A매치 100": ("Century Club",), "홈그로운": ("Home Grown",), "유스 출신": ("Home Grown",), "임대": ("Legend of the Loan",),
    "저니맨": ("Journeyman",), "베테랑": ("Veteran",), "자유 계약": ("Free Agent",), "이적료": ("Top Transfer",),
    "깜짝 이적": ("Unexpected Transfer",), "원더보이": ("Wonderboys",), "유망주": ("Ones To Watch", "Wonderboys"),
    "카타르 월드컵": ("World Cup 2022",), "2022 월드컵": ("World Cup 2022",), "월드컵 2022": ("World Cup 2022",),
    "유로 2024": ("UEFA EURO 2024",), "메달리스트": ("Medalist",), "챔스 우승": ("Champions of Europe",),
    "챔피언스 리그 우승": ("Champions of Europe",), "유럽 챔피언": ("Champions of Europe",), "역전": ("Dramatic Comebacks",),
    "준우승": ("Greatest Runner-Ups",), "연승": ("Winning Streak",), "등번호 7": ("Number 7",), "7번": ("Number 7",),
    "레전더리 넘버": ("Legendary Numbers",), "다관왕": ("Multi-League Champions",), "무패 우승": ("Untouchable Champions",),
}
# 이 말이 이름에 든 팀컬러 모두 — '트레블' = 네라주리 · 레드 데블스 · 바르샤(두 번) · 바이언(두 번) · 블루문 · 파리 생제르맹
TEAMCOLOR_THEMES = ("트레블", "황금세대", "갈락티코", "무패 우승", "연패")
NAMED_KINDS = ("관계", "스페셜", "연도별")   # 이름만 말해도 팀컬러로 읽는 종류 (클럽 · 국가는 클럽 · 국적 조건이 된다)
LEGEND_WORDS = ("레전드", "아이콘", "ICON", "은퇴")
# 능력치로 고르는 말 → 능력치 코드 (STAT_CODES 의 키)
ABILITY_WORDS = {
    "빠른": "sprintspeed", "스피드": "sprintspeed", "속력": "sprintspeed", "발 빠른": "sprintspeed", "가속": "acceleration",
    "골 결정력": "finishing", "결정력": "finishing", "골을": "finishing", "득점": "finishing", "슈팅": "finishing",
    "중거리": "longshots", "드리블": "dribbling", "볼 컨트롤": "ballcontrol", "탈압박": "ballcontrol", "짧은 패스": "shortpassing",
    "패스": "shortpassing", "킬패스": "vision", "어시스트": "vision", "시야": "vision", "크로스": "crossing", "긴 패스": "longpassing",
    "태클": "standingtackle", "수비력": "marking", "대인 수비": "marking", "가로채기": "interceptions", "헤더": "headingaccuracy",
    "공중볼": "headingaccuracy", "제공권": "headingaccuracy", "몸싸움": "strength", "피지컬": "strength", "체력": "stamina",
    "스태미너": "stamina", "민첩": "agility", "반응": "reactions", "선방": "gkreflexes", "다이빙": "gkdiving",
}
SORTS = {"ovr": "OVR 높은 순", "pay_low": "급여 낮은 순", "price": "현재가 높은 순"}


@dataclass(frozen=True)
class Filters:
    """찾을 조건 — 이름은 화면에 보이는 그대로(국적 '일본' · 리그 · 클럽 · 팀컬러 이름), 검색할 때 번호로 바꾼다."""
    name: str = ""                    # 쉼표로 여럿 ('손흥민, 케인') — 이름마다 따로 묻고 합친다
    seasons: tuple[int, ...] = ()
    positions: tuple[str, ...] = ()   # POSITION_FILTER 의 키
    position_label: str = ""          # '미드필더' 처럼 사람이 쓴 말
    nation: str | None = None
    league: str | None = None
    club: str | None = None
    club_history: bool = True         # 클럽은 클럽 경력까지 (데이터센터 '클럽 경력' 체크)
    teamcolor: tuple[int, str] | None = None
    teamcolor_any: tuple[tuple[int, str], ...] = ()    # 이 팀컬러 중 하나라도 (트레블 · 황금세대처럼 여럿) — 따로 묻고 합친다
    teamcolor_also: tuple[tuple[int, str], ...] = ()   # 이 팀컬러에도 드는 카드만 (국가 · 소속 · 특성 팀컬러를 함께 고를 때)
    player_teamcolors: tuple[tuple[int, str], ...] = ()   # 이 팀컬러 카드가 있는 선수의 모든 카드 (발롱도르 같은 시즌 팀컬러)
    traits: tuple[str, ...] = ()      # 모두 가진 (데이터센터 한 번에 셋까지)
    traits_any: tuple[str, ...] = ()  # 하나라도 가진 (OR) — 특성마다 따로 묻고 합친다
    foot: str | None = None           # '오른발' · '왼발'
    weak_foot: int | None = None      # 약발 1~5 (그 값만)
    skill_moves: int | None = None    # 개인기 별 1~6 (그 값만)
    reputation: str | None = None
    ovr_min: int | None = None        # OVR 범위는 grade(강화)의 OVR — 데이터센터에는 +1 OVR 로 바꿔 묻는다
    ovr_max: int | None = None
    pay_min: int | None = None
    pay_max: int | None = None
    height: tuple[int, int] | None = None   # (최소, 최대) cm
    weight: tuple[int, int] | None = None   # (최소, 최대) kg
    physical: tuple[str, ...] = ()          # 체형 (datacenter.PHYSICAL 의 이름 — 그중 하나)
    grade: int = 1
    legend: bool | None = None        # True 레전드만 · False 현역만
    ability: str | None = None        # 이 능력치 높은 순 (STAT_CODES 의 키)
    sort: str = "ovr"

    def empty(self) -> bool:
        return self == Filters(sort=self.sort, grade=self.grade)

    def chips(self, seasons: dict[int, str] | None = None) -> list[str]:
        """화면에 보일 조건 — ['국적 일본', '포지션 미드필더', …]."""
        out = []
        if self.name:
            out.append(f"이름 '{self.name}'")
        if self.nation:
            out.append(f"국적 {self.nation}")
        if self.league:
            out.append(f"리그 {self.league}" + (" (뛰었던 리그 — 클럽 경력)" if self.legend and not self.club else ""))
        if self.club:
            out.append(f"클럽 {self.club}" + (" (클럽 경력 포함)" if self.club_history else " (현 소속)"))
        if self.teamcolor:
            out.append(f"팀컬러 {self.teamcolor[1]}")
        if self.teamcolor_any:
            names = [n for _, n in self.teamcolor_any]
            more = f" 외 {len(names) - 4}개" if len(names) > 4 else ""
            out.append(f"팀컬러 {' · '.join(names[:4])}{more} 중 하나")
        if self.teamcolor_also:
            out.append("팀컬러 " + " + ".join(n for _, n in self.teamcolor_also))
        if self.player_teamcolors:
            out.append(" · ".join(n for _, n in self.player_teamcolors) + " 카드가 있는 선수 (모든 시즌 카드)")
        if self.positions:
            out.append(f"포지션 {self.position_label or '·'.join(self.positions)}")
        if self.seasons:
            names = [(seasons or {}).get(s, str(s)) for s in self.seasons]
            out.append("시즌 " + "·".join(dict.fromkeys(names)))
        if self.legend is not None:
            out.append("레전드만" if self.legend else "현역만")
        if self.traits:
            out.append("특성 " + "·".join(self.traits))
        if self.traits_any:
            out.append("특성 " + " · ".join(self.traits_any) + (" 중 하나" if len(self.traits_any) > 1 else ""))
        if self.foot:
            out.append(f"주발 {self.foot}")
        if self.weak_foot:
            out.append(f"약발 {self.weak_foot}")
        if self.skill_moves:
            out.append(f"개인기 {self.skill_moves}성")
        if self.reputation:
            out.append(f"명성 {self.reputation}")
        at = f" (+{self.grade}강)" if self.grade > 1 else ""
        for label, low, high, unit in (("OVR", self.ovr_min, self.ovr_max, at), ("급여", self.pay_min, self.pay_max, ""),
                                       ("키", *(self.height or (None, None)), "cm"),
                                       ("몸무게", *(self.weight or (None, None)), "kg")):
            if low and high:
                out.append(f"{label} {low}~{high}{unit}")
            elif low:
                out.append(f"{label} {low}{unit} 이상")
            elif high:
                out.append(f"{label} {high}{unit} 이하")
        if self.physical:
            out.append("체형 " + "·".join(self.physical))
        if self.grade > 1:
            out.append(f"+{self.grade}강 기준")
        out.append(f"{STAT_CODES[self.ability]} 높은 순" if self.ability else SORTS.get(self.sort, "OVR 높은 순"))
        return out

    def key(self) -> str:
        data = asdict(self)
        skip = ("sort", "ability", "position_label", "legend",   # 데이터센터에 묻지 않는 것 (받은 뒤에 거른다 · 줄 세운다)
                "teamcolor_any", "teamcolor_also", "traits_any", "grade", "player_teamcolors")   # 나눠 물을 때 이미 풀었다
        return "find:" + "|".join(f"{k}={data[k]}" for k in sorted(data) if k not in skip)


def _split(f: Filters) -> list[Filters]:
    """데이터센터에 한 번씩 물을 조건들 — 이름(쉼표) × 특성(하나라도) × 팀컬러(하나라도). 강화 OVR 범위는 +1 OVR 로."""
    names = [n.strip() for n in f.name.split(",") if n.strip()][:SPLIT_MAX] or [""]
    traits = [tuple(dict.fromkeys(f.traits + (t,)))[:3] for t in f.traits_any[:SPLIT_MAX]] or [f.traits]
    colors = list(f.teamcolor_any[:TC_UNION_MAX]) or [f.teamcolor]
    bonus = GRADE_OVR.get(f.grade, 0)
    base = replace(f, teamcolor_any=(), teamcolor_also=(), traits_any=(), grade=1,
                   ovr_min=max(1, f.ovr_min - bonus) if f.ovr_min else None,
                   ovr_max=max(1, f.ovr_max - bonus) if f.ovr_max else None)
    return [replace(base, name=n, traits=t, teamcolor=c) for n in names for t in traits for c in colors]


def club_label(name: str) -> str:
    """클럽 이름 + 부르는 이름 — '롬바르디아 FC (인테르 · 인터 밀란)'. 고르기 칸에서 실제 이름 · 줄임말을 쳐도 찾게
    (FC 온라인은 이탈리아 몇 클럽이 가명이다)."""
    nicknames = CLUB_NICKNAMES.get(name)
    return f"{name} ({' · '.join(nicknames)})" if nicknames else name


def teamcolor_matches(text: str, teamcolors: list[dict]) -> list[dict]:
    """말에 든 팀컬러 — 이름 그대로(관계 · 스페셜 · 연도별, 네 글자 이상)가 있으면 그것만, 없으면 수상 · 업적 말
    (TEAMCOLOR_WORDS)과 주제 말(TEAMCOLOR_THEMES — 이름에 그 말이 든 것 모두). 클럽 · 국가 팀컬러는 여기서 읽지 않는다."""
    plain = text.replace(" ", "").upper()
    named = [tc for tc in teamcolors if tc["kind"] in NAMED_KINDS and len(tc["name"].replace(" ", "")) >= 4
             and tc["name"].replace(" ", "").upper() in plain]
    if named:
        return named
    wanted = {name for word, names in TEAMCOLOR_WORDS.items() if word.replace(" ", "").upper() in plain for name in names}
    themes = [w.replace(" ", "") for w in TEAMCOLOR_THEMES if w.replace(" ", "") in plain]
    return [tc for tc in teamcolors if tc["name"] in wanted or any(w in tc["name"].replace(" ", "") for w in themes)]


def legend_pids(catalog) -> set[int]:
    """아이콘 카드가 있는 선수(pid) — 그 선수의 모든 카드를 레전드로 본다."""
    icons = {sid for sid, s in catalog.seasons.items() if "ICON" in s["className"].upper() or "TKI" in s["className"].upper()}
    return {pid for pid, spids in catalog.by_pid.items() if any(split_spid(s)[0] in icons for s in spids)}


def _longest(text: str, names, aliases: dict[str, str] | None = None) -> str | None:
    """글에 들어 있는 이름 중 가장 긴 것 (별칭은 원래 이름으로)."""
    plain = text.replace(" ", "").upper()
    found = [(len(n), n) for n in names if len(n) >= 2 and n.replace(" ", "").upper() in plain]
    found += [(len(a), real) for a, real in (aliases or {}).items() if a.replace(" ", "").upper() in plain and real in names]
    return max(found)[1] if found else None


def parse_rules(text: str, options: dict, teamcolors: list[dict], season_names: dict[int, str]) -> Filters:
    """말 → 조건 (규칙) — 국적 · 리그 · 클럽 · 팀컬러 · 포지션 · 시즌 · 레전드 · 특성 · 주발 · 약발 · 개인기 · OVR · 급여 · 능력치."""
    plain = text.replace(" ", "")
    nation = _longest(text, options.get("nations", {}), NATION_ALIASES)
    league = _longest(text, options.get("leagues", {}), LEAGUE_ALIASES)
    club = _longest(text, options.get("teams", {}), CLUB_ALIASES)
    if club and nation and club in nation:   # '대한민국' 안의 클럽 이름 같은 우연
        club = None
    teamcolor, teamcolor_any = None, ()
    if "팀컬러" in plain or "팀 컬러" in text:
        name = _longest(text, {tc["name"] for tc in teamcolors}, CLUB_ALIASES)
        tc = next((tc for tc in teamcolors if tc["name"] == name), None)
        if tc:
            teamcolor, club = (tc["id"], tc["name"]), None
    player_teamcolors = ()
    if teamcolor is None:   # 수상 · 업적 · 듀오 같은 팀컬러는 '팀컬러'라고 하지 않아도 ('발롱도르 받은 선수')
        matched = teamcolor_matches(text, teamcolors)
        player_teamcolors = tuple((tc["id"], tc["name"]) for tc in matched if tc["kind"] == "시즌")[:TC_UNION_MAX]
        found = [(tc["id"], tc["name"]) for tc in matched if tc["kind"] != "시즌"]
        if len(found) == 1:
            teamcolor = found[0]
        else:
            teamcolor_any = tuple(found[:TC_UNION_MAX])
    group = None
    for word in sorted(GROUP_WORDS, key=len, reverse=True):
        if word.replace(" ", "") in plain:
            group = GROUP_WORDS[word]
            break
    positions = GROUP_CODES.get(group, ())
    if not positions:
        codes = [c for c in CODE_WORDS if re.search(rf"(?<![A-Z]){c}(?![A-Z])", text.upper())]
        positions = tuple(codes)
        group = "·".join(codes) if codes else None
    seasons = tuple(sorted({sid for sid, name in season_names.items()
                            if len(name) >= 3 and re.search(rf"(?<![0-9A-Z]){re.escape(name.upper())}(?![0-9A-Z])", text.upper())}))
    legend = True if any(w.upper() in text.upper() for w in LEGEND_WORDS) else (False if "현역" in text else None)
    traits = tuple(t for t in options.get("traits", []) if t.replace(" ", "") in plain)[:3]
    foot = "왼발" if "왼발" in plain else "오른발" if "오른발" in plain else None
    weak = re.search(r"약발\s*(\d)", text)
    weak_foot = int(weak.group(1)) if weak else (5 if "양발" in plain else None)
    skill = re.search(r"개인기\s*(\d)\s*성|(\d)\s*성\s*개인기|(★{1,6})", text)
    skill_moves = None
    if skill:
        skill_moves = int(skill.group(1) or skill.group(2)) if (skill.group(1) or skill.group(2)) else len(skill.group(3))
    reputation = next((r for r in REPUTATION if r in plain), None)
    ovr = re.search(r"(?:OVR|오버롤)\s*(\d{2,3})", text, re.I)
    pay = re.search(r"급여\s*(\d{1,2})\s*(?:이하|까지|아래)", text)
    ability = None
    rest = plain   # 특성 이름('스피드 드리블러')의 말은 능력치로 읽지 않는다
    for trait in traits:
        rest = rest.replace(trait.replace(" ", ""), "")
    for word in sorted(ABILITY_WORDS, key=len, reverse=True):
        if word.replace(" ", "") in rest:
            ability = ABILITY_WORDS[word]
            break
    if group == "골키퍼" and ability not in ("gkreflexes", "gkdiving"):   # 골키퍼에게 '빠른' · '패스' 는 뜻이 다르다
        ability = None
    sort = "pay_low" if any(w in plain for w in ("가성비", "급여낮은", "저급여", "싼")) else "ovr"
    return Filters(seasons=seasons, positions=tuple(positions), position_label=group or "", nation=nation, league=league,
                   club=club, teamcolor=teamcolor, teamcolor_any=teamcolor_any, player_teamcolors=player_teamcolors,
                   traits=traits, foot=foot, weak_foot=weak_foot, skill_moves=skill_moves,
                   reputation=reputation, ovr_min=int(ovr.group(1)) if ovr else None,
                   pay_max=int(pay.group(1)) if pay else None, legend=legend, ability=ability, sort=sort)


def parse_with_ai(text: str, model, options: dict, teamcolors: list[dict], season_names: dict[int, str]) -> tuple[Filters, list[str]]:
    """말 → 조건 — 규칙으로 먼저 읽고, 규칙이 못 읽은 칸은 LLM 이 뽑은 이름으로 채운다 (목록에 있는 이름만 받는다).
    돌려주는 값: (조건, 읽지 못한 말 목록)."""
    found = parse_rules(text, options, teamcolors, season_names)
    if model is None:
        return found, []
    try:
        data = parse_json(reply_text(model.invoke(find_messages(text), temperature=0)), dict) or {}
    except Exception as exc:  # noqa: BLE001 — AI 가 안 되면 규칙으로 읽은 것만
        log.warning("조건 읽기(LLM) 실패: %s", exc)
        return found, []
    unknown, updates = [], {}

    def pick(value, names, aliases=None):
        if not value:
            return None
        name = _longest(str(value), names, aliases)
        if not name:   # '나폴리' → 'SSC 나폴리' 처럼 이름의 일부만 말했을 때 — 그 말이 든 이름이 하나뿐이면
            part = str(value).replace(" ", "").upper()
            inside = [n for n in names if len(part) >= 2 and part in n.replace(" ", "").upper()]
            name = inside[0] if len(inside) == 1 else None
        if not name:
            unknown.append(str(value))
        return name

    if not found.nation:
        updates["nation"] = pick(data.get("nation"), options.get("nations", {}), NATION_ALIASES)
    if not found.league:
        updates["league"] = pick(data.get("league"), options.get("leagues", {}), LEAGUE_ALIASES)
    if not found.club and not found.teamcolor:
        updates["club"] = pick(data.get("club"), options.get("teams", {}), CLUB_ALIASES)
    if not found.teamcolor and not found.teamcolor_any and not found.player_teamcolors and data.get("teamcolor"):
        said = str(data["teamcolor"])
        named = _longest(said, {tc["name"] for tc in teamcolors}, CLUB_ALIASES)
        matches = ([tc for tc in teamcolors if tc["name"] == named] if named else []) or teamcolor_matches(said, teamcolors)
        if matches and all(tc["kind"] == "시즌" for tc in matches):   # 수상 · 업적 — 선수 단위로
            updates["player_teamcolors"] = tuple((tc["id"], tc["name"]) for tc in matches[:TC_UNION_MAX])
        elif len(matches) == 1:
            updates["teamcolor"] = (matches[0]["id"], matches[0]["name"])
            if updates.get("club") == matches[0]["name"]:   # 클럽 팀컬러를 말했다 — 클럽 경력이 아니라 팀컬러로
                updates["club"] = None
        elif matches:
            updates["teamcolor_any"] = tuple((tc["id"], tc["name"]) for tc in matches[:TC_UNION_MAX])
        else:
            unknown.append(said)
    if not found.positions and data.get("position"):
        group = next((GROUP_WORDS[w] for w in sorted(GROUP_WORDS, key=len, reverse=True)
                      if w.replace(" ", "") in str(data["position"]).replace(" ", "")), None)
        if group:
            updates.update(positions=GROUP_CODES[group], position_label=group)
        else:
            unknown.append(str(data["position"]))
    if found.legend is None and isinstance(data.get("legend"), bool):
        updates["legend"] = data["legend"]
    if not found.traits and data.get("traits"):
        traits = [t for t in options.get("traits", []) for v in data["traits"] if str(v).replace(" ", "") == t.replace(" ", "")]
        updates["traits"] = tuple(dict.fromkeys(traits))[:3]
    if not found.ability and data.get("ability"):
        code = next((c for c, name in STAT_CODES.items() if name.replace(" ", "") == str(data["ability"]).replace(" ", "")), None)
        updates["ability"] = code or next((ABILITY_WORDS[w] for w in ABILITY_WORDS if w in str(data["ability"])), None)
    if not found.foot and data.get("foot") in FOOT:
        updates["foot"] = data["foot"]
    return replace(found, **{k: v for k, v in updates.items() if v not in (None, (), "")}), unknown


class CardFinder:
    """조건 → 카드 (데이터센터 선수 검색 · 하루 캐시). 화면이 기다리는 요청이라 데이터센터 창구에서 앞 순서다."""

    def __init__(self, client, db, catalog, teamcolors):
        self.client, self.db, self.catalog, self.teamcolors = client, db, catalog, teamcolors
        self._legends: set[int] | None = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def options(self) -> dict:
        data = self.db.dc_get(OPTIONS_KEY, 24 * 7)
        if data is None and self.client is not None:
            data = fetch_search_options(self.client)   # 모듈 함수 — 앱을 켠 채 코드가 바뀌어 예전 창구 객체여도 된다
            self.db.dc_put(OPTIONS_KEY, data)
        return data or {"teams": {}, "nations": {}, "leagues": {}, "team_league": {}, "traits": []}

    def legends(self) -> set[int]:
        if self._legends is None:
            self._legends = legend_pids(self.catalog)
        return self._legends

    def season_names(self) -> dict[int, str]:
        """시즌 번호 → 짧은 이름 ('26 TOTS' · 'ICON TM' …) — 말에서 시즌을 찾고 화면에 보일 때."""
        return {sid: s["className"].split(" (")[0].strip() for sid, s in self.catalog.seasons.items()}

    def parse(self, text: str, model=None) -> tuple[Filters, list[str]]:
        return parse_with_ai(text, model, self.options(), self.teamcolors.all(), self.season_names())

    def _ask(self, f: Filters) -> list[dict]:
        """데이터센터 선수 검색 한 번 (_split 으로 나눈 조건) — 같은 조건은 하루 동안 다시 묻지 않는다."""
        rows = self.db.dc_get(f.key(), CACHE_HOURS)
        if rows is None:
            options = self.options()
            rows = search_cards(
                self.client, f.name, seasons=list(f.seasons) or None, positions=list(f.positions) or None,
                nation_id=options["nations"].get(f.nation) if f.nation else None,
                league_id=options["leagues"].get(f.league) if f.league else None,
                team_id=options["teams"].get(f.club) if f.club else None, club_history=f.club_history,
                teamcolor_id=f.teamcolor[0] if f.teamcolor else None, traits=f.traits,
                foot=FOOT.get(f.foot) if f.foot else None, weak_foot=f.weak_foot, skill_moves=f.skill_moves,
                reputation=REPUTATION.get(f.reputation) if f.reputation else None, ovr_min=f.ovr_min,
                ovr_max=f.ovr_max, pay_min=f.pay_min, pay_max=f.pay_max, height=f.height, weight=f.weight,
                physical=f.physical)
            self.db.put_listing(rows)   # OVR · 급여 · 현재가가 바로 타일 · 줄에 보이게
            self.db.dc_put(f.key(), rows)
        return rows

    def _variants(self, f: Filters) -> list[Filters]:
        """레전드 + 리그 — 데이터센터의 리그는 지금 소속 리그라 은퇴한 레전드가 없다. 그 리그 클럽마다 클럽 경력으로 묻는다."""
        if f.legend and f.league and not f.club:
            options = self.options()
            league_id = options["leagues"].get(f.league)
            clubs = sorted(name for name, i in options["teams"].items()
                           if options["team_league"].get(str(i), options["team_league"].get(i)) == league_id)
            if clubs:
                return [replace(f, league=None, club=name, club_history=True) for name in clubs]
        return [f]

    def _merged(self, f: Filters) -> dict[int, dict]:
        """나눠 물은 결과를 합친다 (같은 카드는 한 번) — 리그 레전드 · 쉼표 이름 · '하나라도' 특성 · 팀컬러."""
        merged: dict[int, dict] = {}
        for variant in self._variants(f):
            for one in _split(variant):
                for r in self._ask(one):
                    merged.setdefault(r["spid"], r)
        return merged

    def _players_of(self, f: Filters) -> list[dict]:
        """선수 단위 팀컬러(발롱도르처럼 시즌 팀컬러) — 그 팀컬러 카드가 있는 선수의 모든 시즌 카드. 선수는 데이터센터에 그
        팀컬러로 묻고(국적 · 클럽 경력은 선수마다 같아 함께 묻는다), 그 선수들의 카드는 모아 둔 능력치(card_stats — OVR · 급여 ·
        포지션 · 주발 · 약발)로 거른다. 카드마다 다른 나머지 조건(특성 · 리그 · 체형 · 팀컬러 …)이 있으면 그 검색과 겹치는 카드만."""
        pids: set[int] = set()
        for tc in f.player_teamcolors:
            for r in self._ask(Filters(teamcolor=tc, nation=f.nation, club=f.club, club_history=f.club_history)):
                pids.add(r["spid"] % 1_000_000)
        spids = [spid for pid in sorted(pids) for spid in self.catalog.cards_of(pid)]
        known = self.db.card_stats(spids)
        bonus = GRADE_OVR.get(f.grade, 0)
        rows = []
        for spid in spids:
            card = known.get(spid)
            if not card or not card.get("ovr"):
                continue
            main = (card.get("positions") or [(None, None)])[0][0]
            foot = card.get("foot") or {}
            side = foot.get("main")
            weak = foot.get("R" if side == "L" else "L") if side else None
            checks = (not f.seasons or split_spid(spid)[0] in f.seasons, not f.positions or main in f.positions,
                      not f.ovr_min or card["ovr"] + bonus >= f.ovr_min, not f.ovr_max or card["ovr"] + bonus <= f.ovr_max,
                      not f.pay_min or (card.get("pay") or 0) >= f.pay_min, not f.pay_max or (card.get("pay") or 99) <= f.pay_max,
                      not f.foot or FOOT.get(f.foot) == {"R": 1, "L": 2}.get(side), not f.weak_foot or weak == f.weak_foot)
            if all(checks):
                rows.append({"spid": spid, "name": card.get("name"), "ovr": card["ovr"], "pay": card.get("pay"),
                             "positions": card.get("positions") or [], "foot": card.get("foot"), "prices": {}})
        per_card = (f.name or f.league or f.teamcolor or f.teamcolor_any or f.teamcolor_also or f.traits or f.traits_any
                    or f.skill_moves or f.reputation or f.physical or f.height or f.weight)
        if per_card:
            keep = set(self._merged(replace(f, player_teamcolors=())))
            rows = [r for r in rows if r["spid"] in keep]
        return sorted(rows, key=lambda r: -(r.get("ovr") or 0))

    def search(self, f: Filters) -> list[dict]:
        """조건에 맞는 카드 [{spid, name, ovr, pay, positions, prices, …}] — 레전드 · 능력치 · 정렬까지 적용한 순서.
        쉼표로 적은 이름 · '하나라도' 특성 · 팀컬러는 따로 물어 합치고(같은 카드는 한 번), teamcolor_also 는 그 팀컬러로
        물은 카드와 겹치는 것만 남긴다."""
        if f.player_teamcolors:
            rows = self._players_of(f)
        else:
            rows = sorted(self._merged(f).values(), key=lambda r: -(r.get("ovr") or 0))   # 나눠 물었으면 다시 OVR 높은 순으로
            for tc in f.teamcolor_also:
                keep = set(self._merged(replace(f, teamcolor=tc, teamcolor_any=(), teamcolor_also=())))
                rows = [r for r in rows if r["spid"] in keep]
        if f.legend is not None:
            legends = self.legends()
            rows = [r for r in rows if (split_spid(r["spid"])[1] in legends) == f.legend]
        if f.ability:
            stats = self.db.card_stats([r["spid"] for r in rows])
            rows = sorted(rows, key=lambda r: (-(stats.get(r["spid"], {}).get("stats", {}).get(f.ability) or 0),
                                               -(r.get("ovr") or 0)))
        elif f.sort == "pay_low":
            rows = sorted(rows, key=lambda r: (r.get("pay") or 99, -(r.get("ovr") or 0)))
        elif f.sort == "price":
            rows = sorted(rows, key=lambda r: -((r.get("prices") or {}).get(1) or (r.get("prices") or {}).get("1") or 0))
        return rows


MEMBER_SEASONS = 8   # 200장에서 잘렸을 때 따로 물을 시즌 수 (볼 카드가 많은 시즌부터)


def members(finder: CardFinder, f: Filters, seasons: dict[int, int]) -> dict:
    """조건에 맞는 카드 묶음 — 이미 있는 카드 목록(랭커 분석 · 데일리 기록)을 조건으로 거를 때.
    seasons = {시즌: 그 시즌에서 거를 카드 수}. 모든 시즌을 한 번에 묻고, 200장에서 잘렸으면 카드가 많은 시즌부터
    MEMBER_SEASONS 개를 따로 묻는다. 돌려주는 값: {"spids": 맞는 카드, "pids": 그 카드의 선수, "complete": 빠짐없이 봤나}.
    국적 · 클럽(경력) · 클럽/국가 팀컬러는 선수마다 정해지므로 pids 로 같은 선수의 다른 시즌 카드도 맞는다고 볼 수 있다."""
    found = finder.search(replace(f, seasons=tuple(sorted(seasons))))
    complete = len(found) < PAGE_LIMIT
    if not complete:
        order = sorted(seasons, key=lambda s: -seasons[s])
        for season in order[:MEMBER_SEASONS]:
            found = found + finder.search(replace(f, seasons=(season,)))
        complete = len(order) <= MEMBER_SEASONS
    return {"spids": {r["spid"] for r in found}, "pids": {r["spid"] % 1_000_000 for r in found}, "complete": complete}
