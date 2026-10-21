"""한국어 위키백과 API — 검색과 본문 수집. (F2004 · NF2006)

외부 API 예절 (NF2006)
- User-Agent 에 연락처를 넣는다. 없으면 분당 10회, 있으면 분당 200회로 한도가 나뉜다.
- 호출 사이에 최소 간격(request_interval)을 둔다.
- 429 · 503 을 받으면 서버가 알려 준 시간(없으면 점점 길게)만큼 기다렸다가 다시 요청한다.
"""

from __future__ import annotations

import html
import re
import threading
import time

import requests

from config import ConfigError

API_URL = "https://ko.wikipedia.org/w/api.php"


class WikiError(RuntimeError):
    """요청 제한이 계속되는 등 위키백과 호출이 끝내 실패했다."""


class WikiClient:
    name = "한국어 위키백과"

    def __init__(self, contact: str = "", interval: float = 0.5, timeout: float = 20, max_retries: int = 4):
        self.user_agent = f"FCLab/0.1 ({contact}) python-requests" if contact else "FCLab/0.1 python-requests"
        try:
            self.user_agent.encode("latin-1")   # HTTP 헤더에는 한글을 쓸 수 없다
        except UnicodeEncodeError:
            raise ConfigError("WIKI_CONTACT 에 한글이 들어 있습니다. 영문 이메일이나 URL 로 바꿔 주세요.") from None
        self.has_contact = bool(contact)
        self.interval = interval
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_call = 0.0
        self._lock = threading.Lock()   # 여러 스레드가 동시에 불러도 호출 간격은 지킨다

    def _wait_turn(self) -> None:
        with self._lock:
            gap = self.interval - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            self._last_call = time.monotonic()

    def _get(self, params: dict) -> dict:
        wait = 2.0
        for _ in range(self.max_retries):
            self._wait_turn()
            response = requests.get(
                API_URL, params=params, headers={"User-Agent": self.user_agent}, timeout=self.timeout
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 503):
                try:
                    retry_after = float(response.headers.get("Retry-After") or wait)
                except ValueError:
                    retry_after = wait
                time.sleep(retry_after)
                wait *= 2
                continue
            response.raise_for_status()
        raise WikiError("요청 제한이 계속되어 위키백과 호출에 실패했습니다.")

    def search(self, query: str, limit: int = 5, movie_id: str | None = None) -> list[dict]:
        """검색어로 후보 문서의 제목과 짧은 요약을 받는다. 본문은 받지 않는다."""
        data = self._get({
            "action": "query", "list": "search", "srsearch": query, "srlimit": limit,
            "format": "json", "formatversion": 2,
        })
        return [
            {
                "title": item["title"],
                "page_id": str(item["pageid"]),
                # 요약에 섞인 <span class="searchmatch"> 같은 강조 태그를 지운다.
                "snippet": html.unescape(re.sub(r"<[^>]+>", "", item.get("snippet", ""))),
            }
            for item in data["query"]["search"]
        ]

    def fetch_intros(self, titles: list[str]) -> dict[str, dict | None]:
        """여러 제목의 첫 문단 · 원문 URL 을 한 번에 받는다(최대 20개). 넘겨주기 문서를 따라간다.

        돌려주는 dict 의 키는 넘겨받은 제목 그대로다. 없는 문서는 None, 동음이의 문서는 disambiguation=True.
        """
        data = self._get({
            "action": "query", "prop": "extracts|info|pageprops", "titles": "|".join(titles[:20]),
            "exintro": 1, "explaintext": 1, "exlimit": 20, "inprop": "url", "ppprop": "disambiguation",
            "redirects": 1, "format": "json", "formatversion": 2,
        })
        query = data["query"]
        normalized = {n["from"]: n["to"] for n in query.get("normalized", [])}
        redirects = {r["from"]: r["to"] for r in query.get("redirects", [])}
        pages = {p["title"]: p for p in query.get("pages", [])}
        result = {}
        for title in titles[:20]:
            final = normalized.get(title, title)
            final = redirects.get(final, final)
            page = pages.get(final)
            if not page or page.get("missing") or "pageid" not in page:
                result[title] = None
                continue
            result[title] = {
                "page_id": str(page["pageid"]),
                "title": page["title"],
                "url": page["fullurl"],
                "intro": page.get("extract", "").strip(),
                "disambiguation": "disambiguation" in page.get("pageprops", {}),
            }
        return result

    def fetch(self, title: str) -> dict | None:
        """문서 제목으로 본문(태그 없는 평문)과 원문 URL 을 받는다. 없는 문서면 None."""
        data = self._get({
            "action": "query", "prop": "extracts|info", "titles": title,
            "explaintext": 1, "inprop": "url", "redirects": 1,
            "format": "json", "formatversion": 2,
        })
        page = data["query"]["pages"][0]
        if page.get("missing") or "pageid" not in page:
            return None
        return {
            "page_id": str(page["pageid"]),
            "title": page["title"],
            "url": page["fullurl"],
            "text": page.get("extract", ""),
        }
