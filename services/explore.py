"""조건 기반 항목 탐색 — "이런 선수 찾아줘". (F2011)

    자연어 조건 → 후보 = ① 파워 랭킹 상위(조건의 포지션 · 스탯으로) + ② 메타데이터 색인 의미 검색 상위
              → LLM 이 후보 정보(랭킹 순위 포함)만 보고 조건에 맞는 선수만 고름 → 스탯이 있으면 랭킹 순으로

의미 검색만 쓰면 "골을 많이 넣는 윙어"처럼 순위가 중요한 조건에서 정작 랭킹 1위가 후보에 없을 수 있다.
그래서 조건에서 포지션 · 스탯 · 현역/레전드를 읽어 파워 랭킹 상위를 후보에 함께 넣는다.
"""

from __future__ import annotations

import math
import random
import re

from data_prep.players import KEY_STATS, STATS, stat_values
from data_prep.prompts import explore_messages
from llm import parse_json, reply_text

GROUP_WORDS = {
    "스트라이커": ["스트라이커", "공격수", "원톱", "투톱", "골잡이", "ST", "CF"],
    "윙어": ["윙어", "측면 공격", "윙", "LW", "RW", "LM", "RM"],
    "공격형 미드필더": ["공격형 미드필더", "공미", "CAM", "플레이메이커"],
    "중앙 미드필더": ["중앙 미드필더", "중미", "CM", "박스 투 박스"],
    "수비형 미드필더": ["수비형 미드필더", "수미", "CDM", "홀딩"],
    "풀백": ["풀백", "윙백", "LB", "RB", "측면 수비"],
    "센터백": ["센터백", "중앙 수비", "CB"],
    "골키퍼": ["골키퍼", "키퍼", "GK"],
}
WIDE_WORDS = {   # 넓은 말 → 여러 그룹
    "수비수": ["센터백", "풀백"], "미드필더": ["공격형 미드필더", "중앙 미드필더", "수비형 미드필더"],
    "공격": ["스트라이커", "윙어", "공격형 미드필더"],
}
STAT_WORDS = {
    "goal": ["골", "득점", "결정력", "골잡이"], "assist": ["도움", "어시스트", "찬스 메이킹", "킬패스"],
    "tackle": ["태클", "수비력", "볼 탈취"], "block": ["블록", "차단"], "pass_rate": ["패스"],
    "dribble_rate": ["드리블"], "shoot": ["슈팅"], "effective_shoot": ["유효슈팅"],
}
STAT_NAMES = {key: name for key, name, _ in STATS} | {"points": "공격 포인트"}

# 사람들이 자주 찾는 조건 — 홈의 추천 검색어는 이것과 이 앱에서 실제로 찾은 조건(search_log)을 섞어 무작위로 뽑는다.
# 조건 탐색이 읽을 수 있는 말(포지션 · 스탯 · 현역/레전드 · 국적 · 리그 · 클럽)로만 적는다.
POPULAR_CONDITIONS = [
    "대한민국 출신 공격수", "프리미어리그 레전드 미드필더", "랭커가 골을 많이 넣는 윙어", "태클이 좋은 수비형 미드필더",
    "스페인 출신 센터백", "브라질 출신 윙어", "아르헨티나 출신 공격수", "프랑스 출신 미드필더", "잉글랜드 출신 풀백",
    "독일 출신 골키퍼", "이탈리아 레전드 수비수", "네덜란드 레전드 공격수", "포르투갈 출신 윙어", "레알 마드리드 출신 미드필더",
    "바르셀로나 레전드", "맨체스터 유나이티드 레전드", "어시스트가 많은 공격형 미드필더", "드리블이 좋은 윙어",
    "패스 성공률이 높은 중앙 미드필더", "블록을 많이 하는 센터백", "유효슈팅이 많은 스트라이커", "현역 최고의 스트라이커",
    "아이콘 골키퍼", "일본 출신 미드필더", "세리에 A 레전드 수비수", "분데스리가 공격수", "라리가 미드필더",
    "골을 많이 넣는 레전드 공격수", "리버풀 출신 공격수",
]


def suggest_conditions(counts: dict[str, int], k: int = 5, rng: random.Random | None = None) -> list[str]:
    """자주 찾는 조건 k 개를 무작위로 — 이 앱에서 많이 찾은 조건일수록 잘 뽑힌다 (늘 같은 것만 보이지 않게 무작위)."""
    rng = rng or random.Random()
    weights = {q: 1.0 for q in POPULAR_CONDITIONS}
    for query, n in counts.items():
        weights[query] = weights.get(query, 0.0) + 1 + math.log2(1 + n)
    # 가중치 비복원 추출 (Efraimidis–Spirakis): U^(1/w) 가 큰 k 개
    return sorted(weights, key=lambda q: rng.random() ** (1 / weights[q]), reverse=True)[:k]


def parse_condition(condition: str) -> dict:
    """조건 문장 → {groups, stat, kind}. 못 읽으면 빈 값 — 의미 검색만으로 진행한다."""
    text = condition.replace(" ", "")
    groups = [g for g, words in GROUP_WORDS.items() if any(w.replace(" ", "") in text for w in words)]
    if not groups:
        for word, gs in WIDE_WORDS.items():
            if word in text:
                groups = gs
                break
    stat = next((key for key, words in STAT_WORDS.items() if any(w.replace(" ", "") in text for w in words)), None)
    if stat == "shoot" and "유효슈팅" in text:
        stat = "effective_shoot"
    kind = "레전드" if any(w in text for w in ("레전드", "아이콘", "ICON", "은퇴")) else ("현역" if "현역" in text else None)
    return {"groups": groups, "stat": stat, "kind": kind}


def stat_value(player: dict, key: str) -> float:
    values = stat_values(player.get("ranker"))
    if not values:
        return -1
    return values["goal"] + values["assist"] if key == "points" else values[key]


def default_stat(group: str | None) -> str:
    """조건에 스탯이 없을 때 그 포지션의 대표 스탯 — 공격은 공격 포인트, 수비는 태클."""
    first = (KEY_STATS.get(group or "") or ["goal"])[0]
    return "points" if first in ("goal", "assist") else first


def group_rank(player: dict, players: list[dict], key: str | None = None, min_matches: int = 5) -> tuple[int, int, str] | None:
    """같은 포지션 그룹 안에서의 파워 랭킹 순위 (순위, 전체, 스탯 이름). 랭커 기록이 없으면 None."""
    group = player.get("position_group")
    if not group or not player.get("ranker"):
        return None
    key = key or default_stat(group)
    pool = [p for p in players if p.get("position_group") == group and p.get("ranker")
            and p["ranker"]["match_count"] >= min_matches]
    pool.sort(key=lambda p: -stat_value(p, key))
    ids = [p["player_id"] for p in pool]
    if player["player_id"] not in ids:
        return None
    return ids.index(player["player_id"]) + 1, len(ids), STAT_NAMES[key]


STOP_WORDS = {"선수", "선수들", "카드", "좋은", "잘하는", "많이", "넣는", "하는", "있는", "뛰는", "같은", "찾아줘", "찾아",
              "추천", "해줘", "높은", "강한", "뛰어난", "최고", "그리고", "또는", "이런", "중에", "시즌", "출신",
              "랭커", "랭킹", "경기", "공식경기", "자주", "쓰는", "빠른", "느린", "키가", "주는"}
SUFFIXES = ("출신의", "출신", "에서", "으로", "에게", "의", "은", "는", "이", "가", "을", "를", "로")


def _variants(word: str) -> list[str]:
    """원래 단어 + 조사를 뗀 단어 — '스페인'(인으로 끝나는 나라 이름)처럼 떼면 안 되는 말도 원래 단어로 맞춘다."""
    forms = [word]
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            forms.append(word[: -len(suffix)])
    return forms


def keywords(condition: str) -> list[str]:
    """포지션 · 스탯 · 현역/레전드 말을 뺀 나머지 단어 — 국적 · 리그 · 클럽 이름 같은 것 (예: 대한민국, 프리미어리그)."""
    vocab = {w.replace(" ", "") for words in list(GROUP_WORDS.values()) + list(STAT_WORDS.values()) for w in words}
    vocab |= set(WIDE_WORDS) | {"레전드", "아이콘", "ICON", "은퇴", "현역"}
    found = []
    for word in re.findall(r"[0-9A-Za-z가-힣]+", condition):
        forms = _variants(word)
        # 포지션 · 스탯 말의 일부(예: '수비형' ⊂ '수비형미드필더')이거나 그 말을 품은 단어(예: '태클이')는 뺀다
        if any(f in STOP_WORDS for f in forms) or any(v in f or f in v for f in forms for v in vocab):
            continue
        if len(forms[-1]) >= 2:
            found.append(word)
    return found


def _hits(text: str, words: list[str]) -> int:
    return sum(1 for w in words if any(f in text for f in _variants(w)))


def db_candidates(players: list[dict], parsed: dict, words: list[str], top: int = 40) -> list[dict]:
    """분석된 DB 전체에서 후보 — 포지션 · 현역/레전드로 거르고, 단어(국적 · 리그 · 클럽)가 맞는 선수만, 랭킹 순."""
    pool = [p for p in players
            if (not parsed["groups"] or p.get("position_group") in parsed["groups"])
            and (parsed["kind"] is None or (parsed["kind"] == "레전드") == ("ICON" in p["season"]))]

    def hits(p: dict) -> int:
        return _hits(f"{p['name']} {p.get('intro') or ''} {p['season']}", words)

    if words:
        pool = [p for p in pool if hits(p) > 0]
    key = parsed["stat"] or default_stat(parsed["groups"][0] if parsed["groups"] else None)
    pool.sort(key=lambda p: (-hits(p), -stat_value(p, key)))
    return pool[:top]


def explore_players(condition: str, store, model, players_by_id: dict[str, dict], settings) -> dict:
    """{"items": [{"player_id", "reason", "rank"}], "excluded": [{"player_id", "reason"}],
        "filtered": 조건으로 거른 수, "semantic": 의미 검색으로 보탠 수, "checked": AI 가 확인한 수, "matched": 맞는 수}
    items 는 조건에 맞는 선수만, 스탯 조건이 있으면 랭킹 순 (최대 explore_max_results 장).

    후보: 분석된 DB 전체를 조건(포지션 · 스탯 · 현역/레전드 · 국적/리그/클럽 단어)으로 거른 상위 40명.
    조건을 하나도 읽지 못했거나 거른 결과가 적으면 메타데이터 색인 의미 검색으로 보탠다.
    LLM 은 후보마다 맞음/아님을 하나씩 판단한다 — 고른 것만 적게 하면 맞는 후보를 빠뜨려도 티가 나지 않아서.
    """
    players = list(players_by_id.values())
    parsed = parse_condition(condition)
    words = keywords(condition)
    pool = (db_candidates(players, parsed, words, top=10_000)
            if (parsed["groups"] or parsed["stat"] or parsed["kind"] or words) else [])
    candidates = pool[:40]
    if len(candidates) < 15:
        docs = store.search_meta(condition, k=settings.explore_candidates)
        seen = {p["player_id"] for p in candidates}
        for d in docs:
            p = players_by_id.get(d.metadata.get("player_id"))
            if p and p["player_id"] not in seen and (not parsed["groups"] or p.get("position_group") in parsed["groups"]):
                seen.add(p["player_id"])
                candidates.append(p)
    found = {"items": [], "excluded": [], "filtered": len(pool), "semantic": len(candidates) - min(len(pool), 40),
             "checked": len(candidates), "matched": 0}
    if not candidates:
        return found

    key = parsed["stat"]
    notes = {}
    for p in candidates:
        rank = group_rank(p, players, key)
        if rank:
            notes[p["player_id"]] = f"파워 랭킹 {p['position_group']} {rank[2]} {rank[0]}위/{rank[1]}"
    messages = explore_messages(condition, candidates, notes)
    # 같은 조건이면 같은 결과가 나오도록 temperature 0 (기본값 1 이면 부를 때마다 고르는 수가 크게 달라진다)
    verdicts = parse_json(reply_text(model.invoke(messages, temperature=0)), list) or []

    results, excluded, judged = [], [], set()
    for item in verdicts:
        if not isinstance(item, dict):
            continue
        no = item.get("no")
        if not isinstance(no, int) or isinstance(no, bool) or not 1 <= no <= len(candidates):
            continue
        player = candidates[no - 1]
        if player["player_id"] in judged:
            continue
        judged.add(player["player_id"])
        reason = str(item.get("reason", "")).strip()
        if item.get("match", True) is False:
            excluded.append({"player_id": player["player_id"], "reason": reason})
        else:
            results.append({"player_id": player["player_id"], "reason": reason, "rank": notes.get(player["player_id"], "")})
    if key:   # "골을 많이 넣는 ~" — 고른 선수를 그 스탯 순(랭킹 순)으로
        results.sort(key=lambda r: -stat_value(players_by_id[r["player_id"]], key))
    return found | {"items": results[: settings.explore_max_results], "excluded": excluded, "matched": len(results)}


def describe(condition: str, players: list[dict], found: dict) -> str:
    """화면 안내 — 조건을 어떻게 읽었고, AI 가 후보 몇 장을 확인해 몇 장이 맞았는지 (화면에 보이는 수와 맞게)."""
    parsed = parse_condition(condition)
    words = keywords(condition)
    parts = []
    if parsed["groups"]:
        parts.append("포지션 " + "·".join(parsed["groups"]))
    if parsed["kind"]:
        parts.append(parsed["kind"])
    if words:
        parts.append("단어 " + "·".join(words))
    if parsed["stat"]:
        parts.append(f"{STAT_NAMES[parsed['stat']]} 순")
    shown = len(found["items"])
    checked = f"AI가 {found['checked']}장을 한 장씩 확인해 {found['matched']}장이 조건에 맞음" + (
        f" · 상위 {shown}장" if found["matched"] > shown else "")
    if not parts:
        return f"DB {len(players)}장 중 뜻이 가까운 후보 → {checked}"
    pool = f"조건({', '.join(parts)})에 걸린 후보 {found['filtered']}장"
    if found["filtered"] > found["checked"] - found["semantic"]:
        pool += f" 중 상위 {found['checked'] - found['semantic']}장"
    if found["semantic"]:
        pool += f" + 뜻이 가까운 후보 {found['semantic']}장"
    return f"DB {len(players)}장 → {pool} → {checked}"
