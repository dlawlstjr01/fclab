"""조건으로 카드 찾기 — 선수 찾기의 '상세 조건'과 홈의 '🤖 AI로 찾기'가 같이 쓴다. (F2011)

국적 · 리그 · 클럽(클럽 경력까지) · 팀컬러 · 포지션 · 시즌 · 보유 특성 · 주발 · 약발 · 개인기 · 명성 · OVR · 급여를
데이터센터 선수 검색 한 번으로 거른다 — 사실(국적 · 포지션 …)은 데이터가 정하고, AI(LLM)는 사람의 말을 이 조건으로
바꾸는 데만 쓴다. 예전처럼 LLM 이 후보마다 국적을 판단하면 '호나우지뉴 · 프랑스 국적' 같은 잘못이 생겼다.

- 레전드 = 아이콘(ICON · ICON TM · ICONS MATCH · TKI …) 카드가 있는 선수 — 그 선수의 모든 시즌 카드가 레전드 카드다
  (아이콘 시즌만으로 한정하지 않는다).
- 검색 한 번은 OVR 높은 순 200장까지 (데이터센터 한계). 같은 조건은 하루 동안 다시 묻지 않는다 (SQLite dc_cache).
- 능력치로 고르는 말('드리블이 좋은', '빠른')은 모은 능력치(card_stats)로 다시 줄 세운다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field, replace

from data_prep.datacenter import FOOT, POSITION_FILTER, REPUTATION, STAT_CODES
from data_prep.nexon_api import split_spid
from data_prep.prompts import find_messages
from llm import parse_json, reply_text

log = logging.getLogger("fclab.finder")

OPTIONS_KEY = "search_options:v2"   # 선수 검색 화면의 목록 (국적 · 리그 · 클럽 · 특성) — 일주일
CACHE_HOURS = 24
PAGE_LIMIT = 200   # 데이터센터 선수 검색 한 번의 최대 장수

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
                "아틀레티코": "아틀레티코 마드리드", "도르트문트": "보루시아 도르트문트", "PSG": "파리 생제르맹", "파리": "파리 생제르맹",
                "인테르": "롬바르디아 FC", "인터 밀란": "롬바르디아 FC", "AC 밀란": "밀라노 FC", "AC밀란": "밀라노 FC"}
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
    name: str = ""
    seasons: tuple[int, ...] = ()
    positions: tuple[str, ...] = ()   # POSITION_FILTER 의 키
    position_label: str = ""          # '미드필더' 처럼 사람이 쓴 말
    nation: str | None = None
    league: str | None = None
    club: str | None = None
    club_history: bool = True         # 클럽은 클럽 경력까지 (데이터센터 '클럽 경력' 체크)
    teamcolor: tuple[int, str] | None = None
    traits: tuple[str, ...] = ()
    foot: str | None = None           # '오른발' · '왼발'
    weak_foot: int | None = None      # 약발 1~5 (그 값만)
    skill_moves: int | None = None    # 개인기 별 1~6 (그 값만)
    reputation: str | None = None
    ovr_min: int | None = None
    pay_max: int | None = None
    legend: bool | None = None        # True 레전드만 · False 현역만
    ability: str | None = None        # 이 능력치 높은 순 (STAT_CODES 의 키)
    sort: str = "ovr"

    def empty(self) -> bool:
        return self == Filters(sort=self.sort)

    def chips(self, seasons: dict[int, str] | None = None) -> list[str]:
        """화면에 보일 조건 — ['국적 일본', '포지션 미드필더', …]."""
        out = []
        if self.name:
            out.append(f"이름 '{self.name}'")
        if self.nation:
            out.append(f"국적 {self.nation}")
        if self.league:
            out.append(f"리그 {self.league}")
        if self.club:
            out.append(f"클럽 {self.club}" + (" (클럽 경력 포함)" if self.club_history else " (현 소속)"))
        if self.teamcolor:
            out.append(f"팀컬러 {self.teamcolor[1]}")
        if self.positions:
            out.append(f"포지션 {self.position_label or '·'.join(self.positions)}")
        if self.seasons:
            names = [(seasons or {}).get(s, str(s)) for s in self.seasons]
            out.append("시즌 " + "·".join(dict.fromkeys(names)))
        if self.legend is not None:
            out.append("레전드만" if self.legend else "현역만")
        if self.traits:
            out.append("특성 " + "·".join(self.traits))
        if self.foot:
            out.append(f"주발 {self.foot}")
        if self.weak_foot:
            out.append(f"약발 {self.weak_foot}")
        if self.skill_moves:
            out.append(f"개인기 {self.skill_moves}성")
        if self.reputation:
            out.append(f"명성 {self.reputation}")
        if self.ovr_min:
            out.append(f"OVR {self.ovr_min} 이상")
        if self.pay_max:
            out.append(f"급여 {self.pay_max} 이하")
        out.append(f"{STAT_CODES[self.ability]} 높은 순" if self.ability else SORTS.get(self.sort, "OVR 높은 순"))
        return out

    def key(self) -> str:
        data = asdict(self)
        skip = ("sort", "ability", "position_label", "legend")   # 데이터센터에 묻지 않는 것 (받은 뒤에 거른다 · 줄 세운다)
        return "find:" + "|".join(f"{k}={data[k]}" for k in sorted(data) if k not in skip)


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
    teamcolor = None
    if "팀컬러" in plain or "팀 컬러" in text:
        name = _longest(text, {tc["name"] for tc in teamcolors}, CLUB_ALIASES)
        tc = next((tc for tc in teamcolors if tc["name"] == name), None)
        if tc:
            teamcolor, club = (tc["id"], tc["name"]), None
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
                   club=club, teamcolor=teamcolor, traits=traits, foot=foot, weak_foot=weak_foot, skill_moves=skill_moves,
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
        if not name:
            unknown.append(str(value))
        return name

    if not found.nation:
        updates["nation"] = pick(data.get("nation"), options.get("nations", {}), NATION_ALIASES)
    if not found.league:
        updates["league"] = pick(data.get("league"), options.get("leagues", {}), LEAGUE_ALIASES)
    if not found.club and not found.teamcolor:
        updates["club"] = pick(data.get("club"), options.get("teams", {}), CLUB_ALIASES)
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
            data = self.client.search_options()
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

    def search(self, f: Filters) -> list[dict]:
        """조건에 맞는 카드 [{spid, name, ovr, pay, positions, prices, …}] — 레전드 · 능력치 · 정렬까지 적용한 순서."""
        rows = self.db.dc_get(f.key(), CACHE_HOURS)
        if rows is None:
            options = self.options()
            rows = self.client.player_list(
                f.name, seasons=list(f.seasons) or None, positions=list(f.positions) or None,
                nation_id=options["nations"].get(f.nation) if f.nation else None,
                league_id=options["leagues"].get(f.league) if f.league else None,
                team_id=options["teams"].get(f.club) if f.club else None, club_history=f.club_history,
                teamcolor_id=f.teamcolor[0] if f.teamcolor else None, traits=f.traits,
                foot=FOOT.get(f.foot) if f.foot else None, weak_foot=f.weak_foot, skill_moves=f.skill_moves,
                reputation=REPUTATION.get(f.reputation) if f.reputation else None, ovr_min=f.ovr_min, pay_max=f.pay_max)
            self.db.put_listing(rows)   # OVR · 급여 · 현재가가 바로 타일 · 줄에 보이게
            self.db.dc_put(f.key(), rows)
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
