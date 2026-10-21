"""넥슨 Open API (FC 온라인) — 메타데이터 · 랭커 스탯 · 선수 이미지 주소. (F2001 · NF2006)

    메타데이터  https://open.api.nexon.com/static/fconline/meta/{spid|seasonid|spposition}.json   (키 필요 없음)
    랭커 스탯   GET /fconline/v1/ranker-stats?matchtype=50&players=[{"id":spid,"po":포지션}]      (x-nxopen-api-key)
    이미지      fco.dn.nexoncdn.co.kr 의 선수 얼굴(pid) · 액션 이미지(spid)

spid(선수 카드 ID) = 시즌 ID × 1,000,000 + pid(선수 ID).  예) 866200104 → 26 TOTS 시즌의 손흥민(200104)
출처 표기: Data based on NEXON Open API
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

META_URL = "https://open.api.nexon.com/static/fconline/meta/{name}.json"
API_URL = "https://open.api.nexon.com/fconline/v1"
ACTION_IMAGE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction/p{spid}.png"
FACE_IMAGE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/players/p{pid}.png"
SOURCE_URL = "https://openapi.nexon.com/game/fconline/"


def split_spid(spid: int) -> tuple[int, int]:
    """(시즌 ID, pid)"""
    return spid // 1_000_000, spid % 1_000_000


class NexonError(RuntimeError):
    """넥슨 Open API 호출이 끝내 실패했다."""


class NexonQuotaError(NexonError):
    """하루 호출 한도를 다 썼다 (테스트 키 1,000회/일). 다시 시도해도 오늘은 안 된다."""


class NexonClient:
    # 0.25초(초당 4회): 데이터 빌드 때 이 간격으로 약 220회 연속 호출해도 429 가 없었다. 0.1초로 줄이면 429 가 났다.
    def __init__(self, api_key: str | None, interval: float = 0.25, timeout: float = 30, max_retries: int = 5):
        self.api_key = api_key
        self.interval = interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.skipped: list[int] = []
        self.remaining: int | None = None   # 오늘 남은 호출 수 (응답 헤더 x-ratelimit-remaining)
        self.limit: int | None = None
        self._last_call = 0.0
        self._lock = threading.Lock()   # 여러 스레드가 동시에 불러도 호출 간격은 지킨다
        self._session = requests.Session()   # 연결을 재사용해 호출마다 TLS 연결을 새로 맺지 않는다

    def _get(self, url: str, params: dict | None = None, auth: bool = True):
        if auth and not self.api_key:
            raise NexonError("NEXON_API_KEY 가 없습니다. .env 에 넣으세요.")
        headers = {"x-nxopen-api-key": self.api_key} if auth else {}
        wait = 1.0
        for _ in range(self.max_retries):
            with self._lock:
                gap = self.interval - (time.monotonic() - self._last_call)
                if gap > 0:
                    time.sleep(gap)
                self._last_call = time.monotonic()
            response = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            if auth and response.headers.get("x-ratelimit-remaining") is not None:
                try:
                    self.remaining = int(response.headers["x-ratelimit-remaining"])
                    self.limit = int(response.headers.get("x-ratelimit-limit") or 0) or None
                except ValueError:
                    pass
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429 and self.remaining is not None and self.remaining <= 0:
                # 하루 한도를 다 썼다 — 다시 보내도 오늘은 안 되니 기다리지 않고 바로 알린다
                raise NexonQuotaError(f"넥슨 Open API 하루 호출 한도({self.limit or 1000}회)를 다 썼습니다. 내일 다시 시도하세요.")
            if response.status_code in (429, 500, 503):   # 요청 제한 · 일시 오류 → 서버가 알려 준 시간(없으면 점점 길게) 쉬고 다시
                try:
                    retry_after = float(response.headers.get("Retry-After") or wait)
                except ValueError:
                    retry_after = wait
                time.sleep(retry_after)
                wait *= 2
                continue
            try:
                detail = response.json().get("error", {}).get("name", "")
            except ValueError:
                detail = ""
            raise NexonError(f"넥슨 Open API 오류 {response.status_code} {detail}".strip())
        raise NexonError("넥슨 Open API 요청 제한이 계속되었습니다.")

    def meta(self, name: str, cache_dir: Path | None = None, max_age_hours: float = 24) -> list[dict]:
        """spid · seasonid · spposition · division · matchtype 메타데이터.

        spid.json 은 6.5MB 라 받는 데 수 초가 걸린다. cache_dir 를 주면 하루 동안 디스크 사본을 쓴다(0.1초대).
        """
        path = Path(cache_dir) / f"{name}.json" if cache_dir else None
        if path and path.exists() and time.time() - path.stat().st_mtime < max_age_hours * 3600:
            return json.loads(path.read_text(encoding="utf-8"))
        data = self._get(META_URL.format(name=name), auth=False)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # ── 유저 · 경기 (내 경기 분석) ───────────────────────────────────

    def ouid(self, nickname: str) -> str | None:
        """닉네임 → 계정 식별자. 없는 닉네임이면 None."""
        try:
            return self._get(f"{API_URL}/id", params={"nickname": nickname})["ouid"]
        except NexonError as exc:
            if "400" in str(exc) or "404" in str(exc):
                return None
            raise

    def user_basic(self, ouid: str) -> dict:
        return self._get(f"{API_URL}/user/basic", params={"ouid": ouid})

    def max_division(self, ouid: str) -> list[dict]:
        return self._get(f"{API_URL}/user/maxdivision", params={"ouid": ouid})

    def user_matches(self, ouid: str, matchtype: int = 50, limit: int = 10) -> list[str]:
        return self._get(f"{API_URL}/user/match", params={"ouid": ouid, "matchtype": matchtype, "offset": 0, "limit": limit})

    def match_detail(self, match_id: str) -> dict:
        return self._get(f"{API_URL}/match-detail", params={"matchid": match_id})

    def user_trades(self, ouid: str, tradetype: str = "buy", limit: int = 20) -> list[dict]:
        """내 이적시장 거래 기록 (buy | sell) — 거래일 · 카드 · 강화 등급 · 금액(BP)."""
        return self._get(f"{API_URL}/user/trade", params={"ouid": ouid, "tradetype": tradetype, "offset": 0, "limit": limit})

    def ranker_stats(self, pairs: list[tuple[int, int]], matchtype: int = 50, batch: int = 40) -> list[dict]:
        """(spid, 포지션) 쌍마다 랭커(상위 유저)가 그 카드를 그 포지션에 썼을 때의 경기당 평균 스탯.

        한 번에 너무 많이 보내면 주소가 길어져 414 오류가 나서 40쌍씩 나눠 보낸다.
        기록이 없는 쌍은 응답에서 빠진다. 랭커 스탯을 지원하지 않는 카드가 섞이면 묶음 전체가 400 이라,
        그때는 카드별로 다시 보내고 끝내 실패하는 카드는 건너뛴다(self.skipped 에 남긴다).
        """
        self.skipped: list[int] = []
        rows = []
        for start in range(0, len(pairs), batch):
            chunk = pairs[start : start + batch]
            try:
                rows += self._ranker_request(chunk, matchtype)
            except NexonError as exc:
                if "OPENAPI00004" not in str(exc):
                    raise
                for spid in dict.fromkeys(s for s, _ in chunk):
                    try:
                        rows += self._ranker_request([p for p in chunk if p[0] == spid], matchtype)
                    except NexonError as card_exc:
                        if "OPENAPI00004" not in str(card_exc):
                            raise
                        self.skipped.append(spid)
        return rows

    def _ranker_request(self, pairs: list[tuple[int, int]], matchtype: int) -> list[dict]:
        players = [{"id": spid, "po": po} for spid, po in pairs]
        return self._get(
            f"{API_URL}/ranker-stats",
            params={"matchtype": matchtype, "players": json.dumps(players, separators=(",", ":"))},
        )

    @staticmethod
    def image_exists(url: str) -> bool:
        try:
            return requests.head(url, timeout=10).status_code == 200
        except requests.RequestException:
            return False
