"""모든 페이지가 함께 쓰는 것 — 준비(설정 · 데이터 · 서비스), 이동, 선수 찾기 · 열기, 근거 표시, 진행 상태.

속도 원칙
- 카드 정보(이름 · 시즌 · 이미지 주소)는 디스크에 캐시한 넥슨 메타데이터에서 바로 만든다 — 네트워크 0회.
- 랭커 스탯 · 위키 소개처럼 네트워크가 필요한 것은 화면을 먼저 그린 뒤 채우고, 한 번 받은 것은 저장한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import openai
import requests
import streamlit as st

import config
import llm
from data_prep.collector import Collector
from data_prep.db import FcDb
from data_prep.market import MarketClient
from data_prep.nexon_api import NexonClient, NexonError, NexonQuotaError, split_spid
from data_prep.player_builder import CardCatalog
from data_prep.players import SAVE_MIN_SHOTS, load_players, player_label
from data_prep.wiki_api import WikiClient, WikiError
from rag.citations import cited_evidence
from rag.graph import build_graph
from services.ai import AiCache
from services.answering import run_task
from services.market import MarketService
from services.prefetch import Prefetcher
from vector_store import PlayerStore

log = logging.getLogger("fclab")

NAV = [("home", "🏠 홈"), ("players", "👤 선수"), ("ranking", "🏆 파워 랭킹"), ("compare", "⚖️ 비교"),
       ("tactics", "🧠 전술 추천"), ("favorites", "⭐ 즐겨찾기"), ("me", "🎮 내 기록")]
NAV_OF = {"player": "players"}   # 선수 상세는 '선수' 메뉴 아래


@dataclass
class Ctx:
    settings: config.Settings
    players: list[dict]          # 분석해 둔 카드 (미리 만든 + 열어 본 카드)
    by_id: dict[str, dict]
    by_pid: dict[int, dict]      # 같은 선수의 다른 카드가 찾은 위키 문서를 다시 쓰려고
    catalog: CardCatalog
    nexon: NexonClient
    model: object
    store: PlayerStore
    collector: Collector
    graph: object
    ai_cache: AiCache
    db: FcDb
    prefetcher: Prefetcher
    favorites: "Favorites"
    theme: str
    curated_ids: set
    market: MarketService        # 강화 단계별 시세 (데이터센터) — 백그라운드에서 받아 SQLite 에


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, llm.MissingKeyError):
        return "GMS_KEY가 없습니다. .env에 키를 넣고 앱을 다시 실행하세요."
    if isinstance(exc, config.ConfigError):
        return str(exc)
    if isinstance(exc, openai.AuthenticationError):
        return "GMS 키가 올바르지 않습니다(401). .env의 GMS_KEY를 확인하세요."
    if isinstance(exc, openai.NotFoundError):
        return "모델 또는 API 주소를 찾지 못했습니다(404). settings.toml의 models를 확인하세요."
    if isinstance(exc, openai.RateLimitError):
        return "요청이 많아 잠시 막혔습니다. 잠시 뒤 다시 시도하세요."
    if isinstance(exc, NexonQuotaError):
        return str(exc) + " (검색 · 미리 분석된 카드 · AI 기능은 계속 쓸 수 있습니다)"
    if isinstance(exc, (openai.APIConnectionError, requests.RequestException, WikiError, NexonError)):
        return "외부 서비스 연결이 원활하지 않습니다. 잠시 뒤 다시 시도하세요."
    return "처리 중 문제가 생겼습니다. 잠시 뒤 다시 시도하세요."


# ── 준비 ────────────────────────────────────────────────────────

@st.cache_resource
def _settings():
    return config.load_settings()


@st.cache_data
def _curated(path: str) -> list[dict]:
    return load_players(path)


@st.cache_resource
def _catalog(_settings) -> tuple[NexonClient, CardCatalog]:
    nexon = NexonClient(config.nexon_key())
    return nexon, CardCatalog(nexon, _settings.storage_dir / "meta")


@st.cache_resource
def _services(_settings):
    model = llm.make_chat_model(_settings)
    store = PlayerStore(_settings, llm.make_embeddings(_settings))
    wiki = WikiClient(config.wiki_contact(), interval=_settings.wiki_interval)
    collector = Collector(_settings, model, wiki)
    return model, store, collector, build_graph(store, model, _settings)


@st.cache_resource
def _background(_settings, _catalog, _wiki, _curated) -> tuple[FcDb, Prefetcher]:
    """SQLite + 백그라운드 미리 불러오기 (앱 프로세스에 하나). 시작하자마자 미리 분석한 카드의 포지션 기록을 채운다."""
    db = FcDb(_settings.storage_dir / "fclab.db")
    hints = {}
    for p in _curated:
        if p.get("wiki_title"):
            hints.setdefault(p["pid"], p)
    # 앱 시작 때 432장을 미리 받던 것은 뺐다 — 넥슨 테스트 키는 하루 1,000회라 시작할 때마다 약 300회를 쓰면 금방 바닥난다.
    # 미리 분석한 카드의 포지션별 기록은 build_items.py 가 players.json 에 함께 넣는다 (ranker_positions).
    return db, Prefetcher(_catalog, _wiki, db, _settings.matchtype, hints)


@st.cache_resource
def _market(_settings, _db) -> MarketService:
    """시세 수집기 (앱 프로세스에 하나). 켜질 때 한 번 — 꺼져 있던 동안 밀린 시세를 뒤쪽 우선순위로 따라잡는다."""
    client = MarketClient(_settings.market_interval) if _settings.market_enabled else None
    market = MarketService(client, _db, _settings.market_ttl_hours, _settings.market_enabled)
    queued = market.catch_up(_settings.market_catch_up)
    if queued:
        log.info("시세 따라잡기 %d건 예약 (앱이 꺼져 있던 동안)", queued)
    return market


def _stop(kind: str, message: str) -> None:
    st.markdown("## ⚽ FC LAB")
    getattr(st, kind)(message)
    st.stop()


def boot() -> Ctx:
    """앱 시작 점검(F2012) — 설정 · 선수 데이터 · 키 · 색인. 문제가 있으면 안내만 보이고 멈춘다."""
    try:
        settings = _settings()
    except config.ConfigError as exc:
        _stop("error", str(exc))
    if not settings.items_path.exists():
        _stop("warning", "선수 데이터가 없습니다. 터미널에서 `python build_items.py`를 실행한 뒤 새로고침하세요.")
    if not config.gms_key():
        _stop("error", "GMS_KEY가 설정되지 않았습니다. `.env`에 `GMS_KEY=발급받은_키`를 넣고 앱을 다시 실행하세요.")
    try:
        model, store, collector, graph = _services(settings)
        nexon, catalog = _catalog(settings)
    except Exception as exc:  # noqa: BLE001 — 화면에는 안내만, 상세는 로그로
        log.exception("서비스 준비 실패")
        _stop("error", friendly_error(exc))
    if store.meta_count() == 0:
        _stop("warning", "선수 메타데이터 색인이 비어 있습니다. 터미널에서 `python build_index.py`를 실행한 뒤 새로고침하세요.")

    curated = _curated(str(settings.items_path))
    curated_ids = {p["player_id"] for p in curated}
    db, prefetcher = _background(settings, catalog, collector.wiki, curated)
    # 미리 분석한 카드 + 앱에서 열어 분석한 카드(SQLite)
    players = curated + [p for p in db.all_players() if p["player_id"] not in curated_ids]
    by_pid = {}
    for p in players:
        if p.get("wiki_title"):
            by_pid.setdefault(p["pid"], p)
    theme = (st.context.theme.type or "dark") if hasattr(st, "context") else "dark"
    for key, default in (("page", "home"), ("player_id", None), ("chats", {}), ("results", {}),
                         ("explore_results", None), ("pending_question", None), ("cmp_a", None), ("cmp_b", None),
                         ("me_result", None)):
        if key not in st.session_state:
            st.session_state[key] = default
    return Ctx(settings, players, {p["player_id"]: p for p in players}, by_pid, catalog, nexon, model, store,
               collector, graph, AiCache(db), db, prefetcher, Favorites(db), theme, curated_ids, _market(settings, db))


# ── 이동 ────────────────────────────────────────────────────────

def _remember() -> None:
    """지금 화면을 이동 기록에 쌓는다 — ← 뒤로 버튼이 꺼내 쓴다."""
    here = (st.session_state.page, st.session_state.player_id)
    history = st.session_state.setdefault("history", [])
    if not history or history[-1] != here:
        history.append(here)
        del history[:-30]


def go(page: str) -> None:
    if page != st.session_state.page:
        _remember()
    st.session_state.page = page


def go_back() -> None:
    history = st.session_state.get("history") or []
    if history:
        st.session_state.page, st.session_state.player_id = history.pop()
    else:
        st.session_state.page = "home"


def open_player(player_id: str) -> None:
    if (st.session_state.page, st.session_state.player_id) != ("player", str(player_id)):
        _remember()
    st.session_state.player_id = str(player_id)
    st.session_state.page = "player"


def add_to_compare(player_id: str, other_id: str | None = None) -> None:
    """비교 페이지로 — 둘 다 주면 A · B 로, 하나면 빈 자리에 넣는다."""
    if other_id:
        st.session_state.cmp_a, st.session_state.cmp_b = player_id, other_id
    elif st.session_state.cmp_a in (None, player_id):
        st.session_state.cmp_a = player_id
    else:
        st.session_state.cmp_b = player_id
    go("compare")


def back_button(where: str, container=None) -> None:
    """← 뒤로 — 검색창 옆 · 상세 화면 맨 위에 둔다 (상단 메뉴가 아니라 지금 보고 있는 곳 가까이)."""
    (container or st).button("← 뒤로", key=f"back_{where}", on_click=go_back, width="stretch",
                             disabled=not st.session_state.get("history"), help="이전 화면으로")


def top_bar() -> None:
    """로고(누르면 홈) · 메뉴. 넓은 화면은 한 줄, 좁은 화면에서만 다음 줄로 (ui_parts.css 의 .st-key-navbar)."""
    with st.container(key="navbar"):
        brand, *cols = st.columns([1.8] + [1.2] * len(NAV), vertical_alignment="center")
        brand.button("⚽ FC LAB", key="nav_brand", on_click=go, args=("home",), type="tertiary", width="stretch")
        active = NAV_OF.get(st.session_state.page, st.session_state.page)
        for col, (key, label) in zip(cols, NAV):
            col.button(label, key=f"nav_{key}", on_click=go, args=(key,), width="stretch",
                       type="primary" if active == key else "tertiary")


# ── 선수 찾기 · 열기 ─────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def search_players(ctx: Ctx, query: str, season_id: int | None = None, limit: int = 24) -> list[dict]:
    """이름 검색 + 시즌(클래스) 거르기 — 분석해 둔 카드 먼저, 그다음 전체 88,359장. 네트워크 없이 끝난다.

    이름 없이 시즌만 고르면 그 시즌 카드를 둘러본다.
    """
    q = _norm(query)
    if not q and season_id is None:
        return []

    def keep(spid: int) -> bool:
        return season_id is None or split_spid(spid)[0] == season_id

    found = [p for p in ctx.players if (not q or q in _norm(p["name"])) and keep(p["spid"])]
    found.sort(key=lambda p: (bool(q) and _norm(p["name"]) != q, -p["season_id"],
                              -((p.get("ranker") or {}).get("goal", 0) + (p.get("ranker") or {}).get("assist", 0))))
    seen = {p["player_id"] for p in found}
    if q:
        hits = [h["spid"] for h in ctx.catalog.search(query, limit=2000)]
    else:
        hits = [c["id"] for c in ctx.catalog.cards if split_spid(c["id"])[0] == season_id]
    for spid in hits:
        if len(found) >= limit:
            break
        if keep(spid) and str(spid) not in seen:
            found.append(ctx.catalog.basic_player(spid))
            seen.add(str(spid))
    return found[:limit]


class Favorites:
    """즐겨찾기 — 자주 보는 선수 카드. SQLite 의 favorites 표 (이 앱을 쓰는 사람 한 명 기준)."""

    def __init__(self, db: FcDb):
        self.db = db

    def ids(self) -> list[str]:
        return self.db.favorite_ids()

    def toggle(self, player_id: str) -> None:
        self.db.toggle_favorite(player_id)


def get_player(ctx: Ctx, player_id: str) -> dict:
    """미리 분석한 카드 → SQLite 에 있는 카드(백그라운드가 끝낸 것 포함) → 카드 정보만으로 바로 만든 카드(lite)."""
    if player_id in ctx.by_id:
        return ctx.by_id[player_id]
    stored = ctx.db.get_player(player_id)
    if stored:
        return stored
    spid = int(player_id)
    if spid not in ctx.catalog.by_spid:
        raise KeyError(player_id)
    return ctx.catalog.basic_player(spid)


def _keep(ctx: Ctx, full: dict) -> dict:
    """색인 · 화면 목록에 더하기 — 화면 스레드에서만 (Chroma 쓰기를 스레드끼리 겹치지 않게).

    SQLite 저장은 백그라운드(Prefetcher)가 이미 했다.
    """
    if not ctx.store.get_meta(full["player_id"]):
        ctx.store.index_players([full])    # player_meta 에 덮어쓰기 — 조건 탐색 · RAG 근거에 바로 쓰인다
    if full["player_id"] not in ctx.by_id:
        ctx.players.append(full)
        ctx.by_id[full["player_id"]] = full
    if full.get("wiki_title"):
        ctx.by_pid.setdefault(full["pid"], full)
    return full


def enrich(ctx: Ctx, player: dict) -> dict:
    """분석 안 된 카드를 완성한다. 백그라운드가 이미 끝냈으면 즉시, 진행 중이면 그 결과를 기다린다."""
    if player["player_id"] in ctx.curated_ids:
        return player
    full = player if not player.get("lite") else ctx.prefetcher.get(player["spid"])
    return _keep(ctx, full)


def enrich_many(ctx: Ctx, players: list[dict]) -> list[dict]:
    """여러 장을 한 번에 — 백그라운드에 한꺼번에 맡기고(동시에), 결과는 차례로 받아 색인."""
    ctx.prefetcher.schedule([p["spid"] for p in players if p.get("lite")])
    return [enrich(ctx, p) for p in players]


def with_saves(ctx: Ctx, players: list[dict]) -> list[dict]:
    """골키퍼 카드에 경기 기록으로 모은 선방 재료(gk_saves)를 붙인다 — 공유 원본은 두고 그 카드만 복사해서."""
    saves = ctx.db.gk_saves()
    if not saves:
        return players
    return [{**p, "gk_saves": saves[p["spid"]]} if p.get("spid") in saves and "gk_saves" not in p else p
            for p in players]


def has_save_samples(ctx: Ctx) -> bool:
    """선방률을 보여 줄 만큼 표본이 모인 골키퍼 카드가 하나라도 있나 — 없으면 골키퍼 정렬 기본값을 랭커 경기 수로."""
    return any(s["on_target"] >= SAVE_MIN_SHOTS for s in ctx.db.gk_saves().values())


def tile_grid(ctx: Ctx, players: list[dict], key: str, cols: int = 6, ranked: bool = False,
              kpi=None, reasons: dict[str, str] | None = None, badges: dict[str, str] | None = None) -> None:
    """카드 타일 묶음 — 카드 전체가 버튼이다 (ui_parts.css 의 .st-key-tile_* : 타일 위에 투명 버튼을 겹친다).

    badges(랭킹 순위) · reasons(AI 추천 이유)는 카드 안에 넣는다 — 카드 밖에 따로 두면 겹치거나 줄이 어긋난다.
    """
    import ui_parts   # 순환 import 를 피하려고 여기서

    players = with_saves(ctx, players)
    with st.container(key=f"grid_{key}"):   # 좁은 화면에서 줄바꿈 (ui_parts.css 의 st-key-grid_*)
        for start in range(0, len(players), cols):
            for offset, (col, p) in enumerate(zip(st.columns(cols), players[start : start + cols])):
                with col, st.container(key=f"tile_{key}_{p['player_id']}"):
                    st.markdown(ui_parts.tile(p, rank=start + offset + 1 if ranked else None, kpi=kpi(p) if kpi else None,
                                              badge=(badges or {}).get(p["player_id"], ""),
                                              note=(reasons or {}).get(p["player_id"], "")), unsafe_allow_html=True)
                    st.button(f"{p['name']} · {p['season_short']} 보기", key=f"{key}_{p['player_id']}",
                              on_click=open_player, args=(p["player_id"],), width="stretch")


# ── 근거 표시 (F2008) · 진행 상태 (F2012) ───────────────────────────

def render_sources(answer: str, evidence: list[dict]) -> None:
    used = cited_evidence(answer, evidence)
    if not used:
        st.caption("ⓘ 답변에 인용된 근거가 없습니다.")
        return
    with st.expander(f"📎 답변에 사용한 근거 {len(used)}건"):
        for e in used:
            link = f" · [원문]({e['url']})" if e.get("url") else ""
            st.markdown(f"**[근거 {e['number']}]** {e['title']} · {e['source_label']}{link}")


def keep_evidence(evidence: list[dict]) -> list[dict]:
    """대화 기록에는 근거 본문을 빼고 표시에 필요한 것만 남긴다."""
    return [{k: e[k] for k in ("number", "source_type", "source_label", "title", "url")} for e in evidence]


def run_with_status(ctx: Ctx, task: str, targets: list[dict], user_text: str) -> dict | None:
    """수집 · 색인 · 답변 생성 중 진행 상태를 보여 준다 (F2012). 실패하면 안내만 보이고 None."""
    try:
        with st.status("관련 위키백과 문서를 찾고 있습니다...", expanded=False) as status:
            targets = enrich_many(ctx, targets)
            for p in targets:
                if not ctx.store.get_meta(p["player_id"]):
                    ctx.store.index_players([p])
            result = run_task(task, targets, user_text, ctx.collector, ctx.store, ctx.graph, on_step=status.write)
            reports = result["reports"].values()
            new_docs = sum(len(r.new_docs) for r in reports)
            total = sum(len(ctx.collector.stored_docs(p["player_id"])) for p in targets)
            label = f"위키 문서 {new_docs}건 새로 수집 · 모두 {total}건" if new_docs else f"이미 수집된 위키 문서 {total}건 사용"
            if total == 0:
                label = "관련 위키 문서를 찾지 못해 선수 메타데이터만으로 답했습니다"
            status.update(label=label, state="complete")
    except Exception as exc:  # noqa: BLE001
        log.exception("답변 생성 실패")
        st.error(friendly_error(exc))
        return None
    if not result["answer"]:
        st.error("답변을 만들지 못했습니다. 잠시 뒤 다시 시도하세요.")
        return None
    return {"answer": result["answer"], "evidence": keep_evidence(result["evidence"])}


def label_of(ctx: Ctx, player_id: str) -> str:
    try:
        return player_label(get_player(ctx, player_id))
    except KeyError:
        return str(player_id)


def season_id_of(player_id: str) -> int:
    return split_spid(int(player_id))[0]
