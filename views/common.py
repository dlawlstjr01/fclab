"""모든 페이지가 함께 쓰는 것 — 준비(설정 · 데이터 · 서비스), 이동, 선수 찾기 · 열기, 근거 표시, 진행 상태.

속도 원칙
- 카드 정보(이름 · 시즌 · 이미지 주소)는 디스크에 캐시한 넥슨 메타데이터에서 바로 만든다 — 네트워크 0회.
- 랭커 스탯 · 위키 소개처럼 네트워크가 필요한 것은 화면을 먼저 그린 뒤 채우고, 한 번 받은 것은 저장한다.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

import openai
import requests
import streamlit as st

import config
import llm
import ui_parts
from data_prep.collector import Collector
from data_prep.db import FcDb
from data_prep.enhance import GRADE_OVR
from data_prep.datacenter import DatacenterClient
from data_prep.nexon_api import NexonClient, NexonError, NexonQuotaError, split_spid
from data_prep.player_builder import CardCatalog
from data_prep.players import load_players, player_label
from data_prep.wiki_api import WikiClient, WikiError
from rag.citations import cited_evidence
from rag.graph import build_graph
from services.ai import AiCache
from services.answering import run_task
from services.cardinfo import CardInfoService
from services.daily import DailyBoard
from services.finder import CardFinder
from services.images import ImageCache
from services.market import MarketService
from services.prefetch import Prefetcher
from services.stats import StatsCrawler
from services.teamcolor import TeamColorService
from vector_store import PlayerStore

log = logging.getLogger("fclab")

NAV = [("home", "🏠 홈"), ("players", "👤 선수"), ("ranking", "🏆 파워 랭킹"), ("compare", "⚖️ 비교"),
       ("tactics", "🧠 스쿼드 · 전술"), ("grow", "⚒️ 강화 시뮬"), ("favorites", "⭐ 즐겨찾기"), ("me", "🎮 내 기록")]
NAV_OF = {"player": "players"}   # 선수 상세는 '선수' 메뉴 아래
PAGE_NAMES = {key for key, _ in NAV} | set(NAV_OF)


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
    cards: CardInfoService       # 카드 상세 (데이터센터) — 특성 · 능력치 · 클럽 경력 · 팀컬러 · 포지션별 OVR
    datacenter: DatacenterClient | None   # 데이터센터 창구 (AI 추천 스쿼드 · 강화 부스트), 시세를 끄면 None
    teamcolors: TeamColorService  # 팀컬러 목록 · 단계별 효과 (데이터센터) — SQLite 에, 새 팀컬러는 더한다
    stats: StatsCrawler          # 모든 카드의 능력치 · 급여 · OVR (데이터센터 선수 검색) — 뒤에서 모은다
    finder: CardFinder           # 조건으로 카드 찾기 (국적 · 리그 · 클럽 · 팀컬러 · 특성 · 주발 …) — 선수 찾기 · AI 찾기
    daily: DailyBoard            # 데일리 기록 · 차트 (랭커가 어제 쓴 카드 기록 TOP · 포메이션 · 포지션별 이용)


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


@st.cache_resource
def _curated(path: str) -> list[dict]:
    """미리 분석한 카드 — 화면마다 복사본을 풀지 않도록 같은 목록을 나눠 쓴다 (선수 dict 를 고쳐 쓰는 곳은 없다)."""
    return load_players(path)


@st.cache_resource
def _database(_settings) -> FcDb:
    """SQLite (앱 프로세스에 하나) — 연결 하나를 열어 두고 스레드끼리 차례로 쓴다."""
    return FcDb(_settings.storage_dir / "fclab.db")


@st.cache_resource
def _catalog(_settings, _db) -> tuple[NexonClient, CardCatalog]:
    """전체 카드 목록 — DB 에 둔 목록을 바로 쓰고, 하루 지났으면 뒤에서 새로 받아 새 카드를 더한다."""
    nexon = NexonClient(config.nexon_key())
    catalog = CardCatalog(nexon, _settings.storage_dir / "meta", _db)
    catalog.refresh_in_background()
    return nexon, catalog


@st.cache_resource
def _services(_settings):
    model = llm.make_chat_model(_settings)
    store = PlayerStore(_settings, llm.make_embeddings(_settings))
    wiki = WikiClient(config.wiki_contact(), interval=_settings.wiki_interval)
    collector = Collector(_settings, model, wiki)
    return model, store, collector, build_graph(store, model, _settings)


@st.cache_resource
def _background(_settings, _db, _catalog, _wiki, _curated) -> Prefetcher:
    """백그라운드 미리 불러오기 (앱 프로세스에 하나)."""
    hints = {}
    for p in _curated:
        if p.get("wiki_title"):
            hints.setdefault(p["pid"], p)
    # 앱 시작 때 432장을 미리 받던 것은 뺐다 — 넥슨 테스트 키는 하루 1,000회라 시작할 때마다 약 300회를 쓰면 금방 바닥난다.
    # 미리 분석한 카드의 포지션별 기록은 build_items.py 가 players.json 에 함께 넣는다 (ranker_positions).
    return Prefetcher(_catalog, _wiki, _db, _settings.matchtype, hints)


@st.cache_resource
def _images(_db) -> ImageCache:
    """선수 · 시즌 이미지 사본 (앱 프로세스에 하나) — static/cache 에 받아 두고 앱이 직접 내준다."""
    return ImageCache(_db, config.BASE_DIR / "static" / "cache")


@st.cache_resource
def _datacenter(_settings) -> DatacenterClient | None:
    """데이터센터 요청 창구 (앱 프로세스에 하나) — 시세 · 카드 상세 · 강화 부스트가 같은 요청 간격을 나눠 쓴다."""
    return DatacenterClient(_settings.market_interval) if _settings.market_enabled else None


@st.cache_resource
def _cards(_settings, _db) -> CardInfoService:
    """카드 상세 수집기 (앱 프로세스에 하나) — 특성 · 능력치 · 급여를 데이터센터에서 받아 SQLite 에 둔다."""
    return CardInfoService(_datacenter(_settings), _db, _settings.market_enabled)


@st.cache_resource
def _market(_settings, _db, _catalog) -> MarketService:
    """시세 수집기 (앱 프로세스에 하나). 켜질 때 한 번 — 꺼져 있던 동안 밀린 시세를 뒤쪽 우선순위로 따라잡는다."""
    def name_of(spid: int) -> str | None:
        return (_catalog.by_spid.get(spid) or {}).get("name", "").strip() or None

    market = MarketService(_datacenter(_settings), _db, _settings.market_ttl_hours, _settings.market_enabled,
                           name_of=name_of, current_ttl_minutes=_settings.market_current_ttl_minutes,
                           refresh_minutes=_settings.market_refresh_minutes)
    queued = market.catch_up(_settings.market_catch_up)
    if queued:
        log.info("시세 따라잡기 %d건 예약 (앱이 꺼져 있던 동안)", queued)
    return market


@st.cache_resource
def _teamcolors(_settings, _db) -> TeamColorService:
    """팀컬러 (앱 프로세스에 하나) — 목록은 하루마다 새로 받아 새 팀컬러를 더하고, 단계별 효과는 쓸 때 받아 둔다."""
    client = _datacenter(_settings)

    def clubs() -> set[str]:   # 선수 검색의 클럽 이름 (일주일 둔다) — 4xxxx 번호 중 클럽을 가려낸다
        data = _db.dc_get("teams", 24 * 7)
        if data is None and client is not None:
            data = client.teams()
            _db.dc_put("teams", data)
        return set((data or {}).get("teams", {}))

    return TeamColorService(client, _db, _settings.market_enabled, clubs=clubs)


def code_version(*files: str) -> str:
    """그 파일들이 마지막으로 바뀐 때 — cache_resource 의 키에 넣어, 앱을 켠 채 코드가 바뀌면 새 객체를 만들게 한다.
    cache_resource 는 함수 코드가 그대로면 예전 객체를 계속 돌려준다 — 서비스 코드만 바뀌면 화면은 새 코드인데 객체는 예전
    코드라 'object has no attribute …' 가 났다 (예: 'DatacenterClient' object has no attribute 'search_options')."""
    stamps = []
    for name in files:
        try:
            stamps.append(str((config.BASE_DIR / name).stat().st_mtime_ns))
        except OSError:
            stamps.append("-")
    return ":".join(stamps)


@st.cache_resource(max_entries=1)
def _finder(_settings, _db, _catalog, code: str) -> CardFinder:
    """조건 검색 (앱 프로세스에 하나 · 코드가 바뀌면 새로) — 검색 화면의 목록(국적 · 리그 · 클럽 · 특성)과 레전드 선수를
    한 번 읽어 둔다. 데이터센터 창구는 _post 만 쓴다 (창구가 예전 객체여도 된다)."""
    return CardFinder(_datacenter(_settings), _db, _catalog, _teamcolors(_settings, _db))


@st.cache_resource(max_entries=1)
def _daily(_settings, _db, code: str) -> DailyBoard:
    """데일리 기록 · 차트 (앱 프로세스에 하나 · 코드가 바뀌면 새로) — 하루에 한 번 받아 SQLite 에 둔다."""
    return DailyBoard(_datacenter(_settings), _db)


@st.cache_resource
def _stats(_settings, _db) -> StatsCrawler:
    """모든 카드의 능력치 · 급여 수집 (앱 프로세스에 하나) — 켜질 때 시작해 뒤에서 이어서 모은다."""
    crawler = StatsCrawler(_datacenter(_settings), _db, _settings.market_enabled and _settings.stats_enabled,
                           _settings.stats_interval, _settings.stats_refresh_days)
    crawler.start()
    return crawler


_index_ready = False


def _theme() -> str:
    """화면 테마 (dark · light). 세션의 첫 실행에는 브라우저가 아직 테마를 알려 주지 않아 'light' 로 와서, 그대로 쓰면
    처음 들어왔을 때만 버튼이 흰 면으로 그려졌다 — 첫 실행은 설정 파일(.streamlit/config.toml)의 테마를 따른다."""
    configured = st.get_option("theme.base")
    reported = st.context.theme.type if hasattr(st, "context") else None
    if not st.session_state.get("_theme_known"):
        st.session_state._theme_known = True
        return configured or reported or "dark"
    return reported or configured or "dark"


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
        db = _database(settings)
        nexon, catalog = _catalog(settings, db)
    except Exception as exc:  # noqa: BLE001 — 화면에는 안내만, 상세는 로그로
        log.exception("서비스 준비 실패")
        _stop("error", friendly_error(exc))
    global _index_ready
    if not _index_ready:   # 비어 있지 않은 것을 한 번 확인하면 그다음부터는 묻지 않는다 (매 화면 Chroma 조회를 줄인다)
        if store.meta_count() == 0:
            _stop("warning", "선수 메타데이터 색인이 비어 있습니다. 터미널에서 `python build_index.py`를 실행한 뒤 새로고침하세요.")
        _index_ready = True

    curated = _curated(str(settings.items_path))
    curated_ids = {p["player_id"] for p in curated}
    prefetcher = _background(settings, db, catalog, collector.wiki, curated)
    ui_parts.use_image_cache(_images(db).resolve)   # 매번 — ui_parts 가 다시 읽혀도(코드 수정) 끼워져 있게
    ui_parts.use_card_frames(_images(db).frame)
    st.session_state._tile_wait = set()   # 이번 화면의 타일 중 카드 상세를 기다리는 카드 (tile_watch)
    # 미리 분석한 카드 + 앱에서 열어 분석한 카드(SQLite)
    players = curated + [p for p in db.all_players() if p["player_id"] not in curated_ids]
    by_pid = {}
    for p in players:
        if p.get("wiki_title"):
            by_pid.setdefault(p["pid"], p)
    theme = _theme()
    for key, default in (("page", "home"), ("player_id", None), ("chats", {}), ("results", {}),
                         ("explore_results", None), ("pending_question", None), ("cmp_a", None), ("cmp_b", None),
                         ("me_result", None)):
        if key not in st.session_state:
            st.session_state[key] = default
    return Ctx(settings, players, {p["player_id"]: p for p in players}, by_pid, catalog, nexon, model, store,
               collector, graph, AiCache(db), db, prefetcher, Favorites(db), theme, curated_ids,
               _market(settings, db, catalog), _cards(settings, db), _datacenter(settings), _teamcolors(settings, db),
               _stats(settings, db),
               _finder(settings, db, catalog, code_version("services/finder.py", "data_prep/datacenter.py")),
               _daily(settings, db, code_version("services/daily.py", "data_prep/datacenter.py")))


# ── 이동 · 브라우저 뒤로가기 ─────────────────────────────────────────
# 화면을 옮길 때마다 브라우저 방문 기록에도 한 칸(#home · #player/866200104 …)을 넣는다. 그래서 브라우저의 ← · → 도
# 이 앱의 직전 화면으로 간다 (다른 사이트로 나가지 않는다). 칸마다 그 화면의 입력(검색어 · 필터 · AI 결과)을 남겨,
# 뒤로 돌아오면 떠날 때 그대로 보인다. 🏠 · 로고만 새 칸으로 처음 상태의 홈을 연다.
#   nav_log: [{page, player_id, inputs}] — 브라우저 기록과 같은 순서, nav_pos: 지금 칸
#   nav_op: 브라우저 기록에 할 일 (push · back · replace) — nav_bridge(브라우저 쪽 JS)가 한 번씩만 한다

KEEP_INPUTS = {   # 다른 화면에 다녀와도 그대로 둘 입력 (Streamlit 은 화면에 없는 입력칸의 값을 지운다)
    "home": ("q", "q_season", "explore_results", "chips"),
    "players": ("pl_q", "pl_season", "pl_kind", "pl_sort", "pl_group", "pl_page", "pl_view"),
    "ranking": ("rank_group", "rank_kind", "rank_sort", "rank_min", "rank_page"),
}
FRESH_HOME = {"q": "", "q_season": None, "explore_results": None}
NAV_JS = """
export default function(component) {
  const { data, setTriggerValue } = component;
  const nav = window.__fclabNav || (window.__fclabNav = { ignore: 0 });
  nav.trigger = setTriggerValue;
  if (!nav.listening) {   // 브라우저 ← · → — 그 칸의 번호와 주소를 앱에 알린다
    nav.listening = true;
    window.addEventListener("popstate", (event) => {
      if (nav.ignore > 0) { nav.ignore -= 1; return; }   // 앱의 '← 뒤로'가 부른 history.back()
      const state = event.state || {};
      if (state.fclab !== undefined)
        nav.trigger("moved", { seq: state.fclab, sid: state.sid, route: location.hash.slice(1), at: Date.now() });
    });
  }
  const op = data.sid + ":" + data.op_id;
  if (nav.done === op) return;   // 화면이 다시 그려질 때마다 불린다 — 같은 지시는 한 번만
  if (nav.sid !== data.sid) {   // 새 세션 (처음 열기 · 새로 고침 · 서버를 다시 켠 뒤) — 주소의 화면(#player/…)을 다시 연다
    nav.sid = data.sid;
    const route = location.hash.slice(1);
    if (data.seq === 0 && route && route !== data.label) {
      nav.done = op;
      setTriggerValue("opened", { route, at: Date.now() });
      return;
    }
  }
  nav.done = op;
  const state = { fclab: data.seq, sid: data.sid }, url = location.pathname + location.search + "#" + data.label;
  if (data.op === "push") history.pushState(state, "", url);
  else if (data.op === "back") { nav.ignore += 1; history.back(); }
  else history.replaceState(state, "", url);
}
"""
_components: dict[tuple[int, str], object] = {}


def browser_component(name: str, **parts):
    """브라우저 쪽 부품(st.components.v2)을 런타임마다 한 번 등록해 돌려준다 — 런타임마다 등록부가 따로라서.
    브라우저 없이 도는 점검(AppTest)에서는 None (부품 없이 그린다)."""
    from streamlit.runtime import Runtime

    if not Runtime.exists():
        return None
    registry = Runtime.instance().bidi_component_registry
    key = (id(registry), name, hash(tuple(sorted(parts.items()))))   # CSS · JS 를 고치면 새로 등록 (같은 이름은 덮어쓴다)
    if key not in _components:
        _components[key] = st.components.v2.component(name, **parts)
    return _components[key]


def _entry(page: str, player_id: str | None = None) -> dict:
    return {"page": page, "player_id": str(player_id) if page == "player" and player_id else None, "inputs": {}}


def _route(entry: dict) -> str:
    return f"player/{entry['player_id']}" if entry["page"] == "player" else entry["page"]


def _nav_op(op: str) -> None:
    st.session_state.nav_op = op
    st.session_state.nav_op_id = st.session_state.get("nav_op_id", 0) + 1


def _leave() -> None:
    """떠나는 화면의 입력을 지금 칸에 적어 둔다 — 뒤로 돌아오면 그대로 되살린다."""
    entry = st.session_state.nav_log[st.session_state.nav_pos]
    entry["inputs"] = {k: st.session_state[k] for k in KEEP_INPUTS.get(entry["page"], ()) if k in st.session_state}


def _arrive(entry: dict) -> None:
    st.session_state.page = entry["page"]
    st.session_state.ai_dialog = st.session_state.cmp_dialog = st.session_state.place_dialog = None
    if entry["player_id"]:
        st.session_state.player_id = entry["player_id"]
    for key in KEEP_INPUTS.get(entry["page"], ()):
        if key in entry["inputs"]:
            st.session_state[key] = entry["inputs"][key]


def _push(entry: dict, inputs: dict | None = None) -> None:
    """새 화면 — 지금 칸 뒤(앞으로 가기 기록)는 버리고 새 칸을 붙인다. 브라우저 기록도 같이."""
    here = st.session_state.nav_log[st.session_state.nav_pos]
    if (here["page"], here["player_id"]) == (entry["page"], entry["player_id"]):
        return
    _leave()
    entry["inputs"] = dict(inputs or {})
    del st.session_state.nav_log[st.session_state.nav_pos + 1 :]
    st.session_state.nav_log.append(entry)
    st.session_state.nav_pos = len(st.session_state.nav_log) - 1
    for key, value in (inputs or {}).items():
        st.session_state[key] = value
    _arrive(entry)
    _nav_op("push")


def go(page: str) -> None:
    _push(_entry(page, st.session_state.player_id))


def go_home() -> None:
    """🏠 · 로고 — 검색어 · 시즌 · AI 결과를 지운 처음 홈 (추천 검색어도 새로). 다른 화면에서 오면 새 칸으로 열어,
    뒤로 가면 전의 홈이 그대로 있다. 이미 홈이면 그 자리에서 처음 상태로."""
    st.session_state.pop("chips", None)
    if st.session_state.nav_log[st.session_state.nav_pos]["page"] == "home":
        st.session_state.update(FRESH_HOME)
        return
    _push(_entry("home"), FRESH_HOME)


def go_back() -> None:
    if st.session_state.nav_pos == 0:
        return
    _leave()
    st.session_state.nav_pos -= 1
    _arrive(st.session_state.nav_log[st.session_state.nav_pos])
    _nav_op("back")


def open_player(player_id: str) -> None:
    _push(_entry("player", player_id))


def _route_entry(route: str) -> dict | None:
    page, _, player_id = route.partition("/")
    if page in PAGE_NAMES and (page != "player" or player_id.isdigit()):
        return _entry(page, player_id)
    return None


def _on_browser_move() -> None:
    """브라우저 ← · → — 이 세션이 만든 칸이면 그 칸으로(입력까지 그대로), 전 세션의 칸이면 그 주소의 화면을 지금 칸에."""
    moved = (st.session_state.get("nav_bridge") or {}).get("moved") or {}
    seq = moved.get("seq")
    _leave()
    if moved.get("sid") == st.session_state.nav_sid and isinstance(seq, int) and 0 <= seq < len(st.session_state.nav_log):
        st.session_state.nav_pos = seq
        _arrive(st.session_state.nav_log[seq])
    elif entry := _route_entry(moved.get("route", "")):
        st.session_state.nav_log[st.session_state.nav_pos] = entry
        _arrive(entry)
        _nav_op("replace")


def _on_reopen() -> None:
    """새로 고침 — 주소에 있던 화면(#player/866200104 · #ranking …)을 첫 칸으로 다시 연다."""
    entry = _route_entry(((st.session_state.get("nav_bridge") or {}).get("opened") or {}).get("route", ""))
    if entry:
        st.session_state.nav_log[0] = entry
        _arrive(entry)
    _nav_op("replace")


def nav_bridge() -> None:
    """브라우저 방문 기록과 앱의 이동 기록을 맞춘다 — 화면 맨 위에서 한 번 부른다."""
    if "nav_log" not in st.session_state:
        st.session_state.nav_log = [_entry(st.session_state.page, st.session_state.player_id)]
        st.session_state.nav_pos = 0
        st.session_state.nav_sid = uuid.uuid4().hex[:8]
        _nav_op("replace")
    bridge = browser_component("fclab_nav", js=NAV_JS)
    if bridge is None:   # 브라우저 없이 도는 점검 — 앱 안 이동 기록만
        return
    here = st.session_state.nav_log[st.session_state.nav_pos]
    with st.container(key="nav_bridge_box"):
        bridge(key="nav_bridge", data={"seq": st.session_state.nav_pos, "label": _route(here),
                                       "op": st.session_state.nav_op, "op_id": st.session_state.nav_op_id,
                                       "sid": st.session_state.nav_sid},
               on_moved_change=_on_browser_move, on_opened_change=_on_reopen, height=0)


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
    """← 뒤로 — 검색창 옆 · 상세 화면 맨 위에 둔다 (상단 메뉴가 아니라 지금 보고 있는 곳 가까이). 브라우저 ← 와 같다."""
    (container or st).button("← 뒤로", key=f"back_{where}", on_click=go_back, width="stretch",
                             disabled=not st.session_state.get("nav_pos"), help="이전 화면으로")


def top_bar() -> None:
    """로고(누르면 처음 홈) · 메뉴. 넓은 화면은 한 줄, 좁은 화면에서만 다음 줄로 (ui_parts.css 의 .st-key-navbar)."""
    nav_bridge()
    with st.container(key="navbar"):
        # 칸 너비는 글자 길이에 맞춘다 — 모두 같으면 가장 긴 '🧠 스쿼드 · 전술'이 고른(굵은 초록) 버튼일 때 칸을 넘쳤다
        brand, *cols = st.columns([1.8] + [0.6 + 0.1 * len(label) for _, label in NAV], vertical_alignment="center")
        with brand.container(key="nav_brand_box"):   # 로고는 글자 그대로 — 위에 투명 버튼을 겹쳐 누르면 처음 홈
            st.markdown('<div class="fc-logo">⚽ FC LAB</div>', unsafe_allow_html=True)
            st.button("FC LAB 홈으로", key="nav_brand", on_click=go_home, width="stretch")
        active = NAV_OF.get(st.session_state.page, st.session_state.page)
        for col, (key, label) in zip(cols, NAV):
            col.button(label, key=f"nav_{key}", on_click=go_home if key == "home" else go, args=() if key == "home" else (key,),
                       width="stretch", type="primary" if active == key else "tertiary")


# ── 선수 찾기 · 열기 ─────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


SEARCH_CAP = 1500   # 이름 · 시즌 검색에서 OVR 로 줄 세울 최대 장수


def card_ovrs(ctx: Ctx, spids: list[int]) -> dict[int, int]:
    """카드마다 +1 OVR — 받아 둔 카드 상세 · 모은 능력치(선수 검색)에서. 모르는 카드는 빠진다."""
    out = {s: i["ovr"] for s, i in ctx.cards.get(spids).items() if i.get("ovr")}
    out.update({s: r["ovr"] for s, r in ctx.db.card_stats([s for s in spids if s not in out]).items() if r.get("ovr")})
    return out


def by_ovr(ctx: Ctx, spids: list[int]) -> list[int]:
    """같은 선수의 카드(시즌)를 늘어놓을 때는 항상 OVR 높은 순 — OVR 을 모르는 카드는 뒤에 (그 안에서는 받은 순서 = 최신 시즌부터)."""
    ovrs = card_ovrs(ctx, spids)
    return sorted(spids, key=lambda s: (s not in ovrs, -ovrs.get(s, 0)))


def _learn_ovrs(ctx: Ctx, query: str) -> None:
    """이름으로 데이터센터 선수 검색 한 번 — 그 이름이 들어간 선수 카드 전부의 OVR · 급여 · 주발 · 시세를 DB 에 (하루에 한 번)."""
    key = f"name_search:{_norm(query)}"
    if ctx.datacenter is None or ctx.db.dc_get(key, 24) is not None:
        return
    try:
        with st.spinner("선수 OVR 을 데이터센터에서 받는 중… (이 이름은 처음 한 번만)"):
            rows = ctx.datacenter.player_list(query.strip())
    except Exception as exc:  # noqa: BLE001 — 못 받아도 검색은 된다 (OVR 을 모르는 카드는 뒤로)
        log.warning("이름 검색 OVR 받기 실패: %s", exc)
        return
    ctx.db.put_listing(rows)   # 현재가 · 급여 · OVR · 포지션 · 주발 (card_list · market · card_stats)
    ctx.db.dc_put(key, len(rows))


def name_parts(query: str) -> list[str]:
    """쉼표로 적은 여러 선수 이름 — '손흥민, 케인 ,음바페' → ['손흥민', '케인', '음바페'] (빈 칸 · 같은 이름은 뺀다)."""
    return list(dict.fromkeys(part.strip() for part in (query or "").split(",") if part.strip()))


def search_players(ctx: Ctx, query: str, seasons: tuple[int, ...] | None = None, limit: int = 24) -> list[dict]:
    """이름 검색 + 시즌(클래스, 갈래를 합친 시즌이면 여럿) 거르기 — 분석해 둔 카드와 전체 88,359장에서 찾아 OVR 높은 순으로.

    OVR 을 모르는 카드는 최신 시즌 순으로 뒤에 둔다. 이름으로 찾았는데 모르는 카드가 있으면 데이터센터 선수 검색 한 번으로
    그 이름의 카드 OVR 을 받아 DB 에 둔다 — 다음부터는 바로 정렬된다. 이름 없이 시즌만 고르면 그 시즌 카드를 둘러본다.
    쉼표로 여러 이름을 적으면 이름마다 찾아 합친다 (모두 OVR 높은 순).
    """
    parts = name_parts(query)
    if len(parts) > 1:
        found: dict[str, dict] = {}
        for part in parts:
            for p in search_players(ctx, part, seasons, limit):
                found.setdefault(p["player_id"], p)
        ovrs = card_ovrs(ctx, [p["spid"] for p in found.values()])
        return sorted(found.values(), key=lambda p: (p["spid"] not in ovrs, -ovrs.get(p["spid"], 0), -split_spid(p["spid"])[0]))[:limit]
    q = _norm(query)
    if not q and not seasons:
        return []

    def keep(spid: int) -> bool:
        return not seasons or split_spid(spid)[0] in seasons

    analyzed = {p["player_id"]: p for p in ctx.players if (not q or q in _norm(p["name"])) and keep(p["spid"])}
    if q:
        hits = [h["spid"] for h in ctx.catalog.search(query, limit=2000)]
    else:
        hits = [c["id"] for c in ctx.catalog.cards if keep(c["id"])]
    spids = list(dict.fromkeys([p["spid"] for p in analyzed.values()] + [s for s in hits if keep(s)]))[:SEARCH_CAP]
    ovrs = card_ovrs(ctx, spids)
    if q and len(q) >= 2 and any(s not in ovrs for s in spids[:200]):
        _learn_ovrs(ctx, query)
        ovrs = card_ovrs(ctx, spids)
    spids.sort(key=lambda s: (s not in ovrs, -ovrs.get(s, 0), -split_spid(s)[0]))
    return [analyzed.get(str(s)) or ctx.catalog.basic_player(s) for s in spids[:limit]]


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


def tile_grid(ctx: Ctx, players: list[dict], key: str, cols: int = 6, ranked: bool = False,
              kpi=None, reasons: dict[str, str] | None = None, badges: dict[str, str] | None = None, grade: int = 1) -> None:
    """카드 타일 묶음 — 카드 전체가 버튼이다 (ui_parts.css 의 .st-key-tile_* : 타일 위에 투명 버튼을 겹친다).

    badges(랭킹 순위) · reasons(AI 추천 이유)는 카드 안에 넣는다 — 카드 밖에 따로 두면 겹치거나 줄이 어긋난다.
    grade 를 주면 그 강화의 OVR 과 강화 표시로 (상세 검색의 강화 등급).
    """
    players = with_saves(ctx, players)
    spids = [p["spid"] for p in players]
    known = card_basics(ctx, spids)
    missing = [s for s in spids if not known.get(s, {}).get("info")]
    if missing:   # 급여 · 주발 · OVR 이 없는 카드는 뒤에서 받고, 다 받으면 tile_watch 가 화면을 다시 그린다
        ctx.cards.want(missing)
        st.session_state.setdefault("_tile_wait", set()).update(missing)
    with st.container(key=f"grid_{key}"):   # 좁은 화면에서 줄바꿈 (ui_parts.css 의 st-key-grid_*)
        for start in range(0, len(players), cols):
            for offset, (col, p) in enumerate(zip(st.columns(cols), players[start : start + cols])):
                card = known.get(p["spid"], {})
                with col, st.container(key=f"tile_{key}_{p['player_id']}"):
                    ovr = card.get("ovr") + GRADE_OVR[grade] if card.get("ovr") else None
                    st.markdown(ui_parts.tile(p, rank=start + offset + 1 if ranked else None, kpi=kpi(p) if kpi else None,
                                              badge=(badges or {}).get(p["player_id"], ""),
                                              note=(reasons or {}).get(p["player_id"], ""), ovr=ovr,
                                              position=card.get("position"), pay=card.get("pay"), foot=card.get("foot"),
                                              grade=grade if grade > 1 else None),
                                unsafe_allow_html=True)
                    st.button(f"{p['name']} · {p['season_short']} 보기", key=f"{key}_{p['player_id']}",
                              on_click=open_player, args=(p["player_id"],), width="stretch")


def card_basics(ctx: Ctx, spids: list[int]) -> dict[int, dict]:
    """카드마다 {ovr(+1), position(주 포지션), pay, foot, info(카드 상세가 있나)} — 받아 둔 카드 상세 → 모은 능력치
    (선수 검색) 순으로. 급여는 선수 검색 값이 더 자주 새로 오니 그것을 먼저. 둘 다 없으면 빠진다."""
    infos = ctx.cards.get(spids)
    stats = ctx.db.card_stats(spids)
    out = {}
    for spid in spids:
        info, row = infos.get(spid) or {}, stats.get(spid) or {}
        if not info and not row.get("ovr"):
            continue
        positions = row.get("positions") or []
        out[spid] = {"ovr": info.get("ovr") or row.get("ovr"),
                     "position": info.get("position") or (positions[0][0] if positions else None),
                     "pay": row.get("pay") if row.get("pay") is not None else info.get("pay"),
                     "foot": info.get("foot_lr") or row.get("foot"), "info": bool(info)}
    return out


@st.fragment(run_every=2)
def daily_wait(ctx: Ctx) -> None:
    """데일리 차트 · 기록을 받는 동안 2초마다 — 다 받으면(또는 받지 못하고 끝나면) 한 번 다시 그린다."""
    if not ctx.daily.pending() and not dialog_open():
        st.rerun()
    st.caption("⏳ 데이터센터 데일리 기록 · 차트를 받는 중… (하루에 한 번)")


def dialog_open() -> bool:
    """모달(AI 추천 스쿼드 · 바로 비교 · 선수 배치)이 열려 있나 — 열려 있는 동안에는 받는 중 감시가 화면을 다시 그리지 않는다."""
    return bool(st.session_state.get("ai_dialog") or st.session_state.get("cmp_dialog") or st.session_state.get("place_dialog"))


def tile_watch(ctx: Ctx) -> None:
    """이번 화면의 타일 중 카드 상세(급여 · 주발 · OVR)를 받는 카드가 있으면, 다 받았을 때 한 번 다시 그린다."""
    waiting = list(st.session_state.get("_tile_wait") or ())
    if waiting and ctx.cards.pending(waiting):
        _tile_watch_live(ctx, waiting)


@st.fragment(run_every=3)
def _tile_watch_live(ctx: Ctx, waiting: list[int]) -> None:
    if not ctx.cards.pending(waiting) and not dialog_open():
        st.rerun()


def crests_of(ctx: Ctx, info: dict | None) -> list[tuple[str, str]]:
    """국기 + 이 카드가 드는 소속 팀컬러 엠블럼 [(이름, 이미지 주소)] — 국가 팀컬러는 국기로 대신한다.
    이름이 같은 팀컬러(갈래만 다른 것 — Untouchable Champions 두 개)는 한 번만."""
    if not info:
        return []
    icons = st.session_state.get("_tc_icons")
    if icons is None:   # 화면마다 800개를 다시 풀지 않게 세션에 한 번
        icons = {tc["id"]: (tc.get("icon", ""), tc.get("kind", "")) for tc in ctx.teamcolors.all()}
        if icons:
            st.session_state._tc_icons = icons
    out = [(info.get("nation", ""), info["nation_flag"])] if info.get("nation_flag") else []
    for t in (info.get("teamcolors") or {}).get("소속", []):
        icon, kind = icons.get(t["id"], ("", ""))
        if icon and kind != "국가" and all(name != t["name"] and url != icon for name, url in out):
            out.append((t["name"], icon))
    return out


def card_row_list(ctx: Ctx, players: list[dict], key: str, notes: dict[str, str] | None = None, grade: int = 1) -> int:
    """선수 줄 목록 (공식 스쿼드 메이커의 선수 목록처럼) — 줄 전체가 버튼(상세 페이지). 카드 상세가 없는 카드는 받기 시작하고
    아직 받는 중인 장수를 돌려준다 (부르는 쪽이 조각을 다시 그린다). grade 를 주면 그 강화의 OVR · 능력치로."""
    spids = [p["spid"] for p in players]
    ctx.cards.want(spids)
    infos = ctx.cards.get(spids)
    stats = ctx.db.card_stats([s for s in spids if s not in infos])
    for p in players:
        info, known = infos.get(p["spid"]), stats.get(p["spid"]) or {}
        positions = known.get("positions") or []
        ovr = (info or {}).get("ovr") or known.get("ovr")
        position = (info or {}).get("position") or (positions[0][0] if positions else None)
        with st.container(key=f"prow_{key}_{p['player_id']}"):
            st.markdown(ui_parts.card_row(p, info, grade=grade, ovr=ovr + GRADE_OVR[grade] if ovr else ovr, position=position,
                                          pay=(info or {}).get("pay", known.get("pay")),
                                          foot=(info or {}).get("foot_lr") or known.get("foot"), crests=crests_of(ctx, info),
                                          note=(notes or {}).get(p["player_id"], "")), unsafe_allow_html=True)
            if st.button(f"{p['name']} · {p['season_short']} 보기", key=f"{key}_{p['player_id']}", width="stretch"):
                open_player(p["player_id"])   # 조각(fragment) 안에서도 화면이 바뀌게 — 콜백 대신 눌린 뒤 앱 전체를 다시
                st.rerun()
    return ctx.cards.pending(spids)


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
