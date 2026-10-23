"""선수 카드 1장의 메타데이터 만들기 — 랭커 주 포지션 · 위키백과 매칭. (F2001)

build_items.py(미리 306장)와 앱의 '전체 카드에서 찾기'(88,359장 중 고른 카드를 그 자리에서)가 같은 코드를 쓴다.
"""

from __future__ import annotations

import collections
import difflib
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from data_prep.nexon_api import ACTION_IMAGE, FACE_IMAGE, NexonClient, split_spid
from data_prep.players import GROUP_OF, POSITION_GROUPS, season_short

log = logging.getLogger("fclab.catalog")

RECENT_DAYS = 45   # 이보다 오래된 랭커 집계는 '지금 쓰이는 포지션' 판단에서 뺀다
INTRO_CHARS = 4000   # 위키백과 첫 문단을 자르지 않고 담는다 (화면에 전부 보여 준다)
META_NAMES = ("spid", "seasonid", "spposition", "division")


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


# ── 넥슨: 카드 목록 · 랭커 스탯 ───────────────────────────────────

class CardCatalog:
    """FC 온라인 전체 카드(spid) · 시즌 · 포지션 · 등급 메타데이터.

    db 가 있으면 SQLite 에 둔 목록을 바로 읽는다 — 앱이 켜질 때 6.5MB 내려받기를 기다리지 않는다.
    refresh_in_background() 가 하루 지난 목록을 뒤에서 새로 받아, 새로 나온 카드를 '처음 본 날'과 함께 DB 에 더한다.
    db 가 없으면 cache_dir 의 디스크 사본(하루)을 쓴다.
    """

    def __init__(self, nexon: NexonClient, cache_dir: Path | None = None, db=None):
        self.nexon, self.cache_dir, self.db = nexon, cache_dir, db
        stored = db.catalog() if db else None
        if stored:
            meta, self.fetched_at = stored, stored["fetched_at"]
        else:
            meta = {name: nexon.meta(name, cache_dir) for name in META_NAMES}
            self.fetched_at = datetime.now().isoformat(timespec="seconds")
            if db:
                db.put_catalog(meta)
        self._build(meta)
        self._refreshing = False

    def _build(self, meta: dict) -> None:
        """색인을 다 만든 뒤 한꺼번에 바꿔 끼운다 — 뒤에서 새로 받는 동안에도 화면은 앞 목록을 그대로 쓴다."""
        cards = meta["spid"]
        by_pid = collections.defaultdict(list)
        for c in cards:
            by_pid[split_spid(c["id"])[1]].append(c["id"])
        self.seasons = {s["seasonId"]: s for s in meta["seasonid"]}
        self.position_names = {p["spposition"]: p["desc"] for p in meta["spposition"] if p["desc"] != "SUB"}
        self.divisions = {d["divisionId"]: d["divisionName"] for d in meta["division"]}
        self.cards_per_pid = collections.Counter(split_spid(c["id"])[1] for c in cards)
        self.by_spid = {c["id"]: c for c in cards}
        self.by_pid = by_pid
        self._names = [(_norm(c["name"]), c) for c in cards]   # 검색 때마다 정규화하지 않도록 미리
        self.cards = cards

    def refresh_in_background(self, max_age_hours: float = 24) -> None:
        """목록이 하루 지났으면 뒤에서 새로 받는다 — 새로 나온 카드는 DB 에 더하고 이 목록에도 바로 반영한다."""
        if not self.db or self._refreshing:
            return
        if datetime.now() - datetime.fromisoformat(self.fetched_at) < timedelta(hours=max_age_hours):
            return
        self._refreshing = True

        def run() -> None:
            try:
                meta = {name: self.nexon.meta(name, self.cache_dir, max_age_hours=0) for name in META_NAMES}
                new = self.db.put_catalog(meta)
                self._build(meta)
                self.fetched_at = datetime.now().isoformat(timespec="seconds")
                if new:
                    log.info("새로 나온 카드 %d장을 목록에 더했습니다", len(new))
            except Exception as exc:  # noqa: BLE001 — 못 받으면 지금 목록으로 계속, 다음에 다시
                log.warning("카드 목록 새로 받기 실패: %s", exc)
            finally:
                self._refreshing = False

        threading.Thread(target=run, name="catalog-refresh", daemon=True).start()

    def cards_of(self, pid: int) -> list[int]:
        """한 선수의 모든 카드 spid — 최신 시즌(시즌 ID 가 큰 것)부터."""
        return sorted(self.by_pid.get(pid, []), key=lambda s: -split_spid(s)[0])

    def basic_player(self, spid: int) -> dict:
        """네트워크 없이 바로 만드는 카드 정보(이름 · 시즌 · 이미지 주소). 랭커 스탯 · 위키 소개는 enrich() 로 채운다."""
        player = self.make_player(spid, [], date.today(), check_image=False)
        player["lite"] = True
        return player

    def season_name(self, spid: int) -> str:
        season_id = split_spid(spid)[0]
        return self.seasons.get(season_id, {}).get("className", str(season_id))

    def search(self, query: str, limit: int = 30) -> list[dict]:
        """이름에 검색어가 들어간 카드. 최신 시즌(시즌 ID 가 큰 것)부터."""
        q = _norm(query)
        if len(q) < 2:
            return []
        hits = [(name, c) for name, c in self._names if q in name]
        hits.sort(key=lambda nc: (nc[0] != q, -split_spid(nc[1]["id"])[0]))   # 이름이 딱 맞는 것 → 최신 시즌 순
        hits = [c for _, c in hits]
        return [
            {"spid": c["id"], "name": c["name"], "season_short": season_short(self.season_name(c["id"]))}
            for c in hits[:limit]
        ]

    def ranker_rows(self, spids: list[int], matchtype: int) -> dict[int, list[dict]]:
        """카드마다 28개 포지션의 랭커 스탯 행."""
        pairs = [(spid, po) for spid in spids for po in self.position_names]
        by_card = collections.defaultdict(list)
        for r in self.nexon.ranker_stats(pairs, matchtype=matchtype):
            s = r["status"]
            by_card[r["spid"]].append({
                "position": self.position_names[r["spPosition"]],
                "match_count": s["matchCount"],
                "date": datetime.fromisoformat(r["createDate"]).date(),
                "goal": s["goal"], "assist": s["assist"], "shoot": s["shoot"], "effective_shoot": s["effectiveShoot"],
                "pass_try": s["passTry"], "pass_success": s["passSuccess"],
                "dribble_try": s["dribbleTry"], "dribble_success": s["dribbleSuccess"],
                "tackle": s["tackle"], "block": s["block"],
            })
        return by_card

    def ranker_at(self, pairs: list[tuple[int, str]], matchtype: int) -> dict[int, dict]:
        """(카드, 포지션 이름) 쌍마다 그 포지션의 랭커 기록 한 줄 — {spid: ranker} (선수의 ranker 와 같은 모양, 날짜는 글자).
        카드마다 대표 포지션 한 칸만 묻는다 (28칸을 다 묻는 ranker_rows 의 28분의 1)."""
        number = {name: no for no, name in self.position_names.items()}
        asked = [(spid, number[name]) for spid, name in pairs if name in number]
        out = {}
        for r in self.nexon.ranker_stats(asked, matchtype=matchtype):
            s = r["status"]
            out[r["spid"]] = {
                "position": self.position_names[r["spPosition"]], "match_count": s["matchCount"],
                "date": datetime.fromisoformat(r["createDate"]).date().isoformat(),
                "goal": s["goal"], "assist": s["assist"], "shoot": s["shoot"], "effective_shoot": s["effectiveShoot"],
                "pass_try": s["passTry"], "pass_success": s["passSuccess"],
                "dribble_try": s["dribbleTry"], "dribble_success": s["dribbleSuccess"],
                "tackle": s["tackle"], "block": s["block"],
            }
        return out

    def make_player(self, spid: int, rows: list[dict], newest: date, check_image: bool = True) -> dict:
        season_id, pid = split_spid(spid)
        season = self.seasons.get(season_id, {})
        group, positions, ranker = main_position(rows, newest)
        action, face = ACTION_IMAGE.format(spid=spid), FACE_IMAGE.format(pid=pid)
        if check_image:
            image = action if NexonClient.image_exists(action) else (face if NexonClient.image_exists(face) else "")
        else:
            image = action   # 바로 띄울 때는 확인 요청을 기다리지 않는다 — 화면이 얼굴 이미지로 대신 보여 준다
        return {
            "player_id": str(spid),
            "spid": spid,
            "pid": pid,
            "name": self.by_spid[spid]["name"],
            "season_id": season_id,
            "season": season.get("className", str(season_id)),
            "season_short": season_short(season.get("className", str(season_id))),
            "season_img": season.get("seasonImg", ""),
            "image": image,
            "face": face,
            "card_count": self.cards_per_pid[pid],
            "position_group": group,
            "positions": positions,
            "ranker": ranker,
            # 포지션별 랭커 사용 (화면의 '랭커는 이 카드를 어디에 쓰나') — 이미 받은 기록이라 추가 호출 없이 담는다
            "ranker_positions": compact_positions(rows),
            "wiki_title": None,
            "wiki_url": None,
            "intro": "",
        }


def compact_positions(rows: list[dict]) -> list[dict]:
    """포지션별 랭커 기록을 화면에 필요한 만큼만 (JSON 에 담을 수 있게 날짜는 문자열)."""
    return [{"position": r["position"], "match_count": r["match_count"], "date": r["date"].isoformat(),
             "goal": r["goal"], "assist": r["assist"]} for r in rows]


def main_position(rows: list[dict], newest: date) -> tuple[str, list[str], dict | None]:
    """랭커 스탯 행들 → (포지션 그룹, 많이 쓴 포지션 최대 3개, 대표 포지션의 스탯).

    최근 45일 안의 집계만 보고, 포지션 그룹별 경기 수 합이 가장 큰 그룹을 고른다.
    (matchCount 는 최근 최대 20경기라 20 에서 멈춘다 — 인기 지표로 쓰지 않는다)
    """
    if not rows:
        return "", [], None
    recent = [r for r in rows if r["date"] >= newest - timedelta(days=RECENT_DAYS)] or rows
    score = collections.Counter()
    for r in recent:
        score[GROUP_OF.get(r["position"], "")] += r["match_count"]
    groups = list(POSITION_GROUPS)
    group = max(score, key=lambda g: (score[g], -groups.index(g) if g in groups else -99))
    canonical = POSITION_GROUPS.get(group, [])
    in_group = sorted(
        (r for r in recent if GROUP_OF.get(r["position"]) == group),
        key=lambda r: (-r["match_count"], canonical.index(r["position"]) if r["position"] in canonical else 99),
    )
    best = in_group[0]
    return group, [r["position"] for r in in_group[:3]], {**best, "date": best["date"].isoformat()}


# ── 위키백과: 카드 이름 → 선수 문서 ─────────────────────────────────

INITIAL = re.compile(r"^([A-Z])\.\s*")
LATIN_NAME = re.compile(r"[:(]\s*([A-Z][A-Za-zÀ-ÿ'\-]+(?:\s+[A-Za-zÀ-ÿ'\-\"]+)*)")   # 첫 괄호 속 원어 이름
PLAYER_WORDS = ("선수", "골키퍼", "공격수", "미드필더", "수비수")   # '선수' 대신 포지션만 적힌 문서가 있다 (레프 야신)
SIMILAR = 0.65   # 표기 차이 '멘데스 ↔ 멘드스'(0.67)는 받고, 다른 사람 '고츠 ↔ 고츠마나우'(0.57)는 버린다
BIRTH_YEAR = re.compile(r"(\d{4})년")
DIED = re.compile(r"\d{4}년 \d{1,2}월 \d{1,2}일\s*[~–-]\s*\d{4}년")


def _last_word(text: str) -> str:
    words = re.sub(r"\s*\(.*?\)\s*", " ", text).split()
    return _norm(words[-1]) if words else ""


def _end_words(text: str) -> list[str]:
    """제목의 첫 단어와 마지막 단어 — 성이 앞에 오는 이름(헝가리 · 동아시아: 소보슬러이 도미니크)도 맞추려고."""
    words = re.sub(r"\s*\(.*?\)\s*", " ", text).split()
    return [_norm(words[0]), _norm(words[-1])] if words else []


def _surname(name: str) -> str:
    words = INITIAL.sub("", name).split()
    return _norm(words[-1]) if words else ""


def plausible(name: str, info: dict | None, current: bool, searched: bool) -> str | None:
    """위키 문서가 이 카드의 선수가 맞는지 확인한다. 맞으면 None, 아니면 버린 이유.

    동명이인을 막는 규칙 — 실제로 나온 오매칭에서 만들었다.
      L. 수아레스(26 TOTS) → 1935년생 '루이스 수아레스 미라몬테스'   (현역 카드인데 옛 선수, 성 불일치)
      카를루스 아우베르투  → 감독 '카를루스 아우베르투 파헤이라'        (선수 문서가 아님)
      M. 상가레            → '이브라힘 상가레'                         (이니셜 M ≠ I)
      M. 고츠              → '샤르헤이 고츠마나우'                     (성 불일치)
    """
    if not info or info["disambiguation"]:
        return "문서 없음 · 동음이의"
    intro = info["intro"][:800]   # 발음 표기가 길어 '선수'가 뒤에 나오는 문서가 있다 (예: 뤼트 휠릿)
    if "축구" not in intro or not any(word in intro for word in PLAYER_WORDS):
        return "축구 선수 문서가 아님"
    initial = INITIAL.match(name)
    latin = LATIN_NAME.search(intro)
    if initial and latin:
        # 원어 이름의 어느 단어든 이니셜로 시작하면 된다 (헝가리식 '성 이름' 순서: Szoboszlai Dominik)
        words = [w.strip('"') for w in latin.group(1).split()]
        if not any(w.upper().startswith(initial.group(1)) for w in words):
            return f"이니셜 불일치 ({initial.group(1)} ≠ {words[0]})"
    if searched and len(name.split()) > 1:
        # 표기 차이(팀버 ↔ 팀버르)는 받아들이고, 다른 사람(고츠 ↔ 고츠마나우)은 버린다.
        # '카를루스 아우베르투' → '카를루스 아우베르투 토히스'처럼 카드 이름 전체로 시작하면 받아들인다.
        similar = any(difflib.SequenceMatcher(None, word, _surname(name)).ratio() >= SIMILAR
                      for word in _end_words(info["title"]))
        if not similar and not _norm(info["title"]).startswith(_norm(name)):
            return "성 불일치"
    if current:
        born = BIRTH_YEAR.search(intro)
        if (born and int(born.group(1)) < 1980) or DIED.search(intro):
            return "현역 카드인데 옛 선수"
    return None


def attach_wikipedia(players: list[dict], wiki, on_progress=None) -> list[tuple[str, str, str]]:
    """선수 이름 → 한국어 위키백과 문서. ① 이름을 제목으로 바로 열어 보고 ② 안 되면 검색한다.

    돌려주는 값: 버린 후보 (카드 이름, 문서 제목, 이유) — 매칭 품질을 사람이 확인하는 용도.
    """
    progress = on_progress or (lambda _message: None)
    current = {p["name"]: "ICON" not in p["season"] for p in players}
    names = sorted(current)
    found: dict[str, dict] = {}
    dropped: list[tuple[str, str, str]] = []

    for start in range(0, len(names), 20):
        for name, info in wiki.fetch_intros(names[start : start + 20]).items():
            reason = plausible(name, info, current[name], searched=False)
            if reason is None:
                found[name] = info
            elif info:
                dropped.append((name, info["title"], reason))
        progress(f"위키백과 제목 확인 {min(start + 20, len(names))}/{len(names)} — 찾음 {len(found)}")

    missing = [n for n in names if n not in found]
    guesses: dict[str, list[str]] = {}
    for i, name in enumerate(missing, start=1):
        surname = _surname(name)
        hits = wiki.search(f"{name} 축구 선수", limit=5) + wiki.search(INITIAL.sub("", name), limit=5)
        guesses[name] = list(dict.fromkeys(
            r["title"] for r in hits
            if surname and (surname in _norm(r["title"])
                            or difflib.SequenceMatcher(None, _last_word(r["title"]), surname).ratio() >= 0.6)
        ))
        if i % 10 == 0 or i == len(missing):
            progress(f"위키백과 검색 {i}/{len(missing)}")
    titles = sorted({t for ts in guesses.values() for t in ts})
    intros: dict[str, dict | None] = {}
    for start in range(0, len(titles), 20):
        intros.update(wiki.fetch_intros(titles[start : start + 20]))
    for name, candidates in guesses.items():
        for title in candidates:   # 검색 순위대로 — 규칙을 통과한 첫 문서
            reason = plausible(name, intros.get(title), current[name], searched=True)
            if reason is None:
                found[name] = intros[title]
                break
            dropped.append((name, title, reason))

    for p in players:
        info = found.get(p["name"])
        p["wiki_title"] = info["title"] if info else None
        p["wiki_url"] = info["url"] if info else None
        p["intro"] = info["intro"][:INTRO_CHARS] if info else ""
    return dropped


# ── 앱에서 그 자리에서 불러온 카드 ──────────────────────────────────

class ExtraPlayers:
    """'전체 카드에서 찾기'로 불러온 카드. storage/extra_players.json 에 남겨 다음 실행에도 쓴다."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def add(self, player: dict) -> None:
        players = [p for p in self.load() if p["player_id"] != player["player_id"]] + [player]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(players, ensure_ascii=False, indent=1), encoding="utf-8")


def load_card(spid: int, catalog: CardCatalog, wiki, matchtype: int, wiki_hint: dict | None = None) -> dict:
    """카드 1장을 완성한다: 랭커 스탯(요청 1회) → 주 포지션 → 위키백과 매칭.

    wiki_hint: 같은 선수(pid)의 다른 카드가 이미 위키 문서를 찾았으면 그 결과를 그대로 쓴다 (위키 호출 0회).
    """
    rows = catalog.ranker_rows([spid], matchtype).get(spid, [])
    newest = max((r["date"] for r in rows), default=date.today())
    player = catalog.make_player(spid, rows, newest, check_image=False)
    if wiki_hint and wiki_hint.get("wiki_title"):
        player.update({k: wiki_hint[k] for k in ("wiki_title", "wiki_url", "intro")})
    else:
        attach_wikipedia([player], wiki)
    return player
