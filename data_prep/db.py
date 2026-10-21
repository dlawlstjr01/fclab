"""SQLite 저장소 — 앱에서 분석한 카드 · 랭커 포지션별 기록 · AI 캐시 · 즐겨찾기. (NF2001 · NF2002)

    storage/fclab.db

JSON 파일을 통째로 읽고 쓰던 것을 바꿨다.
- 백그라운드 미리 불러오기(services/prefetch.py)의 여러 스레드가 동시에 써도 안전하다 (WAL, 연결은 호출마다).
- 필요한 한 건만 읽고 쓴다 (선수 1명, 카드 1장의 랭커 기록).
"""

from __future__ import annotations

import json
import sqlite3
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
"""
GK_KEEP_DAYS = 30       # 넥슨 약관 — 저장한 데이터는 30일 안의 것만 쓴다
MARKET_KEEP_DAYS = 30   # 30일 넘게 다시 받지 않은 카드의 시세는 지운다 (계속 보는 카드는 그때마다 갱신된다)


class FcDb:
    def __init__(self, path: str | Path, ranker_ttl_hours: float = 24):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ranker_ttl = timedelta(hours=ranker_ttl_hours)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        self._migrate_json()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=15)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

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

    def all_players(self) -> list[dict]:
        with self._conn() as conn:
            return [json.loads(d) for (d,) in conn.execute("SELECT data FROM players ORDER BY name")]

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
    # 일별 시세는 날짜 단위로 계속 쌓는다. 앱이 꺼져 있던 동안의 날짜도, 다시 켜서 받은 응답(최근 약 4개월)에
    # 들어 있으면 그대로 채워진다 — put_price 가 새로 채운 날짜 수를 돌려준다.

    def put_price(self, spid: int, grade: int, data: dict | None) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            last = conn.execute("SELECT MAX(day) FROM market_history WHERE spid = ? AND grade = ?", (spid, grade)).fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO market_prices (spid, grade, current, fetched_at) VALUES (?, ?, ?, ?)",
                         (spid, grade, data["current"] if data else None, now))
            points = (data or {}).get("history") or []
            conn.executemany("INSERT OR REPLACE INTO market_history (spid, grade, day, value) VALUES (?, ?, ?, ?)",
                             [(spid, grade, d, v) for d, v in points])
            stale = (datetime.now() - timedelta(days=MARKET_KEEP_DAYS)).isoformat(timespec="seconds")
            for table in ("market_history", "market_prices"):
                conn.execute(f"DELETE FROM {table} WHERE (spid, grade) IN "
                             "(SELECT spid, grade FROM market_prices WHERE fetched_at < ?)", (stale,))
        return sum(1 for d, _ in points if last and d > last)

    def prices(self, spid: int, max_age_hours: float) -> dict[int, dict]:
        """이 카드의 강화 단계별 시세 {grade: {current, fetched_at, fresh, history}} — 받은 적 있는 단계만."""
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            snaps = conn.execute("SELECT grade, current, fetched_at FROM market_prices WHERE spid = ?", (spid,)).fetchall()
            points = conn.execute("SELECT grade, day, value FROM market_history WHERE spid = ? ORDER BY day", (spid,)).fetchall()
        out = {g: {"current": cur, "fetched_at": at, "fresh": at >= cutoff, "history": []} for g, cur, at in snaps}
        for g, d, v in points:
            if g in out:
                out[g]["history"].append([d, v])
        return out

    def stale_prices(self, max_age_hours: float, limit: int) -> list[tuple[int, int]]:
        """다시 받아야 할 (카드, 강화) — 30일 안에 본 적 있고 max_age 보다 오래된 것, 최근에 본 것부터.

        30일 넘게 다시 받지 않은 카드는 따라잡지 않고 지운다 (약관 — 30일 안의 데이터만).
        """
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        stale = (datetime.now() - timedelta(days=MARKET_KEEP_DAYS)).isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("DELETE FROM market_history WHERE (spid, grade) IN "
                         "(SELECT spid, grade FROM market_prices WHERE fetched_at < ?)", (stale,))
            conn.execute("DELETE FROM market_prices WHERE fetched_at < ?", (stale,))
            return [(s, g) for s, g in conn.execute(
                "SELECT spid, grade FROM market_prices WHERE fetched_at < ? ORDER BY fetched_at DESC LIMIT ?",
                (cutoff, limit))]

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
