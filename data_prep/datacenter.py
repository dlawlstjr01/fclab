"""FC 온라인 홈페이지 데이터센터 — 넥슨 Open API 에 없는 시세 · 능력치 · 특성 · 강화 부스트를 읽는다.

    POST /datacenter/PlayerList        선수 검색 — 카드마다 강화 1~13 단계 현재가 · 급여 · 포지션별 OVR
                                       (이름 하나로 그 선수의 카드 전부가 한 번에 온다 — 카드 1장에 13번 묻지 않는다)
    POST /datacenter/PlayerPriceGraph  카드 1장 · 강화 1단계의 일별 시세 (그래프)
    POST /datacenter/PlayerPreView     카드 1장의 상세 — 능력치 · 특성(일반/신규) · 클럽 경력 · 국적 · 급여
    POST /datacenter/PlayerAbility     카드 1장의 '선수 정보' 화면 — 능력치 34개 · 포지션별 OVR · 이 카드가 들어가는 팀컬러
    POST /datacenter/PlayerGrowCal     강화 확률 부스트 — 강화할 카드 + 소모 카드(최대 5장)
    GET  /Datacenter/BestUsePositionPlayer   데일리 차트 — 포지션마다 랭커(상위 1만 명)가 많이 쓴 카드 16장
                                             (공식 경기 1 ON 1 · 감독 모드 따로, 강화별 현재가 · 이용 비율)
    GET  /datacenter                   선수 검색 화면 — 클럽 · 국적 목록(검색값 번호)을 여기서 읽는다
    GET  /datacenter/teamcolor         팀컬러 801개 — 종류 · 최고 단계 · 최고 단계 효과
    GET  /datacenter/TeamColorDetail   팀컬러 1개의 단계별 조건(몇 명) · 능력치 증가
         (선수 검색의 teamcolorid 로 그 팀컬러가 적용되는 카드만 — 클럽은 '클럽 경력' 기준)

주의
- 넥슨 Open API 가 아니다. 홈페이지 화면용 응답(HTML)을 읽는 것이라 넥슨 약관을 확인해야 하고,
  공개 서비스로 배포할 때는 settings.toml 의 [market] enabled = false 로 끈다.
- 모든 카드를 미리 긁지 않는다. 사용자가 보는 카드 · 즐겨찾기 · 스쿼드만, 요청 간격을 지켜 한 번에 하나씩 받는다.
- 화면 구조가 바뀌면 아래 패턴(상수)만 고치면 된다. 못 읽으면 빈 값 — 앱은 '정보 없음'으로 계속 돈다.
"""

from __future__ import annotations

import html
import itertools
import re
import threading
import time
from datetime import date
from urllib import robotparser

import requests

from data_prep.market import MarketBlocked, MarketError, parse_price

SITE = "https://fconline.nexon.com"
LIST_PATH = "/datacenter/PlayerList"
PRICE_PATH = "/datacenter/PlayerPriceGraph"
PREVIEW_PATH = "/datacenter/PlayerPreView"
ABILITY_PATH = "/datacenter/PlayerAbility"
GROW_PATH = "/datacenter/PlayerGrowCal"
USAGE_PATH = "/Datacenter/BestUsePositionPlayer"
SEARCH_PAGE = "/datacenter"
TEAMCOLOR_PAGE = "/datacenter/teamcolor"
TEAMCOLOR_DETAIL = "/datacenter/TeamColorDetail"
MODES = {"official": 50, "manager": 52}   # 공식 경기 1 ON 1 · 감독 모드
# 데일리 차트의 포지션 탭 번호 (spposition) — 같은 무리의 포지션은 대표 포지션 하나로 묶여 있다
USAGE_POSITION = {"ST": 25, "CF": 21, "LW": 27, "RW": 23, "CM": 14, "CAM": 18, "CDM": 10, "LM": 16, "RM": 12,
                  "CB": 5, "LB": 7, "RB": 3, "LWB": 8, "RWB": 2, "GK": 0}
# 포메이션 자리(28개 포지션) → 데이터센터의 포지션 무리 (검색 · 데일리 차트가 쓰는 15개)
USAGE_GROUP = {"ST": "ST", "LS": "ST", "RS": "ST", "CF": "CF", "LF": "CF", "RF": "CF", "LW": "LW", "RW": "RW",
               "CM": "CM", "LCM": "CM", "RCM": "CM", "CAM": "CAM", "LAM": "CAM", "RAM": "CAM", "CDM": "CDM",
               "LDM": "CDM", "RDM": "CDM", "LM": "LM", "RM": "RM", "CB": "CB", "LCB": "CB", "RCB": "CB", "SW": "CB",
               "LB": "LB", "RB": "RB", "LWB": "LWB", "RWB": "RWB", "GK": "GK"}
USER_AGENT = "FCLab/0.1 (SSAFY AI course project; low volume, cached)"

# 선수 검색 폼 — 화면의 폼 그대로 모든 칸을 보낸다 (빈 칸이 빠지면 결과가 비어서 온다)
LIST_FIELDS = (
    "n8PlayerGrade1Min n8PlayerGrade1Max n1Confederation n4LeagueId strSeason strPosition strPhysical preferredfoot "
    "n1FootAblity n1SkillMove n1InterationalRep n4BirthMonth n4BirthDay n4TeamId n4NationId strAbility1 strAbility2 "
    "strAbility3 strTrait1 strTrait2 strTrait3 strTraitNon1 strTraitNon2 strTraitNon3 n1Strong n1Grow n1TeamColor "
    "strSkill1 strSkill2 strSkill3 strSkill4 strSearchStatus strOrderby teamcolorid strTeamColorCategory n1History "
    "n4PlayYear IsSummaryPlayer strPlayerName strTeamName strNationName strTeamColorName n4OvrMin n4OvrMax "
    "n4SalaryMin n4SalaryMax n1Ability1Min n1Ability1Max n1Ability2Min n1Ability2Max n1Ability3Min n1Ability3Max "
    "n4BirthYearMin n4BirthYearMax n4HeightMin n4HeightMax n4WeightMin n4WeightMax n4AvgPointMin n4AvgPointMax"
).split()
# 포지션 검색값 (spposition 번호) — 화면의 포지션 체크박스와 같다
POSITION_FILTER = {
    "ST": "24,25,26", "CF": "20,21,22", "LW": "27", "RW": "23", "CM": "13,14,15", "CAM": "17,18,19", "CDM": "9,10,11",
    "LM": "16", "RM": "12", "CB": "1,4,5,6", "RB": "3", "LB": "7", "RWB": "2", "LWB": "8", "GK": "0",
}
ORDER_BY = {"ovr": " overallrating descending", "price": " n8playergrade1 descending",
            "salary": " salary descending", "rating": " n4AvgAssessmentPoint descending"}

# 특성 — 데이터센터 '보유 특성' 검색 목록의 앞 17개가 신규 특성(2024~), 나머지가 일반 특성이다. 값은 아이콘 번호
# (trait_icon_{번호}.png — 그 특성을 가진 카드의 선수 정보 화면에서 읽었다). 스쿼드 메이커의 '보유 신규 특성' 고르기에 쓴다.
NEW_TRAIT_ICONS = {
    "크로스 포쳐": 51, "아크로바틱 피니셔": 50, "타이탄": 64, "트릭스터": 65, "레이저 슈터": 66, "프레데터": 67, "GK 데드아이": 68,
    "커맨더": 59, "스피드스터": 63, "파이터": 56, "라인 브레이커": 52, "블로커": 62, "와일드 태클러": 53, "2개의 심장": 55,
    "체이서": 54, "GK 빠른 반응": 57, "GK 공중볼 장악": 60,
}
NEW_TRAITS = frozenset(NEW_TRAIT_ICONS)

# 응답 HTML 에서 값을 찾는 패턴 — 화면이 바뀌면 여기만 고친다
ROW_SPLIT = re.compile(r'<div id="area_playerunit_(\d+)">')
NAME = re.compile(r'<div class="name">\s*([^<]+?)\s*</div>')
SEASON_IMG = re.compile(r"externalAssets/new/season/([A-Za-z0-9_]+)\.png")
POSITION_OVR = re.compile(r'<span class="position \w+"><span class="txt">(\w+)</span><span class="skillData_\d+[^"]*">(\d+)</span>')
PAY = re.compile(r'<span class="pay">\s*(\d+)\s*</span>')
OVR_AFTER_PAY = re.compile(r'<span class="pay">.*?<span class="skillData_\d+">\s*(\d+)', re.S)   # '123<em>(120)</em>' 도
GRADE_PRICE = re.compile(r'<span class="(?:price )?span_bp(\d+)"[^>]*title="([\d,]+)"')
RATING = re.compile(r'td_ar_score"><span>([\d.]+)\s*<em>\((\d+)\)</em>')
PREVIEW_OVR = re.compile(r'<div class="ovr value">\s*(\d+)\s*</div>')
PREVIEW_POSITION = re.compile(r'<div class="position">\s*(\w+)\s*</div>')
PREVIEW_PAY = re.compile(r'<div class="pay_side">\s*(\d+)\s*</div>')
ETC = re.compile(r'<span class="etc (\w+)">(.*?)</span>\s*(?=<span class="etc|</div>)', re.S)
CLUB = re.compile(r'<div class="etc team">.*?<span class="txt">\s*([^<]+?)\s*</span>', re.S)
NATION = re.compile(r'<div class="etc nation">.*?<span class="txt">\s*([^<]+?)\s*</span>', re.S)
TRAIT = re.compile(r'<img src="[^"]*/traits/trait_icon_(\d+)\.png" alt="([^"]+)"')
STAT = re.compile(r'<li class="ab"[^>]*>\s*<div class="txt">\s*([^<]+?)\s*</div>\s*<div class="value[^"]*">\s*(\d+)', re.S)
CLUB_HISTORY = re.compile(r'<div class="td year">\s*([^<]*?)\s*</div>\s*<div class="td club">\s*([^<]*?)\s*</div>\s*'
                          r'<div class="td rent">\s*([^<]*?)\s*</div>', re.S)
USAGE_ITEM = re.compile(r'item_list swiper-slide">(.*?)<p class="txt">\s*([\d,]+)명\(([\d.]+)%\)', re.S)
USAGE_CARD = re.compile(r"PlayerInfo\?spid=(\d+)&(?:amp;)?n1Strong=(\d+)")
USAGE_OVR = re.compile(r'<div class="ovr value">\s*(\d+)')
USAGE_POS = re.compile(r'<div class="position \w+">\s*(\w+)')
USAGE_PAY = re.compile(r'<div class="pay">.*?<span>\s*(\d+)\s*</span>', re.S)
USAGE_NAME = re.compile(r'<span class="name"><span>\s*([^<]+?)\s*</span>')
TEAM_OPTION = re.compile(r"SetAbilitySearch\('(\d+)', 'n4TeamId'\);[^>]*>(?:<[^>]+>)*([^<]+)")
NATION_OPTION = re.compile(r"SetAbilitySearch\('(\d+)', 'n4NationId'\);[^>]*>(?:<[^>]+>)*([^<]+)")
TEAMCOLOR_ITEM = re.compile(r'GetTeamColorDetail\((\d+)\).*?<div class="crests [^"]*">\s*<img src="([^"]*)".*?'
                            r'<div class="num">(\d+)</div>.*?<div class="name">([^<]+)</div>\s*<div class="level">([^<]*)</div>\s*'
                            r'<div class="desc">(.*?)</div>', re.S)
TEAMCOLOR_EFFECT = re.compile(r'<span class="item">\s*([^<]+?)\s*</span>')
TEAMCOLOR_LEVEL = re.compile(r'<div class="level lv[a-z]?(\d+)">.*?<div class="num">(\d+)</div>.*?<ul>(.*?)</ul>', re.S)
MIN_GRADE = re.compile(r"(\d+)강 이상")
YEAR_TEAM = re.compile(r"^(\d\d-\d\d|\d{4}) ")   # '24-25 첼시' · '2026 모로코'
# 선수 상세(PlayerAbility) — 데이터센터 '선수 정보' 화면의 본문: 능력치 · 특성 · 포지션별 OVR · 이 카드가 들어가는 팀컬러
ABILITY_TEAMCOLOR = re.compile(r'class="selector_item (tdefault|tspecial|tenhance|tupgrade)(\d+)"[^>]*>\s*([^<]+?)\s*</a>')
ABILITY_POSITION_OVR = re.compile(r'<div class="position (\w+) value">\s*(\d+)')
TEAMCOLOR_GROUP = {"tdefault": "소속", "tspecial": "관계", "tenhance": "강화", "tupgrade": "강화"}
# 선수 검색에서 고를 수 있는 능력치 칸 (strSkill1~4) — 4개씩 9번이면 36개 모두
STAT_GROUPS = ("스피드", "슛", "패스", "드리블", "수비", "피지컬")
ABILITY_GROUPS = {   # 데이터센터 선수 정보 화면의 세부 능력치 묶음 (34개)
    "스피드": ("속력", "가속력"),
    "슛": ("골 결정력", "슛 파워", "중거리 슛", "위치 선정", "발리슛", "페널티 킥"),
    "패스": ("짧은 패스", "시야", "크로스", "긴 패스", "프리킥", "커브"),
    "드리블": ("드리블", "볼 컨트롤", "민첩성", "밸런스", "반응 속도"),
    "수비": ("대인 수비", "태클", "가로채기", "헤더", "슬라이딩 태클"),
    "피지컬": ("몸싸움", "스태미너", "적극성", "점프", "침착성"),
    "골키퍼": ("GK 다이빙", "GK 핸들링", "GK 킥", "GK 반응속도", "GK 위치 선정"),
}
STAT_CODES = {
    "sprintspeed": "속력", "acceleration": "가속력", "finishing": "골 결정력", "shotpower": "슛 파워", "longshots": "중거리 슛",
    "positioning": "위치 선정", "volleys": "발리슛", "penalties": "페널티 킥", "shortpassing": "짧은 패스", "vision": "시야",
    "crossing": "크로스", "longpassing": "긴 패스", "freekickaccuracy": "프리킥", "curve": "커브", "dribbling": "드리블",
    "ballcontrol": "볼 컨트롤", "agility": "민첩성", "balance": "밸런스", "reactions": "반응 속도", "marking": "대인 수비",
    "standingtackle": "태클", "interceptions": "가로채기", "headingaccuracy": "헤더", "slidingtackle": "슬라이딩 태클",
    "strength": "몸싸움", "stamina": "스태미너", "aggression": "적극성", "jumping": "점프", "composure": "침착성",
    "gkdiving": "GK 다이빙", "gkhandling": "GK 핸들링", "gkkicking": "GK 킥", "gkreflexes": "GK 반응속도",
    "gkpositioning": "GK 위치 선정", "height": "키", "weight": "몸무게",
}
ROW_SKILLS = re.compile(r'<span class="pay">.*?(?=<div class="td td_ar_bp)', re.S)
SKILL_VALUE = re.compile(r'<span class="skillData_\d+"[^>]*>\s*(\d+)')
TEAMCOLOR_LI = re.compile(r"<li>\s*([^<]*?)\s*</li>")
TEAMCOLOR_TITLE = re.compile(r'<div class="tit"><strong>([^<]*?)\s*:\s*</strong><span>([^<]*)</span>')
BOOST = re.compile(r"강화 확률 부스트\s*<span>\s*([\d.]+)%")
BOOST_TOTAL = re.compile(r"소모 선수 가치 총합.*?([\d,]+)\s*BP", re.S)
TRAIT_ICON = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/traits/trait_icon_{no}.png"
FOOT_LIST = re.compile(r'<div class="foot">(.*?)</div>', re.S)
FOOT_ETC = re.compile(r'<span class="etc foot">(.*?)</span>', re.S)
FOOT_MAIN = re.compile(r"<strong>\s*([LR])")
FOOT_SIDES = re.compile(r"([LR])\s*(\d)")
NATION_FLAG = re.compile(r'<div class="etc nation">.*?<img src="([^"]+)"', re.S)
CLUB_CREST = re.compile(r'<div class="etc team">\s*<span class="img"><img src="([^"]+)"', re.S)


def parse_foot(fragment: str | None) -> dict | None:
    """'L5 – <strong>R3</strong>' → {"L": 5, "R": 3, "main": "R"} (굵은 쪽이 주발)."""
    if not fragment:
        return None
    sides = dict(FOOT_SIDES.findall(re.sub(r"<[^>]+>", " ", fragment)))
    if "L" not in sides or "R" not in sides:
        return None
    main = FOOT_MAIN.search(fragment)
    return {"L": int(sides["L"]), "R": int(sides["R"]), "main": main.group(1) if main else None}


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_player_list(text: str, skills: list[str] | tuple[str, ...] = ()) -> list[dict]:
    """선수 검색 응답 → 카드마다 {spid, name, season_code, positions[(포지션, OVR)], ovr, pay, prices{강화: BP}, rating,
    stats{능력치 코드: 값}}. stats 는 검색 때 고른 능력치 칸(skills, 최대 4개) — OVR 칸 다음에 그 순서로 온다."""
    parts = ROW_SPLIT.split(text)
    rows = []
    for spid, block in zip(parts[1::2], parts[2::2]):
        name, pay, ovr = NAME.search(block), PAY.search(block), OVR_AFTER_PAY.search(block)
        if not name:
            continue
        season = SEASON_IMG.search(block)
        rating = RATING.search(block)
        cells = ROW_SKILLS.search(block)
        values = [int(v) for v in SKILL_VALUE.findall(cells.group(0))] if cells else []
        foot = FOOT_LIST.search(block)
        rows.append({
            "foot": parse_foot(foot.group(1)) if foot else None,
            "stats": dict(zip(skills, values[1:])) if skills else {},
            "spid": int(spid),
            "name": html.unescape(name.group(1)),
            "season_code": season.group(1) if season else "",
            "positions": [(pos, int(value)) for pos, value in POSITION_OVR.findall(block)],
            "ovr": int(ovr.group(1)) if ovr else None,
            "pay": int(pay.group(1)) if pay else None,
            "prices": {int(g): int(v.replace(",", "")) for g, v in GRADE_PRICE.findall(block) if int(g) >= 1},
            "rating": (float(rating.group(1)), int(rating.group(2))) if rating else None,
        })
    return rows


def parse_preview(text: str) -> dict | None:
    """선수 상세 응답 → {ovr, position, positions, pay, body, skill_moves, foot, reputation, birth, height, weight,
    club, nation, traits[{name, icon, new}], summary{스피드 · 슛 · 패스 · 드리블 · 수비 · 피지컬}, stats{세부 능력치 34개},
    clubs[{years, club, loan}]}. OVR 을 못 찾으면 None. 요약 6개가 세부 능력치보다 먼저 온다 ('드리블'은 둘 다 있다)."""
    ovr = PREVIEW_OVR.search(text)
    if not ovr:
        return None
    etc = {key: _text(value) for key, value in ETC.findall(text)}
    pay, position, club, nation = PREVIEW_PAY.search(text), PREVIEW_POSITION.search(text), CLUB.search(text), NATION.search(text)
    foot, flag, crest = FOOT_ETC.search(text), NATION_FLAG.search(text), CLUB_CREST.search(text)
    values = [(html.unescape(k), int(v)) for k, v in STAT.findall(text)]
    lead = list(itertools.takewhile(lambda kv: kv[0] in STAT_GROUPS, values))[:len(STAT_GROUPS)]
    seen, traits = set(), []
    for no, name in TRAIT.findall(text):
        name = html.unescape(name)
        if name not in seen:
            seen.add(name)
            traits.append({"name": name, "icon": TRAIT_ICON.format(no=no), "new": name in NEW_TRAITS})
    return {
        "ovr": int(ovr.group(1)),
        "position": position.group(1) if position else "",
        "positions": [(pos, int(value)) for pos, value in POSITION_OVR.findall(text)],
        "pay": int(pay.group(1)) if pay else None,
        "body": etc.get("physical", ""),
        "skill_moves": etc.get("skill", "").count("★"),
        "foot": etc.get("foot", ""),
        "reputation": etc.get("season", ""),
        "birth": etc.get("birth", ""),
        "height": etc.get("height", ""),
        "weight": etc.get("weight", ""),
        "club": html.unescape(club.group(1)) if club else "",
        "nation": html.unescape(nation.group(1)) if nation else "",
        "foot_lr": parse_foot(foot.group(1)) if foot else None,
        "nation_flag": flag.group(1) if flag else "",
        "club_crest": crest.group(1) if crest else "",
        "traits": traits,
        "summary": dict(values[:len(lead)]),
        "stats": dict(values[len(lead):]),
        "clubs": [{"years": _text(y), "club": html.unescape(c), "loan": bool(_text(r))} for y, c, r in CLUB_HISTORY.findall(text)],
    }


def parse_usage(text: str) -> list[dict]:
    """데일리 차트 포지션별 이용 선수 → [{spid, grade, name, ovr, position, pay, prices{강화: BP}, users, share}]."""
    rows = []
    for block, users, share in USAGE_ITEM.findall(text):
        card, name = USAGE_CARD.search(block), USAGE_NAME.search(block)
        if not card or not name:
            continue
        ovr, position, pay = USAGE_OVR.search(block), USAGE_POS.search(block), USAGE_PAY.search(block)
        rows.append({
            "spid": int(card.group(1)), "grade": int(card.group(2)), "name": html.unescape(name.group(1)),
            "ovr": int(ovr.group(1)) if ovr else None, "position": position.group(1) if position else "",
            "pay": int(pay.group(1)) if pay else None,
            "prices": {int(g): int(v.replace(",", "")) for g, v in GRADE_PRICE.findall(block) if int(g) >= 1},
            "users": int(users.replace(",", "")), "share": float(share),
        })
    return rows


def parse_teams(text: str) -> dict[str, dict[str, int]]:
    """선수 검색 화면 → {"teams": {클럽 이름: 번호}, "nations": {국적: 번호}} (팀컬러 고르기 · 검색에 쓴다)."""
    return {"teams": {html.unescape(label).strip(): int(no) for no, label in TEAM_OPTION.findall(text)},
            "nations": {html.unescape(label).strip(): int(no) for no, label in NATION_OPTION.findall(text)}}


def parse_ability(text: str) -> dict | None:
    """선수 상세(능력치 화면) → preview 와 같은 항목 + position_ovr{포지션: OVR} · teamcolors{소속 · 관계 · 강화: [{id, name}]}."""
    info = parse_preview(text)
    if not info:
        return None
    teamcolors: dict[str, list[dict]] = {"소속": [], "관계": [], "강화": []}
    for kind, no, name in ABILITY_TEAMCOLOR.findall(text):
        if no != "0":   # '0' 은 '소속 팀컬러' 같은 머리말
            group = teamcolors[TEAMCOLOR_GROUP[kind]]
            if all(t["id"] != int(no) for t in group):
                group.append({"id": int(no), "name": html.unescape(name)})
    info["teamcolors"] = teamcolors
    info["position_ovr"] = {pos.upper(): int(v) for pos, v in ABILITY_POSITION_OVR.findall(text)}
    return info


def teamcolor_kind(teamcolor_id: int, max_count: int, name: str = "") -> str:
    """팀컬러 번호 · 인원 · 이름으로 종류 — 1xxx 클럽 · 2xxx 국가 (예전 번호), 1xxxx 강화 · 2xxxx 관계 · 3xxxx 스페셜.
    4xxxx 는 섞여 있다: 11명이면 클럽, '24-25 첼시' · '2026 모로코' 는 연도별, 6명 이하는 듀오 · 트리오 같은 관계,
    나머지는 'Ballon d'Or' 같은 시즌 묶음. (클럽 이름과 같은 4xxxx 는 services/teamcolor.py 가 클럽으로 고친다)"""
    text = str(teamcolor_id)
    if len(text) == 4:
        return "클럽" if text[0] == "1" else "국가"
    kind = {"1": "강화", "2": "관계", "3": "스페셜"}.get(text[0])
    if kind:
        return kind
    if max_count == 11:
        return "클럽"
    if YEAR_TEAM.match(name):
        return "연도별"
    return "관계" if max_count <= 6 else "시즌"


def parse_teamcolors(text: str) -> list[dict]:
    """팀컬러 목록 → [{id, name, kind, max_level, max_count, effects[], icon}]."""
    out = []
    for no, icon, count, name, level, desc in TEAMCOLOR_ITEM.findall(text):
        name = html.unescape(name).strip()
        out.append({"id": int(no), "name": name, "kind": teamcolor_kind(int(no), int(count), name),
                    "max_level": int(re.sub(r"\D", "", level) or 0), "max_count": int(count),
                    "effects": [html.unescape(e) for e in TEAMCOLOR_EFFECT.findall(desc)], "icon": icon})
    return out


def parse_teamcolor_detail(text: str) -> dict | None:
    """팀컬러 1개 → {name, description, levels: [{level, count, effects[]}]}. 단계를 못 찾으면 None."""
    levels = [{"level": i, "count": int(count),   # 단계 번호는 순서대로 ('동빛 물결'은 한 단계뿐인데 class 가 lvu3)
               "effects": [html.unescape(e) for e in TEAMCOLOR_LI.findall(body) if e.strip() not in ("", "-")]}
              for i, (_, count, body) in enumerate(TEAMCOLOR_LEVEL.findall(text), start=1)]
    if not levels:
        return None
    title = TEAMCOLOR_TITLE.search(text)
    description = html.unescape(title.group(2)) if title else ""
    min_grade = MIN_GRADE.search(description)   # 강화 팀컬러: '8강 이상의 선수들로 구성된 팀컬러입니다.'
    return {"name": html.unescape(title.group(1)) if title else "", "description": description, "levels": levels,
            "min_grade": int(min_grade.group(1)) if min_grade else None}


def parse_boost(text: str) -> dict | None:
    """강화 부스트 계산 응답 → {boost: 부스트 %, materials_bp: 소모 선수 가치 총합}. 못 읽으면 None."""
    boost = BOOST.search(text)
    if not boost:
        return None
    total = BOOST_TOTAL.search(text)
    return {"boost": float(boost.group(1)), "materials_bp": int(total.group(1).replace(",", "")) if total else None}


class DatacenterClient:
    """데이터센터 요청 — 스레드가 여럿이어도 요청은 한 줄로, interval 초 간격 (1초보다 짧게는 안 보낸다)."""

    def __init__(self, interval: float = 1.2, timeout: float = 15):
        self.interval = max(1.0, interval)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._lock = threading.Lock()
        self._last = 0.0
        self._robots: robotparser.RobotFileParser | None = None

    def allowed(self, path: str = PRICE_PATH) -> bool:
        """robots.txt 가 이 경로를 허용하나 — 한 번만 받아 둔다. 읽지 못하면 허용하지 않은 것으로 본다."""
        if self._robots is None:
            try:
                r = self.session.get(SITE + "/robots.txt", timeout=self.timeout)
            except requests.RequestException:
                return False   # 다음에 다시 확인
            parser = robotparser.RobotFileParser()
            parser.parse(r.text.splitlines() if r.ok else ["User-agent: *", "Disallow: /"])
            self._robots = parser
        return self._robots.can_fetch(USER_AGENT, SITE + path)

    def _post(self, path: str, data: dict, method: str = "POST") -> str:
        if not self.allowed(path):
            raise MarketBlocked("robots.txt 를 확인하지 못했거나 허용되지 않은 경로입니다.")
        error: Exception | None = None
        for attempt in range(2):
            with self._lock:
                wait = self._last + self.interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                try:
                    send = {"data": data} if method == "POST" else {"params": data}
                    r = self.session.request(method, SITE + path, timeout=self.timeout, **send,
                                             headers={"X-Requested-With": "XMLHttpRequest", "Referer": SITE + "/datacenter"})
                except requests.RequestException as exc:
                    r, error = None, exc
                finally:
                    self._last = time.monotonic()
            if r is not None and r.ok:
                return r.text
            error = error if r is None else MarketError(f"HTTP {r.status_code}")
            retry = r is None or r.status_code >= 500 or r.status_code == 429
            if not retry or attempt == 1:
                break
            time.sleep(3)
        raise MarketError(str(error))

    def player_list(self, name: str = "", *, seasons: list[int] | None = None, positions: list[str] | None = None,
                    team_id: int | None = None, nation_id: int | None = None, teamcolor_id: int | None = None,
                    order: str = "ovr", ovr_min: int | None = None, ovr_max: int | None = None,
                    price_max: int | None = None, skills: tuple[str, ...] = ()) -> list[dict]:
        """선수 검색 (한 번에 최대 200장). 이름만 주면 그 이름이 들어간 선수의 카드 전부. teamcolor_id 면 그 팀컬러가
        적용되는 카드만. skills 는 같이 받을 능력치 칸(STAT_CODES 중 최대 4개). price_max 는 +1 강화 가격 상한(BP)."""
        form = dict.fromkeys(LIST_FIELDS, "")
        form.update({"strPlayerName": name, "n4PageNo": "1", "strOrderby": ORDER_BY.get(order, "")})
        form.update({f"strSkill{i}": code for i, code in enumerate(skills[:4], start=1)})
        if teamcolor_id:
            form["teamcolorid"] = str(teamcolor_id)
        if ovr_max:
            form["n4OvrMax"] = str(ovr_max)
        if seasons:
            form["strSeason"] = "," + ",".join(str(s) for s in seasons) + ","
        if positions:
            form["strPosition"] = "," + ",".join(POSITION_FILTER[p] for p in positions) + ","
        if team_id:
            form["n4TeamId"] = str(team_id)
        if nation_id:
            form["n4NationId"] = str(nation_id)
        if ovr_min:
            form["n4OvrMin"] = str(ovr_min)
        if price_max:
            form["n8PlayerGrade1Max"] = str(price_max // 1000)   # 화면의 가격 범위는 1,000 BP 단위
        return parse_player_list(self._post(LIST_PATH, form), skills[:4])

    def price(self, spid: int, grade: int) -> dict | None:
        """카드 1장 · 강화 1단계의 현재가와 일별 시세."""
        return parse_price(self._post(PRICE_PATH, {"spid": spid, "n1strong": grade}), date.today())

    def preview(self, spid: int, grade: int = 1) -> dict | None:
        return parse_preview(self._post(PREVIEW_PATH, {"spid": spid, "n1strong": grade, "n1Grow": 0, "n1TeamColor": 0}))

    def ability(self, spid: int, grade: int = 1, adapt: int = 1, teamcolor: tuple[int, int] = (0, 0),
                enhance: tuple[int, int] = (0, 0), feature: int = 0) -> dict | None:
        """선수 상세(데이터센터 '선수 정보' 화면) — 능력치 · 특성 · 포지션별 OVR · 이 카드가 들어가는 팀컬러.

        화면의 추가 능력치 선택과 같다: 강화(grade) · 적응도(adapt 1~5) · 소속 팀컬러(번호, 단계) ·
        강화 팀컬러(번호, 단계) · 관계 팀컬러(번호). 돌려주는 능력치 · OVR 은 그것을 모두 더한 값이다.
        """
        return parse_ability(self._post(ABILITY_PATH, {
            "spid": spid, "n1Strong": grade, "n1Grow": max(0, adapt - 1), "n4TeamColorId": teamcolor[0],
            "n4TeamColorLv": teamcolor[1], "n4TeamColorId_Enhance": enhance[0], "n4TeamColorLv_Enhance": enhance[1],
            "n4TeamColorId_Feature": feature, "n1Change": 0, "strPlayerImg": ""}))   # n1Change=1 이면 +4 가 더 붙는다

    def position_usage(self, position: str, mode: str = "official") -> list[dict]:
        """그 포지션에서 랭커(상위 1만 명)가 많이 쓴 카드 — 어제 기준 데일리 차트, 공식 경기 · 감독 모드 따로."""
        return parse_usage(self._post(USAGE_PATH, {"strDate": "", "n4Position": USAGE_POSITION[position],
                                                   "n1Type": MODES[mode], "n4StartRanking": 1, "n4EndRanking": 10000,
                                                   "isGroup": "true"}, method="GET"))

    def teams(self) -> dict[str, dict[str, int]]:
        return parse_teams(self._post(SEARCH_PAGE, {}, method="GET"))

    def teamcolors(self) -> list[dict]:
        return parse_teamcolors(self._post(TEAMCOLOR_PAGE, {}, method="GET"))

    def teamcolor_detail(self, teamcolor_id: int) -> dict | None:
        return parse_teamcolor_detail(self._post(TEAMCOLOR_DETAIL, {"teamcolorid": teamcolor_id}, method="GET"))

    def boost(self, spid: int, grade: int, materials: list[tuple[int, int]]) -> dict | None:
        """강화 확률 부스트 % — 데이터센터 '강화 부스트 도우미'와 같은 계산 (게임과 0.1% 안쪽 차이)."""
        info = "," + "".join(f"{s}|{g}," for s, g in materials) if materials else ""
        return parse_boost(self._post(GROW_PATH, {"strCalinfo": info, "n8SpId": spid, "n1Strong": grade, "n4BoostPer": 0}))
