"""🤖 AI 로 찾기 — 1차 나무위키 검색 → 2차 LLM 이 조건에 맞는 선수를 모두 뽑기 → 3차 DB(FC 온라인 전체 카드)와 맞추기 (F2011).

1차  나무위키 검색(조건 그대로) → LLM 이 명단이 있을 문서를 고른다(최대 3개) → 문서에서 조건 · 핵심어와 가까운 부분만 발췌.
2차  LLM 이 발췌를 근거로 조건에 맞는 선수를 빠짐없이 (발췌에 이름이 없는데 아는 것으로 넣은 선수는 'AI 지식'으로 따로).
3차  이름을 FC 온라인 전체 카드(88,359장)의 선수 이름과 맞춘다 — 같은 이름 → 다른 표기(별칭) → 자모가 비슷한 이름(0.8 이상).
     이름만 같은 다른 사람(동명이인)은 시대로 가린다 — LLM 이 2008년까지 은퇴했다고 한 선수인데 FC 온라인의 그 이름이 6년도 더 뒤의
     현역 시즌(LIVE · TOTS · PL …) 카드가 있는 선수면 다른 사람이다 (1960 발롱도르 루이스 수아레스 ↔ 우루과이 루이스 수아레스).
     카드마다의 조건(포지션 · OVR · 급여 · 주발)과 레전드는 모아 둔 능력치로, 조건에 적은 국적 · 클럽은 데이터센터 선수 검색으로.
     DB 에 없는 선수 · 조건에 맞지 않아 뺀 선수는 따로 알려 준다.
같은 조건의 1 · 2차 결과(문서 · 명단)는 하루 동안 다시 만들지 않는다 (SQLite dc_cache). 나무위키에 닿지 않으면 2차는 AI 지식만으로.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import replace
from difflib import SequenceMatcher

from data_prep.namu import LICENSE, NamuError
from data_prep.nexon_api import split_spid
from data_prep.prompts import namu_list_messages, namu_plan_messages
from llm import parse_json, reply_text
from services.finder import PAGE_LIMIT, Filters, card_ok

log = logging.getLogger("fclab.namufind")

CACHE_KEY = "namufind:v5:"
CACHE_HOURS = 24
SEARCH_LIMIT = 20
DOCS_MAX = 3
DOC_CHARS = 9000        # 문서 하나에서 발췌할 글자
EXCERPT_CHARS = 18000   # LLM 에 보낼 발췌 전체
PLAYERS_MAX = 80
LIST_TIMEOUT = 120      # 2차 LLM — 긴 명단은 1분을 넘길 때가 있다 (기본 60초)
FUZZY = 0.8             # 자모가 이만큼 비슷하면 같은 이름으로 (셰브첸코 ↔ 셰우첸코)
VERIFY_MAX = 12         # 국적 · 클럽을 이름마다 따로 데이터센터에 물을 수 상한
LIST_WORDS = ("수상자", "역대", "명단", "스쿼드", "선수", "멤버", "목록", "베스트", "레전드", "주요", "출신", "우승", "기록")
STOP = {"선수", "선수들", "받은", "출신", "있는", "하는", "많은", "많이", "찾아줘", "알려줘", "보여줘", "카드", "중에", "중",
        "및", "그리고", "FC", "온라인"}
NOT_MATCH = ("아니지만", "아니다", "아님", "못했", "못 했", "없지만", "제외")   # LLM 이 근거에 '조건에 안 맞는다'고 적은 선수
# 그해 현역이던 선수만 받는 시즌 (24 LIVE · 25 TOTS · 25 PL · 25 UCL · 26KLeague · 23NG …) — 동명이인을 시대로 가릴 때
ERA_LAST, ERA_GAP = 2008, 6   # 이 해까지 은퇴했다는 선수인데 그보다 6년 넘게 뒤의 현역 카드만 있으면 다른 사람
ACTIVE_SEASON = re.compile(r"^(\d{2}) ?(LIVE|TOTS|TOTY|PL|PLA|PLS|UCL|KLeague|KB|NG)\b")


# ── 이름 맞추기 (3차) ──────────────────────────────────────────────

def norm(text: str) -> str:
    """카드 목록과 같은 정규화 — 띄어쓰기 · 문장 부호를 뺀다 ('마르코 반 바스텐' = '마르코반바스텐')."""
    return re.sub(r"[\W_]+", "", (text or "").lower())


def jamo(text: str) -> str:
    """한글 음절을 자모로 풀어 쓴다 — '셰브첸코'와 '셰우첸코'처럼 한 글자만 다른 표기를 비슷하게 잰다."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + code % 588 // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        else:
            out.append(ch)
    return "".join(out)


def _grams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


_INDEX: dict = {}


def name_index(catalog) -> dict:
    """카드 목록의 선수 이름 → pid 묶음 · 화면 이름(띄어쓰기 그대로) · 두 글자 조각 색인(비슷한 이름 후보를 빨리 좁힌다) ·
    한 단어 이름(베켄바워 · 플라티니처럼 성만 쓰는 레전드). 카드 목록이 바뀌면 다시 만든다."""
    key = (id(catalog), len(catalog.cards))
    if _INDEX.get("key") != key:
        names: dict[str, set[int]] = defaultdict(set)
        shown: dict[str, str] = {}
        for card in catalog.cards:
            names[norm(card["name"])].add(split_spid(card["id"])[1])
            shown.setdefault(norm(card["name"]), card["name"])
        grams: dict[str, set[str]] = defaultdict(set)
        for name in names:
            for g in _grams(name):
                grams[g].add(name)
        single = {n for n, s in shown.items() if len(s.split()) == 1 and len(n) >= 3}
        _INDEX.clear()
        _INDEX.update(key=key, names=dict(names), shown=shown, grams=dict(grams), single=single)
    return _INDEX


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, jamo(norm(a)), jamo(norm(b))).ratio()


def similar(query: str, candidate: str) -> float:
    """두 이름이 같은 사람의 다른 표기일 만큼 비슷한가 (0 ~ 1) — 이름 · 성을 따로 잰다: 이름 0.6 · 성 0.75 · 전체 FUZZY 이상.
    한 단어 이름이나 머리글자 이름('F. 로시')은 0 — 같은 이름 · 성으로만 맞춘다 ('데니스 로' ≠ '데니스',
    '플로리안 알베르트' ≠ '플로리안 비르츠', '파올로 로시' ≠ 'F. 로시')."""
    q, c = query.split(), candidate.split()
    if len(q) < 2 or len(c) < 2 or len(norm(c[0])) < 2:
        return 0.0
    first, rest, whole = _ratio(q[0], c[0]), _ratio("".join(q[1:]), "".join(c[1:])), _ratio(query, candidate)
    return whole if first >= 0.6 and rest >= 0.75 and whole >= FUZZY else 0.0


def match_name(index: dict, names: list[str]) -> tuple[set[int], str, float]:
    """이름(과 다른 표기들) → (pid 묶음, 맞은 DB 이름, 닮은 정도 — 1.0 같은 이름 · 0.9 성만 같은 한 단어 이름 · 그 밖은 자모 닮음).
    차례: 같은 이름 → 이름 · 성이 비슷한 이름('바비 찰튼' ↔ '보비 찰턴', '안드리 셰브첸코' ↔ '안드리 셰우첸코') →
    FC 온라인이 성만 쓰는 레전드('프란츠 베켄바워' → '베켄바워'). 못 찾으면 (빈 묶음, '', 0)."""
    for name in names:
        if norm(name) in index["names"]:
            return set(index["names"][norm(name)]), index["shown"][norm(name)], 1.0
    best = (0.0, "")
    for name in names:
        query = norm(name)
        if len(query) < 3:
            continue
        grams = _grams(query)
        shared = Counter(n for g in grams for n in index["grams"].get(g, ()))
        need = max(1, len(grams) // 3)
        for candidate, count in shared.most_common(80):
            if count < need:
                break
            score = similar(name, index["shown"][candidate])
            if score > best[0]:
                best = (score, candidate)
    if best[0]:
        return set(index["names"][best[1]]), index["shown"][best[1]], round(best[0], 2)
    for name in names:
        parts = name.split()
        if len(parts) >= 2 and norm(parts[-1]) in index["single"]:
            return set(index["names"][norm(parts[-1])]), index["shown"][norm(parts[-1])], 0.9
    return set(), "", 0.0


# ── 발췌 (1차) ─────────────────────────────────────────────────────

def keywords(*texts: str) -> list[str]:
    """조건 · 계획에서 문서를 고를 말 — 두 글자 이상, 흔한 말은 뺀다 (순서 그대로, 같은 말은 한 번)."""
    words = []
    for text in texts:
        for word in re.findall(r"[가-힣A-Za-z0-9']+", text or ""):
            if len(word) >= 2 and word not in STOP and word not in words:
                words.append(word)
    return words


def excerpt(doc: dict, words: list[str], budget: int = DOC_CHARS) -> str:
    """문서에서 조건 · 핵심어와 가까운 부분만 — 제목 길(위 제목까지 '5. 역대 수상자 > 5.1. 남성')에 핵심어 · 명단 말(수상자 ·
    역대 · 명단 …)이 있으면 크게, 짧은 줄이 많은(표 · 목록 — 명단) 부분일수록, 본문에 핵심어가 있을수록 앞. '여성' 부분은 뺀다
    (FC 온라인은 남자 선수만). 고른 부분은 점수 높은 것부터 (LLM 이 앞쪽 명단을 먼저 꼼꼼히), 넘치는 한 부분은 앞쪽만."""
    scored = []
    for i, section in enumerate(doc.get("sections") or []):
        path, text = section.get("path") or section["heading"], section["text"]
        if "여성" in path or "여자" in path:
            continue
        lines = text.split("\n")
        score = sum(6 for w in words if w in path) + sum(4 for w in LIST_WORDS if w in path)
        score += min(sum(1 for line in lines if len(line) <= 40), 100) / 4   # 표 · 목록
        score += sum(min(text.count(w), 5) for w in words)
        scored.append((score, i, path))
    picked, used = [], 0
    for score, i, path in sorted(scored, key=lambda x: -x[0]):
        size = len(doc["sections"][i]["text"]) + len(path) + 4
        if score <= 0 or (picked and used + size > budget):
            continue
        picked.append((i, path))
        used += size
        if used >= budget:
            break
    parts = [f"## {path}\n{doc['sections'][i]['text']}" for i, path in picked]   # 점수 높은(명단 표) 부분부터
    return "\n".join(parts)[:budget]


def active_years(catalog, pid: int) -> list[int]:
    """그 선수가 현역으로 받은 시즌의 해 (24 LIVE → 2024) — 레전드 · 스페셜 시즌은 세지 않는다."""
    years = []
    for spid in catalog.cards_of(pid):
        m = ACTIVE_SEASON.match(catalog.season_name(spid))
        if m:
            years.append(2000 + int(m.group(1)))
    return years


def parse_rows(reply: str) -> list[dict]:
    """2차 LLM 답 — 한 줄에 한 명 '이름 | 다른 표기 | 국적 | 포지션 | 마지막 해 | 근거 | N/K' → [{name, aliases, nation, pos,
    last, why}]. 칸이 모자라거나 머리줄 · 설명 줄은 건너뛴다."""
    rows = []
    for line in (reply or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not cells[0] or cells[0] in ("이름", "name"):
            continue
        try:
            last = int(re.sub(r"\D", "", cells[4]) or 0)
        except ValueError:
            last = 0
        rows.append({"name": cells[0].lstrip("-·0123456789. ").strip(),
                     "aliases": [a.strip() for a in re.split(r"[,·/]", cells[1]) if a.strip() and a.strip() != "-"][:4],
                     "nation": cells[2], "pos": cells[3].upper()[:2], "last": last if 1900 < last < 2100 else 0,
                     "why": cells[5][:60]})
    return [r for r in rows if r["name"]]


# ── 세 단계 ────────────────────────────────────────────────────────

class NamuFinder:
    """조건 → 나무위키 → LLM 명단 → FC 온라인 카드. namu = data_prep.namu.NamuClient, finder = services.finder.CardFinder."""

    def __init__(self, namu, db, catalog, finder, model):
        self.namu, self.db, self.catalog, self.finder, self.model = namu, db, catalog, finder, model

    # 1차 — 나무위키
    def gather(self, condition: str) -> dict:
        """{search: [{title, snippet}], docs: [{title, url, excerpt}], keywords, kind('list' · 'attribute'), error}."""
        out = {"search": [], "docs": [], "keywords": keywords(condition), "kind": "list", "error": ""}
        try:
            out["search"] = self.namu.search(condition, self.db, SEARCH_LIMIT)
        except NamuError as exc:
            out["error"] = str(exc)
            return out
        plan = {}
        try:
            plan = parse_json(reply_text(self.model.invoke(namu_plan_messages(condition, out["search"]), temperature=0)),
                              dict) or {}
        except Exception as exc:  # noqa: BLE001 — 문서 고르기를 못 하면 검색 결과 위쪽 문서로
            log.warning("읽을 나무위키 문서 고르기(LLM) 실패: %s", exc)
        out["kind"] = "attribute" if plan.get("kind") == "attribute" else "list"
        out["keywords"] = keywords(condition, " ".join(str(k) for k in plan.get("keywords") or []))
        titles = [str(t).strip() for t in plan.get("docs") or [] if str(t).strip()][:DOCS_MAX]
        titles = titles or [r["title"] for r in out["search"][:2]]
        for title in titles:
            try:
                doc = self.namu.document(title, self.db)
                if doc is None:   # 없는 제목 — 검색 결과 중 그 말이 든 제목으로
                    alt = next((r["title"] for r in out["search"]
                                if title.replace(" ", "") in r["title"].replace(" ", "") and r["title"] not in titles), None)
                    doc = self.namu.document(alt, self.db) if alt else None
            except NamuError as exc:
                out["error"] = str(exc)
                break
            if doc and all(d["title"] != doc["title"] for d in out["docs"]):
                out["docs"].append({"title": doc["title"], "url": doc["url"], "excerpt": excerpt(doc, out["keywords"])})
        return out

    # 2차 — LLM 명단
    def extract(self, condition: str, gathered: dict) -> list[dict]:
        """[{name, aliases, nation, pos, why, src('나무위키' · 'AI 지식')}] — 발췌에 이름(다른 표기 포함)이 없으면 'AI 지식'."""
        parts = []
        if gathered["search"]:
            parts.append("### 검색 결과\n" + "\n".join(f"- {r['title']}: {r['snippet']}" for r in gathered["search"][:12]))
        parts += [f"### 문서: {d['title']}\n{d['excerpt']}" for d in gathered["docs"]]
        text = "\n\n".join(parts)[:EXCERPT_CHARS]
        reply = reply_text(self.model.invoke(namu_list_messages(condition, text), temperature=0, max_tokens=4096,
                                             timeout=LIST_TIMEOUT))
        plain = norm(text)
        players, seen = [], set()
        for row in parse_rows(reply):
            if norm(row["name"]) in seen or any(w in row["why"] for w in NOT_MATCH):   # '…는 아니지만' — 스스로 아니라고 한 선수
                continue
            seen.add(norm(row["name"]))
            cited = any(norm(n) and norm(n) in plain for n in [row["name"]] + row["aliases"])
            players.append({**row, "src": "나무위키" if cited else "AI 지식"})
        return players[:PLAYERS_MAX]

    # 3차 — DB
    def match(self, players: list[dict]) -> tuple[list[dict], list[dict]]:
        """이름 → FC 온라인 선수(pid 묶음). (맞은 선수, DB 에 없는 선수)."""
        index = name_index(self.catalog)
        found, missing, seen = [], [], set()
        for p in players:
            pids, db_name, score = match_name(index, [p["name"]] + p["aliases"])
            if pids and frozenset(pids) in seen:   # 표기만 다르게 두 번 적은 같은 선수 ('프랑코 바레시' · '프란코 바레시')
                continue
            seen.add(frozenset(pids))
            (found if pids else missing).append({**p, "pids": sorted(pids), "db_name": db_name, "score": score})
        return found, missing

    def _stats(self, spids: list[int]) -> dict[int, dict]:
        known: dict[int, dict] = {}
        for i in range(0, len(spids), 800):
            known.update(self.db.card_stats(spids[i:i + 800]))
        return known

    def pick_cards(self, found: list[dict], f: Filters) -> tuple[list[dict], list[dict]]:
        """선수마다 조건에 맞는 카드 — 레전드(아이콘 카드가 있는 선수) · 카드 조건(포지션 · OVR · 급여 · 주발)은 모아 둔 능력치로.
        같은 이름의 선수가 여럿이면 맞는 카드가 많은(그다음 카드가 많은) 선수. (남은 선수, 뺀 선수 — reason 과 함께)."""
        legends = self.finder.legends()
        known = self._stats([s for p in found for pid in p["pids"] for s in self.catalog.cards_of(pid)])
        card_rules = any((f.seasons, f.positions, f.ovr_min, f.ovr_max, f.pay_min, f.pay_max, f.foot, f.weak_foot))
        kept, dropped = [], []
        for p in found:
            options, other_era = [], []
            for pid in p["pids"]:
                late = [y for y in active_years(self.catalog, pid)
                        if p.get("last") and p["last"] <= ERA_LAST and y >= p["last"] + ERA_GAP]
                if late and self._nation_ok(pid, p["db_name"], p.get("nation")) is not True:
                    # 마지막으로 뛴 해 뒤에 현역 카드가 있고, 그 국적으로 찾아도 이 선수가 아니다 — 이름만 같은 다른 사람
                    # (LLM 이 적은 해가 틀렸어도 국적이 맞으면 같은 사람으로 둔다 — 호날두를 '2008년까지'라고 적는 일)
                    other_era.append(max(late))
                    continue
                if f.legend is not None and (pid in legends) != f.legend:
                    continue
                cards = self.catalog.cards_of(pid)
                ok = [s for s in cards if card_ok(f, s, known.get(s))] if card_rules else cards
                if ok:
                    options.append((len(ok), len(cards), pid, ok))
            if not options:
                if other_era and len(other_era) == len(p["pids"]):
                    reason = (f"이름만 같은 다른 선수 — FC 온라인의 '{p['db_name']}'은(는) {max(other_era)}년 현역 카드가 있는 선수"
                              f" ({p['name']} 은(는) {p['last']}년까지)")
                elif f.legend is not None and not any((pid in legends) == f.legend for pid in p["pids"]):
                    reason = "레전드가 아님" if f.legend else "레전드(은퇴) 선수"
                else:
                    reason = "카드 조건(포지션 · OVR · 급여 · 주발)이 맞지 않음"
                dropped.append({**p, "reason": reason})
                continue
            _, _, pid, ok = max(options)
            ok.sort(key=lambda s: -((known.get(s) or {}).get("ovr") or 0))
            best = known.get(ok[0]) or {}
            kept.append({**p, "pid": pid, "spid": ok[0], "spids": ok, "ovr": best.get("ovr"),
                         "positions": best.get("positions") or []})
        return kept, dropped

    def _nation_ok(self, pid: int, name: str, nation: str | None) -> bool | None:
        """그 이름 · 국적으로 데이터센터 선수 검색에 이 선수(pid)가 나오나 — 모르면(데이터센터가 꺼졌거나 목록에 없는 국적) None."""
        if not nation or not name or not self.finder.enabled:
            return None
        try:
            if nation not in self.finder.options().get("nations", {}):
                return None
            return any(r["spid"] % 1_000_000 == pid for r in self.finder.search(Filters(name=name, nation=nation)))
        except Exception as exc:  # noqa: BLE001
            log.warning("동명이인 확인(이름 + 국적) 실패: %s", exc)
            return None

    def verify(self, kept: list[dict], f: Filters, condition: str) -> tuple[list[dict], list[dict], list[str]]:
        """조건에 적은 국적 · 클럽(· '팀컬러'라고 말한 팀컬러)은 데이터센터 선수 검색으로 확인한다. 한 번에 물어 200장 안에
        다 들어오면 그것으로, 넘으면 빠진 선수만 이름마다 따로 (VERIFY_MAX 명까지 — 그 너머는 '확인 못 함'으로 남긴다).
        (맞는 선수, 뺀 선수, 확인 못 한 선수 이름)."""
        facts = Filters(nation=f.nation, club=f.club, club_history=f.club_history,
                        teamcolor=f.teamcolor if "팀컬러" in condition.replace(" ", "") else None)
        if facts.empty() or not kept or not self.finder.enabled:
            return kept, [], []
        try:
            rows = self.finder.search(facts)
        except Exception as exc:  # noqa: BLE001 — 데이터센터가 안 되면 확인 없이
            log.warning("AI 로 찾기 — 국적 · 클럽 확인 실패: %s", exc)
            return kept, [], [p["name"] for p in kept]
        found = {r["spid"] % 1_000_000 for r in rows}
        complete = len(rows) < PAGE_LIMIT
        ok, bad, unsure, asked = [], [], [], 0
        for p in kept:
            if p["pid"] in found:
                ok.append(p)
            elif complete:
                bad.append({**p, "reason": " · ".join(facts.chips()[:-1]) + " 이(가) 아님"})
            elif asked < VERIFY_MAX:
                asked += 1
                name = self.catalog.by_spid.get(p["spid"], {}).get("name") or p["name"]
                try:
                    hit = any(r["spid"] % 1_000_000 == p["pid"] for r in self.finder.search(replace(facts, name=name)))
                except Exception:  # noqa: BLE001
                    hit = None
                if hit is None:
                    unsure.append(p["name"])
                    ok.append(p)
                elif hit:
                    ok.append(p)
                else:
                    bad.append({**p, "reason": " · ".join(facts.chips()[:-1]) + " 이(가) 아님"})
            else:
                unsure.append(p["name"])
                ok.append(p)
        return ok, bad, unsure

    def run(self, condition: str, f: Filters | None, progress=None) -> dict:
        """세 단계를 차례로 — progress(글) 로 지금 단계를 알린다. 1 · 2차(문서 · 명단)는 같은 조건이면 하루 동안 그대로 쓴다."""
        say = progress or (lambda _text: None)
        f = f or Filters()
        key = CACHE_KEY + re.sub(r"\s+", " ", condition.strip())
        stages = self.db.dc_get(key, CACHE_HOURS)
        if stages is None:
            say("1차 — 나무위키에서 찾는 중… (검색 → 명단이 있을 문서)")
            gathered = self.gather(condition)
            say(f"　나무위키 문서 {len(gathered['docs'])}개 · 검색 결과 {len(gathered['search'])}건"
                + (f" — {gathered['error']}" if gathered["error"] else ""))
            say("2차 — AI 가 조건에 맞는 선수를 모두 찾는 중… (LLM)")
            players = self.extract(condition, gathered)
            stages = {"docs": [{"title": d["title"], "url": d["url"]} for d in gathered["docs"]],
                      "search": len(gathered["search"]), "kind": gathered["kind"], "error": gathered["error"],
                      "players": players}
            if players:
                self.db.dc_put(key, stages)
        players = stages["players"]
        say(f"3차 — AI 가 찾은 {len(players)}명을 FC 온라인 DB(전체 카드)와 맞추는 중…")
        found, missing = self.match(players)
        kept, dropped = self.pick_cards(found, f)
        kept, wrong_facts, unsure = self.verify(kept, f, condition)
        kept.sort(key=lambda p: -(p.get("ovr") or 0))
        return {"condition": condition, "docs": stages["docs"], "search": stages["search"], "kind": stages["kind"],
                "error": stages["error"], "license": LICENSE, "listed": len(players),
                "cited": sum(1 for p in players if p["src"] == "나무위키"),
                "players": [{k: p[k] for k in ("name", "db_name", "pid", "spid", "spids", "ovr", "why", "src", "nation", "score")}
                            for p in kept],
                "missing": [{"name": p["name"], "why": p["why"]} for p in missing],
                "dropped": [{"name": p["name"], "reason": p["reason"]} for p in dropped + wrong_facts],
                "unsure": unsure}
