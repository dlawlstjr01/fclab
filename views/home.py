"""🏠 홈 — 검색이 첫 화면이다. 이름 · 시즌으로 바로 찾고(네트워크 0회), 말로 찾고(AI), 랭커 픽을 둘러본다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from html import escape

import streamlit as st

import config
from data_prep import namu
from data_prep.players import stat_values
from services import aisearch
from services.explore import describe, explore_players, suggest_conditions
from views.common import Ctx, back_button, daily_wait, friendly_error, get_player, log, search_players, tile_grid
from views.widgets import pager, season_family, season_select, set_page

AI_PAGE = 12   # AI 찾기 결과 한 화면 (2줄 × 6장) — 많으면 쪽 번호로 넘긴다
AI_VIEWS = ("선수마다 대표 카드", "모든 시즌 카드")


def _run_ai_search(ctx: Ctx, condition: str) -> None:
    """말 → 조건에 맞는 길 (services/aisearch.py): 랭커 기록 · 카드 능력치 · 나무위키 → AI 명단 → FC 온라인 DB.
    아무것도 못 찾으면 분석해 둔 카드에서 뜻이 가까운 카드(예전 방식). 진행 상황은 단계 상자(st.status)로."""
    try:
        with st.status("🤖 AI로 찾는 중…", expanded=True) as status:
            found = aisearch.search(condition, finder=ctx.finder, model=ctx.model, namu=namu.client(config.wiki_contact()),
                                    db=ctx.db, catalog=ctx.catalog, players=ctx.players, daily=ctx.daily,
                                    progress=status.write)
            labels = {"ranker": f"랭커 기록 순위 {len(found.get('rows', []))}장", "finder": f"카드 {len(found.get('spids', []))}장",
                      "namu": f"{len((found.get('namu') or {}).get('players', []))}명 · 다른 카드 {len(found.get('extra', []))}장"}
            status.update(label=f"🤖 AI로 찾기 — {labels.get(found['mode'], '예전 방식으로')}", state="complete", expanded=False)
        if found["mode"] == "none":
            found = {"condition": condition, **explore_players(condition, ctx.store, ctx.model, ctx.by_id, ctx.settings)}
        st.session_state.explore_results = found
        st.session_state.ai_page = st.session_state.ai_page2 = 0
        ctx.db.log_search(condition)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI 검색 실패")
        st.error(friendly_error(exc))


def _pages(ctx: Ctx, spids: list[int], key: str, page_key: str, reasons: dict[str, str] | None = None) -> None:
    """카드 타일을 한 화면 12장씩 (쪽 번호)."""
    total = len(spids)
    pages = (total + AI_PAGE - 1) // AI_PAGE
    page = min(st.session_state.get(page_key, 0), pages - 1)
    shown = [get_player(ctx, str(s)) for s in spids[page * AI_PAGE: (page + 1) * AI_PAGE]]
    tile_grid(ctx, shown, key=f"{key}{page}", reasons=reasons)
    if pages > 1:
        pager(key, page, pages, set_page, (page_key,))
        st.markdown(f'<div class="fc-pageinfo">{page * AI_PAGE + 1}~{min(total, (page + 1) * AI_PAGE)}번째 · 모두 {total}장</div>',
                    unsafe_allow_html=True)


def _namu_results(ctx: Ctx, found: dict) -> None:
    """AI 로 찾은 선수 — 세 단계(나무위키 문서 · AI 명단 · FC 온라인 DB)를 칸으로 보이고, 선수마다 대표 카드(조건에 맞는 카드 중
    OVR 이 가장 높은 것) 또는 모든 시즌 카드를 12장씩. DB 에 없는 선수 · 조건으로 뺀 선수는 접어 둔다."""
    res, filters = found.get("namu") or {}, found.get("filters")
    players = res.get("players", [])
    st.markdown(f'<div class="fc-section">🤖 AI로 찾기 — "{escape(found["condition"])}"<small>{len(players)}명 · 1차 나무위키 → '
                '2차 AI 명단 → 3차 FC 온라인 DB 순서로 찾았습니다</small></div>', unsafe_allow_html=True)
    if res:
        docs = " · ".join(f'<a href="{escape(d["url"])}" target="_blank" rel="noopener">{escape(d["title"])}</a>'
                          for d in res.get("docs", []))
        first = (f"문서 {docs}" if docs else "읽은 문서 없음") + f" · 검색 결과 {res.get('search', 0)}건"
        if res.get("error"):
            first = f"닿지 않음 — {escape(res['error'])} (AI 지식만으로)"
        cells = [("📚 1차 나무위키", first),
                 ("🧠 2차 AI 명단", f"<b>{res.get('listed', 0)}명</b> — 나무위키 근거 {res.get('cited', 0)} · "
                                   f"AI 지식 {res.get('listed', 0) - res.get('cited', 0)}"),
                 ("🗂️ 3차 FC 온라인 DB", f"<b>{len(players)}명</b> 카드 있음 · DB 에 없음 {len(res.get('missing', []))} · "
                                       f"조건으로 뺌 {len(res.get('dropped', []))}")]
        st.markdown('<div class="fc-sum fc-steps">' + "".join(f"<div><span>{k}</span><small>{v}</small></div>" for k, v in cells)
                    + "</div>", unsafe_allow_html=True)
    # 실제로 쓴 조건만 — 수상 · 업적 말로 읽은 시즌 팀컬러(발롱도르 → Ballon d'Or 카드)는 '팀컬러'라고 말했을 때만 쓴다 (_facts)
    used = filters if filters is None or "팀컬러" in found["condition"].replace(" ", "") else replace(filters, player_teamcolors=())
    if used is not None and not used.empty():
        read = "".join(f'<span class="fc-chip">{escape(c)}</span>' for c in used.chips(ctx.finder.season_names()))
        st.markdown(f'<div class="fc-chips"><span class="fc-chip lead">AI가 읽은 조건</span>{read}</div>', unsafe_allow_html=True)
    if found.get("unknown"):
        st.caption("목록에서 찾지 못해 빼고 찾은 말: " + ", ".join(found["unknown"]))
    if players:
        st.session_state.setdefault("ai_view", AI_VIEWS[0])
        view = st.segmented_control("보기", AI_VIEWS, key="ai_view", label_visibility="collapsed",
                                    on_change=lambda: st.session_state.update(ai_page=0)) or AI_VIEWS[0]
        reasons = {}
        for p in players:
            why = (p.get("why") or "조건에 맞음") + (" · AI 지식" if p.get("src") == "AI 지식" else "")
            for s in (p["spids"] if view == AI_VIEWS[1] else [p["spid"]]):
                reasons[str(s)] = why
        spids = [s for p in players for s in (p["spids"] if view == AI_VIEWS[1] else [p["spid"]])]
        _pages(ctx, spids, "ai", "ai_page", reasons)
        if res.get("unsure"):
            st.caption("국적 · 클럽을 데이터센터로 다 확인하지 못한 선수: " + ", ".join(res["unsure"]))
    elif res:
        st.info("AI 가 찾은 선수 중 FC 온라인에 카드가 있고 조건에 맞는 선수가 없습니다.")
    left = res.get("missing", []) + res.get("dropped", [])
    if left:
        with st.expander(f"DB 에 없는 선수 · 조건으로 뺀 선수 ({len(left)}명)"):
            lines = [f"- **{escape(m['name'])}** — FC 온라인에 카드가 없음" + (f" ({escape(m['why'])})" if m.get("why") else "")
                     for m in res.get("missing", [])]
            lines += [f"- **{escape(d['name'])}** — {escape(d['reason'])}" for d in res.get("dropped", [])]
            st.markdown("\n".join(lines))
    usage = found.get("usage")
    if usage:
        st.markdown(f'<div class="fc-section">📈 게임 안의 최고 — 랭커가 {escape(" · ".join(usage["codes"]))} 에 가장 많이 쓰는 카드'
                    f'<small>어제 데일리 차트 {escape(usage["date"])} · 공식 경기 랭커 상위 1만 명 · 조건의 현역 · 레전드로 거름</small></div>',
                    unsafe_allow_html=True)
        by_id = {}
        for r in usage["rows"]:
            by_id.setdefault(str(r["spid"]), r)
        tile_grid(ctx, [get_player(ctx, s) for s in by_id], key="aiu",
                  kpi=lambda p: f"랭커 <b>{by_id[p['player_id']]['share']:.1f}%</b> 사용 · +{by_id[p['player_id']]['grade']}강")
    if found.get("extra"):
        st.markdown(f'<div class="fc-section">📋 데이터센터 조건 검색 — 그 밖의 카드<small>{len(found["extra"])}장'
                    f'{" (OVR 높은 순 200장까지)" if len(found["extra"]) >= 190 else ""} · AI 명단에 없어도 조건에 맞는 카드'
                    '</small></div>', unsafe_allow_html=True)
        _pages(ctx, found["extra"], "aix", "ai_page2")
    if res:
        st.caption(f"출처: 나무위키 ({res.get('license', 'CC BY-NC-SA 2.0 KR')}) — 선수 명단의 근거. 카드 · 포지션 · OVR 은 "
                   "FC 온라인 데이터, 국적 · 클럽 조건은 데이터센터 선수 검색으로 확인합니다. AI 지식으로 넣은 선수는 따로 표시합니다.")


def _ranker_results(ctx: Ctx, found: dict) -> None:
    """랭커 기록으로 찾은 카드 — 분석해 둔 카드(카드별 최근 최대 20경기 평균)의 그 스탯 순위 12장씩 + 어제 데일리 기록 TOP."""
    from views.ranking import daily_value

    name, unit = found["stat_name"], found["unit"]
    rows = found["rows"]
    st.markdown(f'<div class="fc-section">🤖 AI로 찾기 — "{escape(found["condition"])}"<small>랭커 기록으로 줄 세웠습니다 — '
                f'FC 온라인 공식경기 랭커 · 카드별 최근 최대 20경기 평균 (최소 {aisearch.RANKER_MIN}경기) · '
                '나무위키 · AI 는 이 기록을 모릅니다</small></div>', unsafe_allow_html=True)
    chips = [f"기준 {name}{' (경기당)' if not unit else ''} 높은 순"]
    if found["groups"]:
        chips.append("포지션 " + " · ".join(found["groups"]))
    if found["kind"] != "전체":
        chips.append(f"{found['kind']}만")
    st.markdown('<div class="fc-chips"><span class="fc-chip lead">AI가 읽은 조건</span>'
                + "".join(f'<span class="fc-chip">{escape(c)}</span>' for c in chips) + "</div>", unsafe_allow_html=True)
    if rows:
        by_spid = {r["spid"]: r for r in rows}
        ranks = {str(r["spid"]): f"{i}위" for i, r in enumerate(rows, start=1)}
        total = len(rows)
        pages = (total + AI_PAGE - 1) // AI_PAGE
        page = min(st.session_state.get("ai_page", 0), pages - 1)
        shown = [get_player(ctx, str(r["spid"])) for r in rows[page * AI_PAGE: (page + 1) * AI_PAGE]]
        tile_grid(ctx, shown, key=f"air{page}", badges={k: v for k, v in ranks.items()},
                  kpi=lambda p: f"{escape(name)} <b>{by_spid[p['spid']]['value']:.{0 if unit else 2}f}{unit}</b> · "
                                f"{by_spid[p['spid']]['matches']}경기")
        pager("air", page, pages, set_page, ("ai_page",))
    else:
        st.info("이 포지션 · 구분으로 분석해 둔 랭커 기록이 아직 없습니다 — 아래 어제 데일리 기록을 보세요.")
    daily = found.get("daily")
    if daily is not None:
        st.markdown(f'<div class="fc-section">📅 어제 데일리 기록 — {escape(daily["label"])} TOP<small>{escape(daily["date"])} · '
                    '공식 경기 랭커 상위 1만 명이 쓴 카드 (FC 온라인 데이터센터) · 같은 포지션 · 구분만</small></div>',
                    unsafe_allow_html=True)
        if daily["rows"]:
            by_id = {}
            for r in daily["rows"]:
                by_id.setdefault(str(r["spid"]), r)   # 같은 카드는 한 번만 (순위가 높은 쪽)
            tile_grid(ctx, [get_player(ctx, s) for s in by_id], key="aird",
                      kpi=lambda p: f"{escape(daily['label'])} <b>{daily_value(daily['order'], by_id[p['player_id']]['stats'])}</b>"
                                    f" · +{by_id[p['player_id']].get('grade') or 1}강")
        else:
            st.caption("어제 기록 TOP 18 에는 이 포지션 · 구분의 카드가 없습니다.")


def _finder_results(ctx: Ctx, found: dict) -> None:
    """데이터센터 조건 검색으로 찾은 카드 — AI 가 읽은 조건(칩) · 선수마다 대표 카드(그 조건에 맞는 카드 중 가장 앞 — OVR 높은 순,
    능력치 조건이면 그 능력치 높은 순) 또는 모든 카드를 12장씩 (◀ ▶). 같은 선수가 시즌만 달리 줄줄이 나오지 않게 대표 카드가 기본."""
    filters, spids = found["filters"], found["spids"]
    total = len(spids)
    first: dict[int, int] = {}
    for spid in spids:
        first.setdefault(spid % 1_000_000, spid)
    how = (f"{escape(filters.chips()[-1])} — 카드 능력치로 줄 세웠습니다" if found.get("way") == "ability"
           else "국적 · 리그 · 클럽 · 포지션 · 레전드 · 현역 같은 사실은 FC 온라인 데이터센터 데이터로 거릅니다")
    st.markdown(f'<div class="fc-section">🤖 AI로 찾기 — "{escape(found["condition"])}"<small>{len(first)}명 · {total}장'
                f'{" (데이터센터 검색은 OVR 높은 순 200장까지)" if total >= 200 else ""} · {how}</small></div>',
                unsafe_allow_html=True)
    read = "".join(f'<span class="fc-chip">{escape(c)}</span>' for c in filters.chips(ctx.finder.season_names()))
    st.markdown(f'<div class="fc-chips"><span class="fc-chip lead">AI가 읽은 조건</span>{read}</div>', unsafe_allow_html=True)
    if found.get("unknown"):
        st.caption("목록에서 찾지 못해 빼고 찾은 말: " + ", ".join(found["unknown"]))
    if not total:
        st.info("조건에 맞는 카드가 없습니다. 조건을 줄여 보세요.")
        return
    st.session_state.setdefault("ai_view", AI_VIEWS[0])
    view = st.segmented_control("보기", AI_VIEWS, key="ai_view", label_visibility="collapsed",
                                on_change=lambda: st.session_state.update(ai_page=0)) or AI_VIEWS[0]
    _pages(ctx, list(first.values()) if view == AI_VIEWS[0] else spids, "ai", "ai_page")


def _shuffle_chips(ctx: Ctx) -> None:
    st.session_state.chips = suggest_conditions(ctx.db.search_counts())


def _use_example(text: str) -> None:
    st.session_state.q = text
    st.session_state.pending_ai = text


def _clear() -> None:
    """검색 지우고 첫 화면으로."""
    st.session_state.q = ""
    st.session_state.q_season = None
    st.session_state.explore_results = None
    st.session_state.auto_ai = None


def render(ctx: Ctx) -> None:
    analyzed = sum(1 for p in ctx.players if p.get("ranker"))
    dates = [p["ranker"]["date"] for p in ctx.players if p.get("ranker")]
    new_cards = ctx.db.new_cards(days=7)
    refreshed = ctx.db.last_ranker_refresh()   # 몇 시간마다 다시 받은 때 (services/ranker_refresh.py)
    when = ""
    if refreshed:
        at = refreshed["at"]
        when = f" · {at[11:16]} 갱신" if at[:10] == str(date.today()) else f" · {at[5:10]} {at[11:16]} 갱신"
    st.markdown(
        '<div class="fc-hero"><h1>FC 온라인 선수, <b>한 번에</b> 찾고 비교하세요</h1>'
        "<p>랭커 실전 기록 · 실제 커리어 · AI 분석을 근거와 함께. 이름으로 찾거나, 원하는 선수를 말로 설명해 보세요.</p>"
        f'<div class="stats"><div><b>{len(ctx.catalog.cards):,}</b>전체 카드</div>'
        f'<div><b>{len(ctx.catalog.by_pid):,}</b>선수</div><div><b>{analyzed:,}</b>랭커 분석 카드</div>'
        f'<div><b>{max(dates) if dates else "-"}</b>랭커 집계일{when}</div>'
        + (f'<div><b>+{new_cards:,}</b>최근 7일 새 카드</div>' if new_cards else "") + "</div></div>",
        unsafe_allow_html=True,
    )

    # 검색 줄 — ← 뒤로 · 검색어 · 시즌 · ↺ 초기화 · 🤖 AI로 찾기 (초기화는 실행 버튼 바로 왼쪽, 넓은 화면은 한 줄: ui_parts.css)
    back_col, q_col, s_col, clear_col, ai_col = st.container(key="wrap_search").columns(
        [0.9, 4, 3.4, 1, 1.4], vertical_alignment="bottom")
    back_button("home", back_col)
    query = q_col.text_input("선수 검색", key="q", placeholder="선수 이름 (예: 손흥민, 호날두)  ·  또는 조건 (예: 발 빠른 한국 윙어)",
                             label_visibility="collapsed")
    with s_col:
        season = season_select(ctx, key="q_season")
    ai_clicked = ai_col.button("🤖 AI로 찾기", key="ai_search", type="primary", width="stretch")
    searching = bool(query.strip() or season or st.session_state.explore_results)
    clear_col.button("↺ 초기화", key="q_clear", on_click=_clear, width="stretch", disabled=not searching,
                     help="검색어 · 시즌 · AI 결과를 지우고 첫 화면으로")

    if "chips" not in st.session_state:   # 자주 찾는 조건 중 무작위 — 들어올 때마다 바뀌고, 🔀 로 다시 뽑는다
        _shuffle_chips(ctx)
    # 칩은 글자 길이만큼 — 칸을 나누면 좁은 칸에서 글자가 단어 중간에 끊기고 🔀 가 줄 하나를 다 차지했다
    with st.container(key="chipbar", horizontal=True, gap="small", vertical_alignment="center"):
        for example in st.session_state.chips:
            st.button(f"💡 {example}", key=f"chip_{example}", on_click=_use_example, args=(example,), type="tertiary",
                      width="content")
        st.button("🔀", key="chip_shuffle", on_click=_shuffle_chips, args=(ctx,), type="tertiary", help="다른 추천 검색어",
                  width="content")

    pending = st.session_state.pop("pending_ai", None)
    text = query.strip()
    # 이름 · 시즌 검색 — 입력하는 즉시 (네트워크 없이). 보이는 카드는 백그라운드로 미리 분석해 둔다.
    results = search_players(ctx, query, season) if (text or season) else []
    # 이름이 맞는 카드가 없으면 그 말을 조건으로 보고 🤖 AI로 찾기를 바로 — 같은 말은 한 번만 (다시 그릴 때마다 묻지 않게)
    previous = st.session_state.explore_results or {}
    if (not pending and not ai_clicked and text and not season and not results and len(text) >= 2
            and previous.get("condition") != text and st.session_state.get("auto_ai") != text):
        st.session_state.auto_ai = text
        pending = text
        st.caption(f"'{text}' 이(가) 이름에 들어간 카드가 없어 🤖 AI로 찾습니다.")
    if (ai_clicked and text) or pending:
        _run_ai_search(ctx, pending or text)   # 진행 상황은 그 안의 단계 상자(st.status)로
    elif ai_clicked:
        st.caption("찾고 싶은 조건을 먼저 적어 주세요.")

    found = st.session_state.explore_results
    if found and found.get("mode") == "ranker":
        _ranker_results(ctx, found)
    elif found and found.get("mode") == "namu":
        _namu_results(ctx, found)
    elif found and found.get("mode") == "finder":
        _finder_results(ctx, found)
    elif found:
        items = [i for i in found["items"] if i["player_id"] in ctx.by_id]
        st.markdown(f'<div class="fc-section">🤖 AI 추천 — "{escape(found["condition"])}"<small>{len(items)}명</small></div>',
                    unsafe_allow_html=True)
        st.caption(describe(found["condition"], ctx.players, found))
        if items:
            tile_grid(ctx, [ctx.by_id[i["player_id"]] for i in items], key="ai", ranked=True,
                      reasons={i["player_id"]: i["reason"] for i in items},
                      badges={i["player_id"]: i.get("rank", "").replace("파워 랭킹 ", "") for i in items})
        else:
            st.info("조건에 맞는 선수를 찾지 못했습니다. 조건을 바꿔 보세요.")

    ctx.prefetcher.schedule([p["spid"] for p in results if p.get("lite")])
    if results:   # 이름이 맞는 카드가 있을 때만 '이름 검색 결과' — 조건을 적었을 때 '0장'을 보이지 않는다
        title = f"'{query.strip()}'" if query.strip() else ""
        # 갈래를 합친 시즌(25IM - S&P · 25IM - FB)은 묶음 이름 하나로
        where = (" · ".join(dict.fromkeys(season_family(ctx.catalog.seasons[s]["className"])[0] for s in season))
                 if season else "전체 시즌")
        st.markdown(f'<div class="fc-section">🔍 {title} 이름 검색 결과<small>{where} · {len(results)}장'
                    f'{" (더 있으면 이름을 더 정확히)" if len(results) >= 24 else ""}</small></div>', unsafe_allow_html=True)
        tile_grid(ctx, results, key="res")
    elif text and not found:
        st.info(f"'{text}' 이(가) 이름에 들어간 카드도, AI로 찾은 카드도 없습니다. 이름이나 조건(예: 일본 출신 미드필더)을 바꿔 보세요.")
    if searching:
        return

    favorite_ids = [i for i in ctx.favorites.ids() if i in ctx.by_id]
    if favorite_ids:
        st.markdown('<div class="fc-section">⭐ 즐겨찾기</div>', unsafe_allow_html=True)
        tile_grid(ctx, [ctx.by_id[i] for i in favorite_ids][:6], key="fav")

    if ctx.daily.enabled:
        _daily_sections(ctx)
        return
    _ranker_sections(ctx)   # 데이터센터를 끈 경우 — 넥슨 Open API 랭커 분석 카드로


def _daily_sections(ctx: Ctx) -> None:
    """어제 랭커가 실제로 쓴 카드 (데이터센터 데일리 기록) — 파워 랭킹 · 레전드(모든 시즌) · 수비 핵심. 시즌 · 이미지와 함께."""
    tables = {order: ctx.daily.stats(order) for order in ("ATTACKPOINT", "MATCHCOUNT", "RATING", "INTERCEPT")}
    if any(v is None for v in tables.values()):
        if ctx.daily.pending():
            st.markdown('<div class="fc-section">🏆 오늘의 파워 랭킹</div>', unsafe_allow_html=True)
            daily_wait(ctx)
            return
        if not any(tables.values()):
            _ranker_sections(ctx)
            return
    rows = {order: (data or {}).get("rows", []) for order, data in tables.items()}
    day = next((d["date"] for d in tables.values() if d), "")
    top = rows["ATTACKPOINT"]
    st.markdown(f'<div class="fc-section">🏆 오늘의 파워 랭킹 — 랭커 공격 포인트 TOP<small>{day} · 공식 경기 랭커 상위 1만 명이 '
                '쓴 카드의 경기당 골 + 도움 · FC 온라인 데이터센터 데일리 기록</small></div>', unsafe_allow_html=True)
    _daily_tiles(ctx, top, "top", lambda r: f"공격 포인트 <b>{r['stats'].get('ATTACKPOINT', 0):.1f}</b> · {r['matches']:,}경기",
                 limit=12)

    legends = ctx.finder.legends()
    seen, picked = set(), []
    for r in sorted((r for table in rows.values() for r in table), key=lambda r: -r["matches"]):
        if r["spid"] % 1_000_000 in legends and r["spid"] not in seen:
            seen.add(r["spid"])
            picked.append(r)
    st.markdown('<div class="fc-section">👑 랭커가 쓰는 레전드<small>아이콘 카드가 있는 선수의 모든 시즌 카드 · 출전 많은 순</small></div>',
                unsafe_allow_html=True)
    if picked:
        _daily_tiles(ctx, picked[:6], "legend", lambda r: f"{r['matches']:,}경기 · 평점 <b>{r['stats'].get('RATING', 0):.1f}</b>")
    else:
        st.caption("오늘 기록 TOP 에 든 레전드 카드가 없습니다.")

    st.markdown('<div class="fc-section">🛡️ 수비 핵심 — 가로채기 TOP<small>경기당 가로채기</small></div>', unsafe_allow_html=True)
    _daily_tiles(ctx, rows["INTERCEPT"], "def", lambda r: f"가로채기 <b>{r['stats'].get('INTERCEPT', 0):.1f}</b> · {r['matches']:,}경기",
                 limit=6)


def _daily_tiles(ctx: Ctx, rows: list[dict], key: str, kpi, limit: int = 6) -> None:
    """데일리 기록 카드 타일 — 같은 카드가 강화 등급만 달리 두 번 나오면 앞(높은 순위)의 것 하나만."""
    seen: set[int] = set()
    rows = [r for r in rows if not (r["spid"] in seen or seen.add(r["spid"]))][:limit]
    by_id = {str(r["spid"]): r for r in rows}
    players = [get_player(ctx, str(r["spid"])) for r in rows]
    tile_grid(ctx, players, key=key, ranked=key == "top", kpi=lambda p: kpi(by_id[p["player_id"]]))


def _ranker_sections(ctx: Ctx) -> None:
    """넥슨 Open API 랭커 분석 카드로 만든 랭킹 (데이터센터를 끈 경우)."""
    def points(p: dict) -> float:
        v = stat_values(p.get("ranker"))
        return v["goal"] + v["assist"] if v else -1

    active = [p for p in ctx.players if p.get("ranker") and p["ranker"]["match_count"] >= 10]
    st.markdown('<div class="fc-section">🏆 파워 랭킹 — 랭커 경기 공격 포인트 TOP<small>공식경기 랭커 경기당 골 + 도움 · '
                '분석해 둔 카드</small></div>', unsafe_allow_html=True)
    tile_grid(ctx, sorted(active, key=points, reverse=True)[:12], key="top", ranked=True)

    legends = ctx.finder.legends()
    picked = [p for p in active if p["pid"] in legends]
    st.markdown('<div class="fc-section">👑 랭커가 쓰는 레전드<small>아이콘 카드가 있는 선수의 모든 시즌 카드</small></div>',
                unsafe_allow_html=True)
    tile_grid(ctx, sorted(picked, key=points, reverse=True)[:6], key="legend")

    defenders = [p for p in active if p.get("position_group") in ("센터백", "풀백", "수비형 미드필더")]
    st.markdown('<div class="fc-section">🛡️ 수비 핵심 — 태클 + 블록 TOP</div>', unsafe_allow_html=True)
    tile_grid(ctx, sorted(defenders, key=lambda p: -(p["ranker"]["tackle"] + p["ranker"]["block"]))[:6], key="def")
