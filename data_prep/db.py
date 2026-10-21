"""SQLite 저장소 — 앱에서 분석한 카드 · 랭커 포지션별 기록 · AI 캐시 · 즐겨찾기. (NF2001 · NF2002)

    storage/fclab.db

JSON 파일을 통째로 읽고 쓰던 것을 바꿨다.
- 백그라운드 미리 불러오기(services/prefetch.py)의 여러 스레드가 동시에 써도 안전하다 (WAL, 연결 하나를 잠금으로 차례로).
- 필요한 한 건만 읽고 쓴다 (선수 1명, 카드 1장의 랭커 기록).
- 연결은 열어 둔 채 쓴다. 호출마다 열고 닫으면 마지막 연결이 닫힐 때마다 WAL 체크포인트가 돌아,
  느린 디스크에서는 쓰기 한 번에 0.1초씩 걸렸다 (열어 두면 0.01초 안쪽).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY, pid INTEGER, name TEXT, data TEXT NOT NULL, updated_at TEXT);
CREATE INDEX IF NOT EXISTS players_pid ON players(pid);
CREATE TABLE IF NOT EXISTS ranker_rows (
    spid INTEGER, position TEXT, data TEXT NOT NULL, PRIMARY KEY (spid, position));
CREATE TABLE IF NOT EXISTS ranker_fetched (spid INTEGER PRIMARY KEY, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS ai_cache (key TEXT PRIMARY KEY, value TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS favorites (player_id TEXT PRIMARY KEY, added_at TEXT);
CREATE TABLE IF NOT EXISTS account (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS gk_games (
    match_id TEXT, side INTEGER, spid INTEGER, on_target INTEGER, goals INTEGER, match_date TEXT,
    PRIMARY KEY (match_id, side));
CREATE TABLE IF NOT EXISTS market_prices (
    spid INTEGER, grade INTEGER, current INTEGER, fetched_at TEXT, PRIMARY KEY (spid, grade));
CREATE TABLE IF NOT EXISTS market_history (
    spid INTEGER, grade INTEGER, day TEXT, value INTEGER, PRIMARY KEY (spid, grade, day));
CREATE TABLE IF NOT EXISTS market_snapshots (
    spid INTEGER, grade INTEGER, at TEXT, value INTEGER, PRIMARY KEY (spid, grade, at));
CREATE TABLE IF NOT EXISTS market_history_fetched (spid INTEGER, grade INTEGER, fetched_at TEXT, PRIMARY KEY (spid, grade));
CREATE TABLE IF NOT EXISTS market_watch (spid INTEGER PRIMARY KEY, viewed_at TEXT);
CREATE TABLE IF NOT EXISTS card_list (spid INTEGER PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS card_info (spid INTEGER PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS search_log (query TEXT PRIMARY KEY, count INTEGER, last_at TEXT);
CREATE TABLE IF NOT EXISTS images (url TEXT PRIMARY KEY, file TEXT, status INTEGER, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS catalog_cards (spid INTEGER PRIMARY KEY, name TEXT, first_seen TEXT);
CREATE TABLE IF NOT EXISTS catalog_meta (name TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS dc_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS teamcolors (
    id INTEGER PRIMARY KEY, name TEXT, kind TEXT, data TEXT NOT NULL, detail TEXT, first_seen TEXT, updated_at TEXT,
    detail_at TEXT);
CREATE TABLE IF NOT EXISTS card_stats (
    spid INTEGER PRIMARY KEY, season INTEGER, name TEXT, ovr INTEGER, pay INTEGER, positions TEXT, stats TEXT NOT NULL,
    updated_at TEXT, foot TEXT);
CREATE INDEX IF NOT EXISTS card_stats_season ON card_stats(season);
CREATE TABLE IF NOT EXISTS card_pay_history (spid INTEGER, at TEXT, pay INTEGER, before INTEGER, PRIMARY KEY (spid, at));
CREATE TABLE IF NOT EXISTS stats_crawl (
    part TEXT PRIMARY KEY, season INTEGER, filter TEXT NOT NULL, cards INTEGER, passes INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS squads (name TEXT PRIMARY KEY, data TEXT NOT NULL, saved_at TEXT);
"""
STAT_PASSES = 9          # 선수 검색 한 번에 능력치 4칸 — 36칸(능력치 34 + 키 · 몸무게)을 다 받으려면 9번
GK_KEEP_DAYS = 30       # 넥슨 약관 — 저장한 데이터는 30일 안의 것만 쓴다
MARKET_KEEP_DAYS = 30   # 30일 넘게 다시 받지 않은 카드의 시세는 지운다 (계속 보는 카드는 그때마다 갱신된다)


class FcDb:
    def __init__(self, path: str | Path, ranker_ttl_hours: float = 24):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ranker_ttl = timedelta(hours=ranker_ttl_hours)
        self._lock = threading.RLock()
        self._players: list[dict] | None = None   # all_players 의 메모리 사본 (put_player 가 비운다)
        self._db = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")   # WAL 에서는 이걸로도 데이터가 깨지지 않는다
            conn.executescript(SCHEMA)
            columns = {c[1] for c in conn.execute("PRAGMA table_info(card_stats)")}
            if "foot" not in columns:   # 처음 만든 표에는 주발 칸이 없었다
                conn.execute("ALTER TABLE card_stats ADD COLUMN foot TEXT")
        self._migrate_json()

    @contextmanager
    def _conn(self):
        """열어 둔 연결 하나를 스레드끼리 차례로 쓴다. 블록이 끝나면 커밋, 예외면 되돌린다."""
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    # ── 선수 (앱에서 분석한 카드) ───────────────────────────────────

    def get_player(self, player_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT data FROM players WHERE player_id = ?", (str(player_id),)).fetchone()
        return json.loads(row[0]) if row else None

    def player_with_wiki(self, pid: int) -> dict | None:
        """같은 선수(pid)의 다른 카드 중 위키 문서를 찾은 것 — 위키 매칭을 다시 하지 않으려고."""
        with self._conn() as conn:
            for (data,) in conn.execute("SELECT data FROM players WHERE pid = ?", (pid,)):
                player = json.loads(data)
                if player.get("wiki_title"):
                    return player
        return None

    def put_player(self, player: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO players (player_id, pid, name, data, updated_at) VALUES (?, ?, ?, ?, ?)",
                (player["player_id"], player["pid"], player["name"], json.dumps(player, ensure_ascii=False),
                 datetime.now().isoformat(timespec="seconds")),
            )
            self._players = None

    def all_players(self) -> list[dict]:
        """앱에서 분석한 카드 전부 — 새로 저장된 카드가 없으면 앞서 읽은 목록을 그대로 (화면마다 JSON 을 다시 풀지 않게)."""
        with self._conn() as conn:
            if self._players is None:
                self._players = [json.loads(d) for (d,) in conn.execute("SELECT data FROM players ORDER BY name")]
            return list(self._players)

    # ── 랭커 포지션별 기록 ───────────────────────────────────────

    def ranker_rows(self, spid: int) -> list[dict] | None:
        """받아 둔 기록 (하루 안). 받은 적이 없거나 오래됐으면 None — 기록이 0건인 카드는 [] 를 돌려준다."""
        with self._conn() as conn:
            fetched = conn.execute("SELECT fetched_at FROM ranker_fetched WHERE spid = ?", (spid,)).fetchone()
            if not fetched or datetime.now() - datetime.fromisoformat(fetched[0]) > self.ranker_ttl:
                return None
            rows = [json.loads(d) for (d,) in conn.execute("SELECT data FROM ranker_rows WHERE spid = ?", (spid,))]
        for r in rows:
            r["date"] = date.fromisoformat(r["date"])
        return rows

    def fresh_spids(self, spids: list[int]) -> set[int]:
        cutoff = (datetime.now() - self.ranker_ttl).isoformat(timespec="seconds")
        with self._conn() as conn:
            return {s for (s,) in conn.execute("SELECT spid FROM ranker_fetched WHERE fetched_at >= ?", (cutoff,))} & set(spids)

    def put_ranker_rows(self, by_card: dict[int, list[dict]], spids: list[int]) -> None:
        """spids 전부를 '받았음'으로 남긴다 (기록이 없는 카드도 다시 묻지 않게)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            for spid in spids:
                conn.execute("DELETE FROM ranker_rows WHERE spid = ?", (spid,))
                for r in by_card.get(spid, []):
                    conn.execute("INSERT OR REPLACE INTO ranker_rows (spid, position, data) VALUES (?, ?, ?)",
                                 (spid, r["position"], json.dumps({**r, "date": r["date"].isoformat()})))
                conn.execute("INSERT OR REPLACE INTO ranker_fetched (spid, fetched_at) VALUES (?, ?)", (spid, now))

    # ── AI 캐시 · 즐겨찾기 ──────────────────────────────────────

    def ai_get(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM ai_cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def ai_put(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO ai_cache (key, value, created_at) VALUES (?, ?, ?)",
                         (key, value, datetime.now().isoformat(timespec="seconds")))

    def log_search(self, query: str) -> None:
        """AI 검색 조건을 센다 — 홈의 추천 검색어가 자주 찾는 조건을 더 자주 보여 주도록."""
        with self._conn() as conn:
            conn.execute("INSERT INTO search_log (query, count, last_at) VALUES (?, 1, ?) "
                         "ON CONFLICT(query) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
                         (query.strip(), datetime.now().isoformat(timespec="seconds")))

    def search_counts(self, limit: int = 50) -> dict[str, int]:
        with self._conn() as conn:
            return dict(conn.execute("SELECT query, count FROM search_log ORDER BY count DESC, last_at DESC LIMIT ?",
                                     (limit,)))

    def favorite_ids(self) -> list[str]:
        with self._conn() as conn:
            return [pid for (pid,) in conn.execute("SELECT player_id FROM favorites ORDER BY added_at")]

    def toggle_favorite(self, player_id: str) -> None:
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM favorites WHERE player_id = ?", (player_id,)).fetchone():
                conn.execute("DELETE FROM favorites WHERE player_id = ?", (player_id,))
            else:
                conn.execute("INSERT INTO favorites (player_id, added_at) VALUES (?, ?)",
                             (player_id, datetime.now().isoformat(timespec="seconds")))

    # ── 골키퍼 선방 재료 (경기 상세에서) ──────────────────────────────
    # 랭커 API 에 선방이 없어, 내 기록 · 내 스쿼드가 이미 받은 경기 상세에서 양 팀 골키퍼의 상대 유효슈팅 · 실점을 모은다.
    # 유저 식별자(ouid · 닉네임)는 저장하지 않는다.

    def put_gk_games(self, rows: list[dict]) -> None:
        cutoff = (date.today() - timedelta(days=GK_KEEP_DAYS)).isoformat()
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO gk_games (match_id, side, spid, on_target, goals, match_date) VALUES (?, ?, ?, ?, ?, ?)",
                [(r["match_id"], r["side"], r["spid"], r["on_target"], r["goals"], r["match_date"]) for r in rows])
            conn.execute("DELETE FROM gk_games WHERE match_date < ?", (cutoff,))

    def gk_saves(self) -> dict[int, dict]:
        """카드별 최근 30일 합계 {spid: {games, on_target, goals}}."""
        cutoff = (date.today() - timedelta(days=GK_KEEP_DAYS)).isoformat()
        with self._conn() as conn:
            rows = conn.execute("SELECT spid, COUNT(*), SUM(on_target), SUM(goals) FROM gk_games "
                                "WHERE match_date >= ? GROUP BY spid", (cutoff,)).fetchall()
        return {spid: {"games": n, "on_target": shots, "goals": goals} for spid, n, shots, goals in rows}

    # ── 강화 단계별 시세 (데이터센터) ─────────────────────────────────
    # 현재가: 선수 검색 한 번에 그 선수 카드 전부의 강화 1~13 현재가가 온다 (put_listing). 바뀐 값만 시각과 함께
    #   market_snapshots 에 쌓는다 — 시세가 바뀐 기록이 시간별로 남고, 화면은 마지막 값을 바로 그린다.
    # 일별 시세: 날짜 단위로 계속 쌓는다. 앱이 꺼져 있던 동안의 날짜도, 다시 켜서 받은 응답(최근 약 4개월)에
    #   들어 있으면 그대로 채워진다 — put_price 가 새로 채운 날짜 수를 돌려준다.

    @staticmethod
    def _put_current(conn, spid: int, grade: int, value: int | None, now: str) -> None:
        conn.execute("INSERT OR REPLACE INTO market_prices (spid, grade, current, fetched_at) VALUES (?, ?, ?, ?)",
                     (spid, grade, value, now))
        if value is None:
            return
        last = conn.execute("SELECT value FROM market_snapshots WHERE spid = ? AND grade = ? ORDER BY at DESC LIMIT 1",
                            (spid, grade)).fetchone()
        if not last or last[0] != value:
            conn.execute("INSERT OR REPLACE INTO market_snapshots (spid, grade, at, value) VALUES (?, ?, ?, ?)",
                         (spid, grade, now, value))

    def put_listing(self, rows: list[dict]) -> None:
        """데이터센터 선수 검색 결과 — 카드마다 강화 1~13 현재가 · OVR · 급여 · 포지션별 OVR."""
        now = datetime.now().isoformat(timespec="milliseconds")   # 같은 초에 두 번 바뀌어도 기록이 겹치지 않게
        with self._conn() as conn:
            for row in rows:
                conn.execute("INSERT OR REPLACE INTO card_list (spid, data, fetched_at) VALUES (?, ?, ?)",
                             (row["spid"], json.dumps(row, ensure_ascii=False), now))
                for grade, value in row["prices"].items():
                    self._put_current(conn, row["spid"], int(grade), value, now)
            self._merge_stats(conn, rows, now)

    def listing(self, spids: list[int]) -> dict[int, dict]:
        """선수 검색에서 받아 둔 카드 정보 {spid: {ovr, pay, positions, prices, rating, fetched_at}}."""
        if not spids:
            return {}
        marks = ",".join("?" * len(spids))
        with self._conn() as conn:
            rows = conn.execute(f"SELECT spid, data, fetched_at FROM card_list WHERE spid IN ({marks})", list(spids))
            return {spid: {**json.loads(data), "fetched_at": at} for spid, data, at in rows}

    def put_price(self, spid: int, grade: int, data: dict | None) -> int:
        now = datetime.now().isoformat(timespec="milliseconds")
        with self._conn() as conn:
            last = conn.execute("SELECT MAX(day) FROM market_history WHERE spid = ? AND grade = ?", (spid, grade)).fetchone()[0]
            self._put_current(conn, spid, grade, data["current"] if data else None, now)
            points = (data or {}).get("history") or []
            conn.executemany("INSERT OR REPLACE INTO market_history (spid, grade, day, value) VALUES (?, ?, ?, ?)",
                             [(spid, grade, d, v) for d, v in points])
            conn.execute("INSERT OR REPLACE INTO market_history_fetched (spid, grade, fetched_at) VALUES (?, ?, ?)",
                         (spid, grade, now))
            self._drop_old_prices(conn)
        return sum(1 for d, _ in points if last and d > last)

    def prices(self, spid: int, max_age_hours: float, current_max_age_minutes: float = 30) -> dict[int, dict]:
        """이 카드의 강화 단계별 시세 — 받은 적 있는 단계만.

        {grade: {current, fetched_at, fresh (현재가가 current_max_age 안), history, history_fresh (일별 시세가 max_age 안),
                 prev: 바로 전에 기록된 다른 값 {value, at} 또는 None}}
        """
        now = datetime.now()
        current_cut = (now - timedelta(minutes=current_max_age_minutes)).isoformat(timespec="seconds")
        history_cut = (now - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            snaps = conn.execute("SELECT grade, current, fetched_at FROM market_prices WHERE spid = ?", (spid,)).fetchall()
            points = conn.execute("SELECT grade, day, value FROM market_history WHERE spid = ? ORDER BY day", (spid,)).fetchall()
            fetched = dict(conn.execute("SELECT grade, fetched_at FROM market_history_fetched WHERE spid = ?", (spid,)))
            changes = conn.execute("SELECT grade, at, value FROM market_snapshots WHERE spid = ? ORDER BY at DESC", (spid,)).fetchall()
        out = {g: {"current": cur, "fetched_at": at, "fresh": at >= current_cut, "history": [],
                   "history_fresh": fetched.get(g, "") >= history_cut, "prev": None} for g, cur, at in snaps}
        for g, d, v in points:
            if g in out:
                out[g]["history"].append([d, v])
        seen: dict[int, int] = {}
        for g, at, v in changes:   # 최신부터 — 두 번째로 나온 값이 바로 전 시세
            seen[g] = seen.get(g, 0) + 1
            if seen[g] == 2 and g in out:
                out[g]["prev"] = {"value": v, "at": at}
        return out

    def watch(self, spids: list[int]) -> None:
        """사용자가 본 카드 — 백그라운드가 이 카드들의 시세를 주기적으로 새로 받는다."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO market_watch (spid, viewed_at) VALUES (?, ?)", [(s, now) for s in spids])

    def watched(self, days: int = 7, limit: int = 60) -> list[int]:
        """최근 본 카드 + 즐겨찾기 — 최근 본 것부터."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._conn() as conn:
            seen = [s for (s,) in conn.execute("SELECT spid FROM market_watch WHERE viewed_at >= ? ORDER BY viewed_at DESC",
                                               (cutoff,))]
            favorites = [int(p) for (p,) in conn.execute("SELECT player_id FROM favorites") if str(p).isdigit()]
        return list(dict.fromkeys(seen + favorites))[:limit]

    def stale_current(self, spids: list[int], max_age_minutes: float) -> list[int]:
        """현재가가 없거나 max_age 보다 오래된 카드."""
        cutoff = (datetime.now() - timedelta(minutes=max_age_minutes)).isoformat(timespec="seconds")
        with self._conn() as conn:
            fresh = {s for (s,) in conn.execute("SELECT DISTINCT spid FROM market_prices WHERE fetched_at >= ?", (cutoff,))}
        return [s for s in spids if s not in fresh]

    def stale_histories(self, max_age_hours: float, limit: int) -> list[tuple[int, int]]:
        """일별 시세를 다시 받아야 할 (카드, 강화) — 30일 안에 받은 적 있고 max_age 보다 오래된 것, 최근에 받은 것부터.

        30일 넘게 다시 받지 않은 카드는 따라잡지 않고 지운다 (약관 — 30일 안의 데이터만).
        """
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            self._drop_old_prices(conn)
            return [(s, g) for s, g in conn.execute(
                "SELECT spid, grade FROM market_history_fetched WHERE fetched_at < ? ORDER BY fetched_at DESC LIMIT ?",
                (cutoff, limit))]

    @staticmethod
    def _drop_old_prices(conn) -> None:
        stale = (datetime.now() - timedelta(days=MARKET_KEEP_DAYS)).isoformat(timespec="seconds")
        for table in ("market_history", "market_snapshots", "market_history_fetched"):
            conn.execute(f"DELETE FROM {table} WHERE (spid, grade) IN "
                         "(SELECT spid, grade FROM market_prices WHERE fetched_at < ?)", (stale,))
        conn.execute("DELETE FROM market_prices WHERE fetched_at < ?", (stale,))
        conn.execute("DELETE FROM card_list WHERE fetched_at < ?", (stale,))

    # ── 전체 카드 목록 (넥슨 메타데이터) ─────────────────────────────
    # 앱은 여기서 바로 읽고, 하루가 지나면 뒤에서 새 목록을 받아 새로 나온 카드만 '처음 본 날'과 함께 더한다.

    def catalog(self) -> dict | None:
        """{"spid": [{id, name}], "seasonid": [...], "spposition": [...], "division": [...], "fetched_at": ...} 또는 None."""
        with self._conn() as conn:
            cards = [{"id": spid, "name": name} for spid, name in conn.execute("SELECT spid, name FROM catalog_cards")]
            meta = {name: (json.loads(data), at) for name, data, at in conn.execute("SELECT name, data, fetched_at FROM catalog_meta")}
        if not cards or not meta:
            return None
        return {"spid": cards, **{name: data for name, (data, _) in meta.items()},
                "fetched_at": min(at for _, at in meta.values())}

    def put_catalog(self, meta: dict) -> list[int]:
        """받은 메타데이터를 저장하고, 전에 없던 카드(새로 나온 카드)의 spid 를 돌려준다. 처음 저장할 때는 빈 목록."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            known = {spid for (spid,) in conn.execute("SELECT spid FROM catalog_cards")}
            first = not known
            new = [c["id"] for c in meta["spid"] if c["id"] not in known]
            conn.executemany("INSERT INTO catalog_cards (spid, name, first_seen) VALUES (?, ?, ?) "
                             "ON CONFLICT(spid) DO UPDATE SET name = excluded.name",
                             [(c["id"], c["name"], "" if first else now) for c in meta["spid"]])
            conn.executemany("INSERT OR REPLACE INTO catalog_meta (name, data, fetched_at) VALUES (?, ?, ?)",
                             [(name, json.dumps(data, ensure_ascii=False), now) for name, data in meta.items() if name != "spid"])
        return [] if first else new

    def catalog_seasons(self) -> list[int]:
        with self._conn() as conn:
            return [s for (s,) in conn.execute("SELECT DISTINCT spid / 1000000 FROM catalog_cards")]

    def catalog_new_without_stats(self) -> dict[int, str]:
        """새로 나온 카드 중 능력치를 아직 못 받은 것 — {시즌: 그 카드를 처음 본 때(가장 늦은)}."""
        with self._conn() as conn:
            return dict(conn.execute(
                "SELECT spid / 1000000, MAX(first_seen) FROM catalog_cards WHERE first_seen != '' "
                "AND spid NOT IN (SELECT spid FROM card_stats) GROUP BY spid / 1000000"))

    def new_cards(self, days: int = 7) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM catalog_cards WHERE first_seen >= ?", (cutoff,)).fetchone()[0]

    # ── 데이터센터 목록 (AI 추천 스쿼드) — 데일리 차트 · 팀컬러 후보 · 클럽/국적 목록 ──────────

    def dc_get(self, key: str, max_age_hours: float):
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            row = conn.execute("SELECT data FROM dc_cache WHERE key = ? AND fetched_at >= ?", (key, cutoff)).fetchone()
        return json.loads(row[0]) if row else None

    def dc_put(self, key: str, data) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO dc_cache (key, data, fetched_at) VALUES (?, ?, ?)",
                         (key, json.dumps(data, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    # ── 받아 둔 이미지 (services/images.py) ──────────────────────────

    def images(self) -> dict[str, tuple[str, int]]:
        with self._conn() as conn:
            return {url: (file, status) for url, file, status in conn.execute("SELECT url, file, status FROM images")}

    def put_image(self, url: str, file: str, status: int) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO images (url, file, status, fetched_at) VALUES (?, ?, ?, ?)",
                         (url, file, status, datetime.now().isoformat(timespec="seconds")))

    # ── 카드 상세 (데이터센터) — 특성 · 능력치 · 클럽 경력 ─────────────────────

    def put_card_info(self, spid: int, info: dict) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO card_info (spid, data, fetched_at) VALUES (?, ?, ?)",
                         (spid, json.dumps(info, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    def card_info(self, spids: list[int], max_age_days: float = 7) -> dict[int, dict]:
        """받아 둔 카드 상세 {spid: info} — max_age 보다 오래된 것은 빼고 (특성 · 능력치는 자주 바뀌지 않는다)."""
        if not spids:
            return {}
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        marks = ",".join("?" * len(spids))
        with self._conn() as conn:
            rows = conn.execute(f"SELECT spid, data FROM card_info WHERE fetched_at >= ? AND spid IN ({marks})",
                                [cutoff, *spids])
            return {spid: json.loads(data) for spid, data in rows}

    # ── 팀컬러 (데이터센터 팀컬러 목록) ──────────────────────────────
    # 목록을 받을 때마다 전에 없던 팀컬러를 '처음 본 날'과 함께 더한다. 단계별 조건 · 효과(detail)는 처음 쓸 때 받아 둔다.

    def put_teamcolors(self, rows: list[dict]) -> list[int]:
        """받은 팀컬러 목록을 저장하고 새로 생긴 팀컬러 번호를 돌려준다 (처음 저장할 때는 빈 목록)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            known = {i for (i,) in conn.execute("SELECT id FROM teamcolors")}
            conn.executemany(
                "INSERT INTO teamcolors (id, name, kind, data, first_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, data = excluded.data, "
                "updated_at = excluded.updated_at",
                [(r["id"], r["name"], r["kind"], json.dumps(r, ensure_ascii=False), now if known else "", now) for r in rows])
        return [r["id"] for r in rows if r["id"] not in known] if known else []

    def teamcolors(self) -> tuple[list[dict], str | None]:
        """저장한 팀컬러 전부 [{id, name, kind, ..., first_seen, detail}] 와 목록을 마지막으로 받은 때."""
        with self._conn() as conn:
            rows = conn.execute("SELECT data, detail, first_seen, updated_at FROM teamcolors").fetchall()
        out = [{**json.loads(data), "detail": json.loads(detail) if detail else None, "first_seen": first}
               for data, detail, first, _ in rows]
        return out, max((at for *_, at in rows), default=None)

    def put_teamcolor_detail(self, teamcolor_id: int, detail: dict) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE teamcolors SET detail = ?, detail_at = ? WHERE id = ?",
                         (json.dumps(detail, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"), teamcolor_id))

    def teamcolor_details(self, ids: list[int], max_age_days: float = 30) -> dict[int, dict]:
        """받아 둔 단계별 조건 · 효과 {id: {name, description, levels, min_grade}} — max_age 안의 것만."""
        if not ids:
            return {}
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        marks = ",".join("?" * len(ids))
        with self._conn() as conn:
            rows = conn.execute(f"SELECT id, detail FROM teamcolors WHERE detail IS NOT NULL AND detail_at >= ? "
                                f"AND id IN ({marks})", [cutoff, *ids])
            return {i: json.loads(d) for i, d in rows}

    # ── 모든 카드의 능력치 · 급여 (데이터센터 선수 검색 — services/stats.py 가 채운다) ─────────────
    # 능력치는 받을 때마다 칸별로 합친다 (한 번에 4개씩 온다). 급여가 바뀌면(로스터 패치) card_pay_history 에 남긴다.
    # 시세를 받을 때 온 선수 검색 결과(put_listing)도 여기에 합친다 — 급여 · OVR 이 어디서 받든 최신이 되게.

    @staticmethod
    def _merge_stats(conn, rows: list[dict], now: str) -> None:
        for row in rows:
            old = conn.execute("SELECT pay, stats FROM card_stats WHERE spid = ?", (row["spid"],)).fetchone()
            stats = {**(json.loads(old[1]) if old else {}), **row.get("stats", {})}
            pay = row.get("pay")
            if old and pay is not None and old[0] is not None and old[0] != pay:
                conn.execute("INSERT OR REPLACE INTO card_pay_history (spid, at, pay, before) VALUES (?, ?, ?, ?)",
                             (row["spid"], now, pay, old[0]))
            conn.execute(
                "INSERT INTO card_stats (spid, season, name, ovr, pay, positions, stats, updated_at, foot) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(spid) DO UPDATE SET name = excluded.name, ovr = COALESCE(excluded.ovr, ovr), "
                "pay = COALESCE(excluded.pay, pay), positions = excluded.positions, stats = excluded.stats, "
                "updated_at = excluded.updated_at, foot = COALESCE(excluded.foot, foot)",
                (row["spid"], row["spid"] // 1_000_000, row.get("name"), row.get("ovr"), pay,
                 json.dumps(row.get("positions") or [], ensure_ascii=False), json.dumps(stats), now,
                 json.dumps(row["foot"]) if row.get("foot") else None))

    def put_card_stats(self, rows: list[dict]) -> None:
        with self._conn() as conn:
            self._merge_stats(conn, rows, datetime.now().isoformat(timespec="milliseconds"))

    def card_stats(self, spids: list[int]) -> dict[int, dict]:
        """{spid: {name, ovr, pay, positions, stats{능력치 코드: 값}, updated_at, pay_change: {before, at} 또는 None, foot}}."""
        if not spids:
            return {}
        marks = ",".join("?" * len(spids))
        with self._conn() as conn:
            rows = conn.execute(f"SELECT spid, name, ovr, pay, positions, stats, updated_at, foot FROM card_stats "
                                f"WHERE spid IN ({marks})", list(spids)).fetchall()
            changes = conn.execute(f"SELECT spid, before, at FROM card_pay_history WHERE spid IN ({marks}) ORDER BY at",
                                   list(spids)).fetchall()
        last = {spid: {"before": before, "at": at} for spid, before, at in changes}
        return {spid: {"name": name, "ovr": ovr, "pay": pay, "positions": json.loads(pos), "stats": json.loads(stats),
                       "updated_at": at, "pay_change": last.get(spid), "foot": json.loads(foot) if foot else None}
                for spid, name, ovr, pay, pos, stats, at, foot in rows}

    def stats_summary(self) -> dict:
        """모은 카드 수 · 시즌 수 · 능력치까지 다 모은 조각 수 · 최근 30일 급여가 바뀐 카드 수 (화면의 진행 상황)."""
        month = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        with self._conn() as conn:
            cards, seasons = conn.execute("SELECT COUNT(*), COUNT(DISTINCT season) FROM card_stats").fetchone()
            parts, done = conn.execute("SELECT COUNT(*), COALESCE(SUM(passes >= ?), 0) FROM stats_crawl",
                                       (STAT_PASSES,)).fetchone()
            changed = conn.execute("SELECT COUNT(DISTINCT spid) FROM card_pay_history WHERE at >= ?", (month,)).fetchone()[0]
        return {"cards": cards, "seasons": seasons, "parts": parts, "parts_done": done, "pay_changed_30d": changed}

    def crawl_parts(self, season: int | None = None) -> list[dict]:
        """능력치 모으기 조각 [{part, season, filter, cards, passes, updated_at}] — 조각 하나는 선수 검색 200장 안쪽."""
        sql = "SELECT part, season, filter, cards, passes, updated_at FROM stats_crawl"
        with self._conn() as conn:
            rows = conn.execute(sql + (" WHERE season = ?" if season is not None else ""),
                                (season,) if season is not None else ()).fetchall()
        return [{"part": p, "season": s, "filter": json.loads(f), "cards": c, "passes": n, "updated_at": at}
                for p, s, f, c, n, at in rows]

    def put_crawl_part(self, part: str, season: int, flt: dict, cards: int, passes: int) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO stats_crawl (part, season, filter, cards, passes, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (part, season, json.dumps(flt), cards, passes, datetime.now().isoformat(timespec="seconds")))

    def drop_crawl_parts(self, season: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM stats_crawl WHERE season = ?", (season,))

    # ── 저장한 스쿼드 ────────────────────────────────────────────

    def save_squad(self, name: str, data: dict) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO squads (name, data, saved_at) VALUES (?, ?, ?)",
                         (name, json.dumps(data, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    def squads(self) -> list[dict]:
        """저장한 스쿼드 — 최근에 저장한 것부터 [{name, data, saved_at}]."""
        with self._conn() as conn:
            return [{"name": n, "data": json.loads(d), "saved_at": at}
                    for n, d, at in conn.execute("SELECT name, data, saved_at FROM squads ORDER BY saved_at DESC")]

    def delete_squad(self, name: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM squads WHERE name = ?", (name,))

    # ── 연결한 계정 (닉네임) ────────────────────────────────────
    # 넥슨 Open API 에는 외부 서비스용 로그인이 없다. 닉네임 → ouid 로 공개 경기 기록만 본다. 비밀번호는 받지 않는다.

    def account(self) -> dict | None:
        with self._conn() as conn:
            rows = dict(conn.execute("SELECT key, value FROM account"))
        return rows if rows.get("nickname") else None

    def set_account(self, nickname: str | None, ouid: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM account")
            if nickname:
                conn.executemany("INSERT INTO account (key, value) VALUES (?, ?)",
                                 [("nickname", nickname), ("ouid", ouid or ""),
                                  ("linked_at", datetime.now().isoformat(timespec="seconds"))])

    # ── 예전 JSON 저장소에서 한 번 옮기기 ──────────────────────────

    def _migrate_json(self) -> None:
        base = self.path.parent
        sources = {
            "extra_players.json": lambda data: [self.put_player(p) for p in data],
            "ai_cache.json": lambda data: [self.ai_put(k, v) for k, v in data.items()],
            "favorites.json": lambda data: [self.toggle_favorite(pid) for pid in data if pid not in self.favorite_ids()],
        }
        for name, load in sources.items():
            path = base / name
            if path.exists():
                load(json.loads(path.read_text(encoding="utf-8")))
                path.rename(path.with_suffix(".json.migrated"))
