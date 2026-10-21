"""설정 읽기 — 값은 settings.toml, 비밀 값(키 · 연락처)은 .env 에서 읽는다. (명세서 2.5 · NF2003)"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# 프로젝트 폴더의 .env 를 먼저, 없으면 실습 폴더(한 단계 위)의 .env 를 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다.
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")

SEARCH_TYPES = {"similarity", "mmr"}
NO_CONTACT_INTERVAL = 6.5   # 연락처 없는 User-Agent 는 분당 10회 → 6초보다 길게 쉰다


class ConfigError(ValueError):
    """설정 값이 잘못됐다. 메시지는 화면에 그대로 보여 줘도 되는 안내 문구다."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    storage_dir: Path
    chat_model: str
    embedding_model: str
    base_url: str
    seasons: tuple[int, ...]
    matchtype: int
    chunk_size: int
    chunk_overlap: int
    top_k: int
    search_type: str
    mmr_fetch_k: int
    mmr_lambda: float
    max_keywords: int
    search_limit: int
    max_docs: int
    min_doc_chars: int
    request_interval: float
    parallel: int
    explore_candidates: int
    explore_max_results: int
    market_enabled: bool = True
    market_interval: float = 1.2
    market_ttl_hours: float = 6
    market_catch_up: int = 60

    @property
    def items_path(self) -> Path:
        return self.data_dir / "players.json"

    @property
    def chroma_dir(self) -> Path:
        return self.storage_dir / "chroma"

    @property
    def docs_dir(self) -> Path:
        return self.storage_dir / "docs"

    @property
    def manifest_path(self) -> Path:
        return self.storage_dir / "manifest.json"

    @property
    def meta_collection(self) -> str:
        return "player_meta"

    @property
    def wiki_collection(self) -> str:
        # 청크 설정마다 컬렉션을 따로 둔다. 값을 바꿔 색인해도 이전 결과와 나란히 비교할 수 있다.
        return f"player_wiki_c{self.chunk_size}_o{self.chunk_overlap}"

    @property
    def wiki_interval(self) -> float:
        return self.request_interval if wiki_contact() else max(self.request_interval, NO_CONTACT_INTERVAL)


def load_settings(path: str | Path | None = None, **overrides) -> Settings:
    """settings.toml 을 읽는다. 경로는 그 파일이 있는 폴더 기준이다. overrides 로 값을 바꿀 수 있다(검증용)."""
    path = Path(path or os.getenv("FCLAB_SETTINGS") or BASE_DIR / "settings.toml")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    values = {
        "data_dir": (base / raw["paths"]["data_dir"]).resolve(),
        "storage_dir": (base / raw["paths"]["storage_dir"]).resolve(),
        "chat_model": raw["models"]["chat"],
        "embedding_model": raw["models"]["embedding"],
        "base_url": raw["models"]["base_url"],
        "seasons": tuple(int(s) for s in raw["items"]["seasons"]),
        "matchtype": int(raw["items"]["matchtype"]),
        "chunk_size": int(raw["chunking"]["chunk_size"]),
        "chunk_overlap": int(raw["chunking"]["chunk_overlap"]),
        "top_k": int(raw["retrieval"]["top_k"]),
        "search_type": raw["retrieval"]["search_type"],
        "mmr_fetch_k": int(raw["retrieval"]["mmr_fetch_k"]),
        "mmr_lambda": float(raw["retrieval"]["mmr_lambda"]),
        "max_keywords": int(raw["collect"]["max_keywords"]),
        "search_limit": int(raw["collect"]["search_limit"]),
        "max_docs": int(raw["collect"]["max_docs"]),
        "min_doc_chars": int(raw["collect"]["min_doc_chars"]),
        "request_interval": float(raw["collect"]["request_interval"]),
        "parallel": int(raw["collect"].get("parallel", 1)),
        "explore_candidates": int(raw["explore"]["candidates"]),
        "explore_max_results": int(raw["explore"]["max_results"]),
        "market_enabled": bool(raw.get("market", {}).get("enabled", True)),
        "market_interval": float(raw.get("market", {}).get("interval", 1.2)),
        "market_ttl_hours": float(raw.get("market", {}).get("ttl_hours", 6)),
        "market_catch_up": int(raw.get("market", {}).get("catch_up", 60)),
    }
    values.update(overrides)
    settings = Settings(**values)

    if settings.search_type not in SEARCH_TYPES:
        raise ConfigError(f"settings.toml 의 search_type 은 {sorted(SEARCH_TYPES)} 중 하나여야 합니다.")
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ConfigError("settings.toml 의 chunk_overlap 은 0 이상, chunk_size 보다 작아야 합니다.")
    return settings


def gms_key() -> str | None:
    return os.getenv("GMS_KEY", "").strip() or None


def nexon_key() -> str | None:
    return os.getenv("NEXON_API_KEY", "").strip() or None


def wiki_contact() -> str:
    return os.getenv("WIKI_CONTACT", "").strip()
