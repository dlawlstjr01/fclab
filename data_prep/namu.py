"""나무위키 — 문서 검색 · 본문 글자 (🤖 AI 로 찾기의 1차 근거, services/namufind.py).

- 공식 API 가 없다. robots.txt 가 문서(/w/) · 검색(/Search)을 허용해, 그 화면의 HTML 에서 글자만 뽑는다.
- 호출 사이에 1.5초 이상 둔다 (여러 화면이 함께 불러도 — 잠금). 같은 문서는 7일, 같은 검색은 하루 동안 다시 받지 않는다
  (SQLite dc_cache — FcDb.dc_get · dc_put).
- 나무위키 글은 CC BY-NC-SA 2.0 KR 이다 — 화면에 문서 이름 · 주소를 출처로 함께 보인다.
"""

from __future__ import annotations

import html
import re
import threading
import time
from urllib.parse import quote

import requests

BASE = "https://namu.wiki"
DOC_HOURS = 24 * 7
SEARCH_HOURS = 24
LICENSE = "CC BY-NC-SA 2.0 KR"

# 응답 HTML 에서 값을 찾는 패턴 — 화면이 바뀌면 여기만 고친다
HEADING = re.compile(r"<h([2-6])[^>]*>(.*?)</h\1>", re.S)
SEARCH_ITEM = re.compile(r'<h4[^>]*>.*?<a href="/w/([^"#?]+)"[^>]*>(.*?)</a>\s*</h4>\s*<div[^>]*>(.*?)</div>', re.S)
FOOTER = ("이 저작물은 CC BY-NC-SA", "This page was last edited")


class NamuError(RuntimeError):
    """나무위키가 막았거나(403 · 503) 응답이 없다 — AI 로 찾기는 나무위키 없이 이어 간다."""


def doc_url(title: str) -> str:
    return f"{BASE}/w/{quote(title)}"


def _plain(fragment: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text.replace("[편집]", "")).strip()


def parse_search(page: str, limit: int = 20) -> list[dict]:
    """검색 화면 → [{title, snippet}] (나온 순서, 같은 문서는 한 번)."""
    out, seen = [], set()
    for path, title, snippet in SEARCH_ITEM.findall(page):
        name = _plain(title)
        if name and name not in seen:
            seen.add(name)
            out.append({"title": name, "snippet": _plain(snippet)[:300]})
        if len(out) >= limit:
            break
    return out


def parse_document(page: str, title: str) -> dict:
    """문서 화면 → {title, url, sections: [{heading, text}]}. 표는 칸을 ' | ' 로, 줄은 줄바꿈으로 남긴다 (수상자 · 명단 표를
    LLM 이 읽을 수 있게). 첫 제목 앞(사이트 메뉴 · 목차)과 맨 끝(저작권 안내 · 분류)은 뺀다. 각주 번호 [1] 도 뺀다."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", page, flags=re.S)
    body = HEADING.sub(lambda m: "\n§H " + _plain(m.group(2)) + "\n", body)
    body = re.sub(r"<br\s*/?>", "\n", body)
    body = re.sub(r"</(tr|p|li|div|dd|dt|table|ul|ol)>", "\n", body)
    body = re.sub(r"</t[dh]>", " | ", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    for marker in FOOTER:
        cut = text.find(marker)
        if cut > 0:
            text = text[:cut]
    text = re.sub(r"\[편집\]|\[\d+\]|\[\s*펼치기\s*·\s*접기\s*\]", "", text)
    text = re.sub(r"[ \t ]+", " ", text)
    sections, heading, lines, parents = [], None, [], {}

    def close() -> None:
        if heading is not None and lines:
            sections.append({"heading": heading, "path": " > ".join(parents[d] for d in sorted(parents)),
                             "text": "\n".join(lines)})

    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith("§H "):
            close()
            heading, lines = line[3:].strip(), []
            # '5.1. 남성' 은 '5. 역대 수상자' 아래 — 위 제목까지 붙인 길(path)로 찾게 (본문이 없는 위 제목도 남는다)
            depth = len(re.match(r"^([\d.]*)", heading).group(1).strip(".").split(".")) if heading[:1].isdigit() else 1
            parents = {d: h for d, h in parents.items() if d < depth}
            parents[depth] = heading
            continue
        line = line.strip(" |")
        if heading is None or not line:
            continue
        lines.append(line)
    close()
    return {"title": title, "url": doc_url(title), "sections": sections}


class NamuClient:
    name = "나무위키"

    def __init__(self, contact: str = "", interval: float = 1.5, timeout: float = 20):
        self.user_agent = f"FCLab/0.1 ({contact}) python-requests" if contact else "FCLab/0.1 (student project) python-requests"
        self.interval, self.timeout = interval, timeout
        self._last = 0.0
        self._lock = threading.Lock()   # 여러 화면이 동시에 불러도 호출 간격은 지킨다
        self.session = requests.Session()

    def _get(self, path: str, params: dict | None = None) -> str | None:
        with self._lock:
            gap = self.interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            try:
                response = self.session.get(BASE + path, params=params, timeout=self.timeout,
                                            headers={"User-Agent": self.user_agent, "Accept-Language": "ko"})
            except requests.RequestException as exc:
                raise NamuError(f"나무위키에 연결하지 못했습니다: {exc}") from exc
            finally:
                self._last = time.monotonic()
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise NamuError(f"나무위키가 응답하지 않았습니다 (HTTP {response.status_code})")
        return response.text

    def search(self, query: str, db=None, limit: int = 20) -> list[dict]:
        """나무위키 검색 → [{title, snippet}]. db 가 있으면 같은 검색은 하루 동안 다시 받지 않는다."""
        key = f"namu:search:{query.strip()}"
        cached = db.dc_get(key, SEARCH_HOURS) if db else None
        if cached is not None:
            return cached
        page = self._get("/Search", {"q": query.strip()})
        found = parse_search(page or "", limit)
        if db:
            db.dc_put(key, found)
        return found

    def document(self, title: str, db=None) -> dict | None:
        """문서 본문 {title, url, sections} — 없는 문서면 None. db 가 있으면 같은 문서는 7일 동안 다시 받지 않는다."""
        key = f"namu:doc:v2:{title.strip()}"
        cached = db.dc_get(key, DOC_HOURS) if db else None
        if cached is not None:
            return cached or None
        page = self._get("/w/" + quote(title.strip()))
        doc = parse_document(page, title.strip()) if page else {}
        if db:
            db.dc_put(key, doc)   # 없는 문서도 ({}) 일주일 동안 다시 묻지 않는다
        return doc or None


_client: NamuClient | None = None
_client_lock = threading.Lock()


def client(contact: str = "") -> NamuClient:
    """앱 전체가 함께 쓰는 하나 — 호출 간격을 화면 · 세션마다 따로 세지 않게."""
    global _client
    with _client_lock:
        if _client is None:
            _client = NamuClient(contact)
        return _client
