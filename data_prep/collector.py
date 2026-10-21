"""외부 문서 수집 — 검색어 추출(프롬프트 1) → 검색 → 후보 선별(프롬프트 2) → 본문 수집 · 저장. (F2003 · F2004 · NF2002)

    storage/docs/{선수 ID}/{문서 ID}.json   수집한 문서 (본문 · 원문 URL · 검색어 · 수집일)
    storage/manifest.json                    선수별 수집 이력 — 이미 검색한 검색어는 다시 수집하지 않는다

선수 자체 문서(players.json 의 wiki_title)는 선별 · 길이 같은 어떤 제외 규칙보다 우선해 항상 수집한다.
청킹 · 임베딩 색인은 vector_store.py 가 맡는다.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from data_prep import prompts
from llm import parse_json, reply_text

# 선별 프롬프트에 보내기 전에 제목만 보고 버리는 문서.
SKIP_RULES = [
    (re.compile(r"^\d{1,4}년$"), "연도 문서"),
    (re.compile(r"목록"), "목록 문서"),
    (re.compile(r"\(동음이의\)"), "동음이의 문서"),
]


@dataclass
class CollectReport:
    """수집 한 번의 결과. 화면의 진행 상태와 검증(4.2)에 쓴다."""

    keywords: list[str] = field(default_factory=list)       # 이번 질문의 검색어
    new_keywords: list[str] = field(default_factory=list)   # 그중 처음 검색한 것
    candidates: int = 0
    selected: list[str] = field(default_factory=list)       # 새로 수집한 문서 제목
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (제목, 버린 이유)
    new_docs: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def cached(self) -> bool:
        return not self.new_docs and not self.new_keywords


def _question_key(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def save_doc(docs_dir: Path, player: dict, page: dict, keyword: str, is_self: bool, source: str) -> dict:
    """수집한 문서를 storage/docs/{선수 ID}/{문서 ID}.json 에 저장한다."""
    doc = {
        **page,
        "source": source,
        "player_id": player["player_id"],
        "related_player": player["name"],
        "search_query": keyword,
        "collected_at": date.today().isoformat(),
        "is_self": is_self,
    }
    folder = Path(docs_dir) / player["player_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{page['page_id']}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def load_stored_docs(docs_dir: Path, player_id: str) -> list[dict]:
    """이 선수로 수집해 저장한 문서. 선수 자체 문서가 맨 앞이다."""
    folder = Path(docs_dir) / player_id
    if not folder.is_dir():
        return []
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
    return sorted(docs, key=lambda d: (not d.get("is_self"), d["title"]))


class Manifest:
    """선수별 수집 이력. {선수 ID: {keywords, questions, page_ids, collected_at}}"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def entry(self, player_id: str) -> dict:
        return self.data.setdefault(
            player_id, {"keywords": [], "questions": {}, "page_ids": [], "collected_at": None}
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class Collector:
    def __init__(self, settings, model, wiki):
        self.settings = settings
        self.model = model
        self.wiki = wiki
        self.manifest = Manifest(settings.manifest_path)

    def stored_docs(self, player_id: str) -> list[dict]:
        return load_stored_docs(self.settings.docs_dir, player_id)

    # ── 프롬프트 1 · 2 ───────────────────────────────────────────────

    def extract_keywords(self, player: dict, question: str) -> list[str]:
        """프롬프트 1. 파싱에 실패하면 빈 목록 — 선수 자체 문서만으로 답이 이어진다."""
        messages = prompts.keyword_messages(player, question, self.settings.max_keywords)
        keywords = parse_json(reply_text(self.model.invoke(messages)), list) or []
        cleaned = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
        names = {player["name"], player.get("wiki_title") or ""}
        return _dedupe([k for k in cleaned if k not in names])[: self.settings.max_keywords]

    def select_candidates(self, player: dict, question: str, pool: list[dict]) -> list[int]:
        """프롬프트 2. 고른 후보 번호(0부터)를 관련이 큰 순서로."""
        messages = prompts.select_messages(player, question, pool, self.settings.max_docs)
        picked = parse_json(reply_text(self.model.invoke(messages)), list) or []
        valid = [n - 1 for n in picked if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(pool)]
        return list(dict.fromkeys(valid))

    # ── 수집 ────────────────────────────────────────────────────────

    def collect(self, player: dict, question: str, on_step: Callable[[str], None] | None = None) -> CollectReport:
        step = on_step or (lambda _message: None)
        player_id = player["player_id"]
        entry = self.manifest.entry(player_id)
        stored = self.stored_docs(player_id)
        stored_pages = {d["page_id"] for d in stored}
        report = CollectReport()

        # 0) 선수 자체 문서 — 아직 없으면 무조건 수집한다 (선별 · 길이 규칙보다 우선)
        if player.get("wiki_title") and not any(d.get("is_self") for d in stored):
            step(f"선수 문서 수집: {player['wiki_title']}")
            page = self.wiki.fetch(player["wiki_title"])
            if page:
                report.new_docs.append(self._save(player, page, player["wiki_title"], True))
                stored_pages.add(page["page_id"])
        elif not player.get("wiki_title"):
            report.notes.append("이 선수의 위키백과 문서가 없습니다. 메타데이터와 검색된 문서만 씁니다.")

        # 1) 검색어 — 같은 질문이면 프롬프트 1을 다시 부르지 않는다 (NF2002)
        qkey = _question_key(question)
        if qkey in entry["questions"]:
            keywords = entry["questions"][qkey]
        else:
            step("검색어를 뽑는 중... (프롬프트 1)")
            keywords = self.extract_keywords(player, question)
        report.keywords = keywords
        report.new_keywords = [k for k in keywords if k not in entry["keywords"]]
        if keywords:
            step("검색어: " + ", ".join(keywords))

        if report.new_keywords:
            # 2) 검색 — 새 검색어만. 이미 저장한 문서는 후보에서 뺀다.
            seen = {d["title"] for d in stored} | {d["title"] for d in report.new_docs}
            candidates: list[dict] = []
            results = self._parallel(lambda k: self.wiki.search(k, self.settings.search_limit), report.new_keywords)
            for keyword, items in zip(report.new_keywords, results):
                for item in items:
                    if item["title"] not in seen and item["page_id"] not in stored_pages:
                        seen.add(item["title"])
                        candidates.append({**item, "keyword": keyword})
            report.candidates = len(candidates)
            step(f"후보 문서 {len(candidates)}건")

            # 3) 제목 규칙으로 먼저 거르고, 나머지를 프롬프트 2로 선별한다
            pool = []
            for cand in candidates:
                reason = next((why for rule, why in SKIP_RULES if rule.search(cand["title"])), None)
                if reason:
                    report.rejected.append((cand["title"], reason))
                else:
                    pool.append(cand)
            picked = []
            if pool:
                step("색인할 문서를 고르는 중... (프롬프트 2)")
                picked = self.select_candidates(player, question, pool)
            for rank, i in enumerate(picked):
                if rank >= self.settings.max_docs:
                    report.rejected.append((pool[i]["title"], "선별 개수 상한"))
            chosen = picked[: self.settings.max_docs]
            for i, cand in enumerate(pool):
                if i not in picked:
                    report.rejected.append((cand["title"], "선별 프롬프트가 고르지 않음"))

            # 4) 본문 수집 · 저장 — 고른 문서를 동시에 받는다
            pages = self._parallel(lambda i: self.wiki.fetch(pool[i]["title"]), chosen)
            for i, page in zip(chosen, pages):
                cand = pool[i]
                if not page:
                    report.rejected.append((cand["title"], "본문을 받지 못함"))
                    continue
                if page["page_id"] in stored_pages:
                    continue
                if len(page["text"]) < self.settings.min_doc_chars:
                    report.rejected.append((cand["title"], f"본문이 짧음 ({len(page['text'])}자)"))
                    continue
                report.new_docs.append(self._save(player, page, cand["keyword"], False))
                stored_pages.add(page["page_id"])

        # 5) 수집 이력 저장 — 여기까지 와야 검색어를 '수집 완료'로 남긴다. 중간에 실패하면 다음에 다시 시도한다.
        entry["keywords"] = _dedupe(entry["keywords"] + report.new_keywords)
        entry["questions"][qkey] = keywords
        entry["page_ids"] = sorted(stored_pages)
        if report.new_docs:
            entry["collected_at"] = date.today().isoformat()
        self.manifest.save()

        report.selected = [d["title"] for d in report.new_docs]
        if report.cached:
            step(f"이미 수집한 검색어입니다. 저장된 문서 {len(stored)}건을 씁니다.")
        else:
            step(f"새로 수집 {len(report.new_docs)}건: " + (", ".join(report.selected) or "없음"))
        if report.rejected:
            step("버린 문서: " + " · ".join(f"{t} ({why})" for t, why in report.rejected[:12]))
        return report

    def _save(self, player: dict, page: dict, keyword: str, is_self: bool) -> dict:
        return save_doc(self.settings.docs_dir, player, page, keyword, is_self, self.wiki.name)

    def _parallel(self, fn, items: list) -> list:
        """위키 호출을 동시에 보낸다. 호출 간격은 WikiClient 가 잠금으로 지킨다(NF2006). 결과 순서는 입력 순서."""
        if len(items) <= 1 or self.settings.parallel <= 1:
            return [fn(item) for item in items]
        with ThreadPoolExecutor(max_workers=self.settings.parallel) as pool:
            return list(pool.map(fn, items))
