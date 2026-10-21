"""조건 기반 항목 탐색 — "이런 선수 찾아줘". (F2011)

    자연어 조건 → 후보 = ① 파워 랭킹 상위(조건의 포지션 · 스탯으로) + ② 메타데이터 색인 의미 검색 상위
              → LLM 이 후보 정보(랭킹 순위 포함)만 보고 조건에 맞는 선수만 고름 → 스탯이 있으면 랭킹 순으로

의미 검색만 쓰면 "골을 많이 넣는 윙어"처럼 순위가 중요한 조건에서 정작 랭킹 1위가 후보에 없을 수 있다.
그래서 조건에서 포지션 · 스탯 · 현역/레전드를 읽어 파워 랭킹 상위를 후보에 함께 넣는다.
"""

from __future__ import annotations

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


def explore_players(condition: str, store, model, players_by_id: dict[str, dict], settings) -> list[dict]:
    """[{"player_id", "reason", "rank"}] — 조건에 맞는 선수만, 스탯 조건이 있으면 랭킹 순. 맞는 선수가 없으면 빈 목록.

    후보: 분석된 DB 전체를 조건(포지션 · 스탯 · 현역/레전드 · 국적/리그/클럽 단어)으로 거른 상위 40명.
    조건을 하나도 읽지 못했거나 거른 결과가 적으면 메타데이터 색인 의미 검색으로 보탠다.
    """
    players = list(players_by_id.values())
    parsed = parse_condition(condition)
    words = keywords(condition)
    candidates = db_candidates(players, parsed, words) if (parsed["groups"] or parsed["stat"] or parsed["kind"] or words) else []
    if len(candidates) < 15:
        docs = store.search_meta(condition, k=settings.explore_candidates)
        seen = {p["player_id"] for p in candidates}
        for d in docs:
            p = players_by_id.get(d.metadata.get("player_id"))
            if p and p["player_id"] not in seen and (not parsed["groups"] or p.get("position_group") in parsed["groups"]):
                seen.add(p["player_id"])
                candidates.append(p)
    if not candidates:
        return []

    key = parsed["stat"]
    notes = {}
    for p in candidates:
        rank = group_rank(p, players, key)
        if rank:
            notes[p["player_id"]] = f"파워 랭킹 {p['position_group']} {rank[2]} {rank[0]}위/{rank[1]}"
    messages = explore_messages(condition, candidates, settings.explore_max_results, notes)
    picked = parse_json(reply_text(model.invoke(messages)), list) or []

    results, chosen = [], set()
    for item in picked:
        if not isinstance(item, dict):
            continue
        no = item.get("no")
        if not isinstance(no, int) or isinstance(no, bool) or not 1 <= no <= len(candidates):
            continue
        player = candidates[no - 1]
        if player["player_id"] not in chosen:
            chosen.add(player["player_id"])
            results.append({"player_id": player["player_id"], "reason": str(item.get("reason", "")).strip(),
                            "rank": notes.get(player["player_id"], "")})
    if key:   # "골을 많이 넣는 ~" — 고른 선수를 그 스탯 순(랭킹 순)으로
        results.sort(key=lambda r: -stat_value(players_by_id[r["player_id"]], key))
    return results[: settings.explore_max_results]


def describe(condition: str, players: list[dict]) -> str:
    """화면 안내 — 조건을 어떻게 읽었고 DB 전체 중 몇 명을 AI 가 확인했는지."""
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
    if not parts:
        return f"DB {len(players)}장 중 뜻이 가까운 선수를 AI가 확인"
    matched = len(db_candidates(players, parsed, words, top=10_000))
    return f"DB {len(players)}장 → 조건({', '.join(parts)})에 맞는 {matched}장 → 상위 {min(matched, 40)}장을 AI가 확인 · 랭킹 순"
