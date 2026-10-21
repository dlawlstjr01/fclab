"""LLM 기능 — AI 한줄 스카우팅 · AI 코치. (F2010)

둘 다 근거를 번호로 넘기고, 답에 [근거 N] 을 붙이게 한다. 화면은 인용된 근거만 보여 준다(F2008).
AI 한줄 스카우팅은 선수마다 한 번만 만들어 SQLite(storage/fclab.db)에 둔다 (같은 선수를 다시 열면 0초).
"""

from __future__ import annotations

from data_prep.players import index_text, player_label
from data_prep.prompts import coach_messages, scout_messages
from llm import reply_text


class AiCache:
    """AI 결과 캐시 — SQLite(storage/fclab.db) 의 ai_cache 표."""

    def __init__(self, db):
        self.db = db

    def get(self, key: str) -> str | None:
        return self.db.ai_get(key)

    def put(self, key: str, value: str) -> None:
        self.db.ai_put(key, value)


def scout_evidence(player: dict) -> list[dict]:
    return [{
        "number": 1, "source_type": "player_meta", "source_label": "선수 메타데이터 (넥슨 Open API · 위키백과 요약)",
        "title": player_label(player), "url": player.get("wiki_url") or "https://openapi.nexon.com/game/fconline/",
    }]


def scout_line(player: dict, model, cache: AiCache, model_name: str) -> str:
    key = f"scout:{model_name}:{player['player_id']}:{(player.get('ranker') or {}).get('date', '')}"
    cached = cache.get(key)
    if cached:
        return cached
    line = reply_text(model.invoke(scout_messages(index_text(player)))).strip()
    if line:
        cache.put(key, line)
    return line


def _fmt(values: dict | None) -> str:
    if not values:
        return "기록 없음"
    return (f"경기당 골 {values['goal']:.2f} · 어시스트 {values['assist']:.2f} · 슈팅 {values['shoot']:.2f}"
            f" · 유효슈팅 {values['effective_shoot']:.2f} · 패스 성공률 {values['pass_rate']:.0f}%"
            f" · 드리블 성공률 {values['dribble_rate']:.0f}% · 태클 {values['tackle']:.2f}")


def coach_evidence(summary: dict) -> list[dict]:
    """내 경기 분석 결과 → 번호 붙은 근거. [근거 1] 은 전체 전적, 이후는 선수마다 내 기록 vs 랭커 평균."""
    rec = summary["record"]
    evidence = [{
        "number": 1, "source_type": "my_match", "source_label": "내 경기 기록 (넥슨 Open API)",
        "title": f"{summary['nickname']} 최근 {len(summary['matches'])}경기",
        "url": "https://openapi.nexon.com/game/fconline/",
        "text": f"전적 {rec['승']}승 {rec['무']}무 {rec['패']}패, 평균 평점 "
                f"{summary['avg_rating']:.2f}" if summary.get("avg_rating") else f"전적 {rec['승']}승 {rec['무']}무 {rec['패']}패",
    }]
    for p in summary["players"]:
        mine = _fmt(p["mine"]) + f" · 평점 {p['mine']['rating']:.2f}"
        evidence.append({
            "number": len(evidence) + 1, "source_type": "my_match", "source_label": "내 기록 vs 랭커 평균 (넥슨 Open API)",
            "title": f"{p['name']} · {p['season_short']} ({p['position']}, {p['games']}경기)",
            "url": "https://openapi.nexon.com/game/fconline/",
            "text": f"내 기록: {mine}\n같은 카드 · 같은 포지션 랭커 평균: {_fmt(p['ranker'])}",
        })
    return evidence


def tactic_evidence(squad: list[tuple[str, dict]]) -> list[dict]:
    """[근거 1] 전술 항목 설명, [근거 2~] 자리마다 선수 메타데이터 (같은 색인 텍스트를 그대로)."""
    from data_prep.tactics import tactics_guide

    evidence = [{
        "number": 1, "source_type": "guide", "source_label": "FC LAB 전술 항목 정리 (data_prep/tactics.py)",
        "title": "FC 온라인 팀 · 개인 전술 항목", "url": "", "text": tactics_guide(),
    }]
    for slot, player in squad:
        evidence.append({
            "number": len(evidence) + 1, "source_type": "player_meta",
            "source_label": "선수 메타데이터 (넥슨 Open API · 위키백과 요약)",
            "title": f"{slot} 자리 — {player_label(player)}", "url": player.get("wiki_url") or "",
            "text": index_text(player)[:900],
        })
    return evidence


def recommend_tactics(formation: str, style: str, squad: list[tuple[str, dict]], model) -> dict:
    from data_prep.prompts import tactic_messages

    evidence = tactic_evidence(squad)
    answer = reply_text(model.invoke(tactic_messages(formation, style, evidence))).strip()
    return {"answer": answer, "evidence": [{k: e[k] for k in ("number", "source_type", "source_label", "title", "url")}
                                           for e in evidence]}


def coach(summary: dict, model) -> dict:
    evidence = coach_evidence(summary)
    answer = reply_text(model.invoke(coach_messages(evidence))).strip()
    return {"answer": answer, "evidence": [{k: e[k] for k in ("number", "source_type", "source_label", "title", "url")}
                                           for e in evidence]}
