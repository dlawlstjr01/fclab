"""🏠 홈 — 검색이 첫 화면이다. 이름 · 시즌으로 바로 찾고(네트워크 0회), 말로 찾고(AI), 랭커 픽을 둘러본다."""

from __future__ import annotations

import streamlit as st

from html import escape

from data_prep.players import stat_values
from services.explore import describe, explore_players, suggest_conditions
from views.common import Ctx, back_button, daily_wait, friendly_error, get_player, log, search_players, tile_grid
from views.widgets import season_family, season_select

AI_PAGE = 12   # AI 찾기 결과 한 화면 (2줄 × 6장) — 많으면 ◀ ▶ 로 넘긴다


def _run_ai_search(ctx: Ctx, condition: str) -> None:
    """말 → 조건(AI) → 데이터센터 선수 검색. 조건을 하나도 못 읽으면 분석해 둔 카드에서 뜻이 가까운 카드(예전 방식)."""
    try:
        filters, unknown = ctx.finder.parse(condition, ctx.model) if ctx.finder.enabled else (None, [])
        if filters is not None and not filters.empty():
            rows = ctx.finder.search(filters)
            st.session_state.explore_results = {"condition": condition, "mode": "finder", "filters": filters,
                                                "unknown": unknown, "spids": [r["spid"] for r in rows]}
        else:
            found = explore_players(condition, ctx.store, ctx.model, ctx.by_id, ctx.settings)
            st.session_state.explore_results = {"condition": condition, **found}
        st.session_state.ai_page = 0
        ctx.db.log_search(condition)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI 검색 실패")
        st.error(friendly_error(exc))


def _finder_results(ctx: Ctx, found: dict) -> None:
    """AI 로 찾은 카드 — AI 가 읽은 조건(칩) · 데이터센터에서 거른 카드를 한 화면 12장씩 (◀ ▶)."""
    filters, spids = found["filters"], found["spids"]
    total = len(spids)
    st.markdown(f'<div class="fc-section">🤖 AI로 찾기 — "{escape(found["condition"])}"<small>{total}장'
                f'{" (데이터센터 검색은 OVR 높은 순 200장까지)" if total >= 200 else ""} · 국적 · 포지션 같은 사실은 '
                'FC 온라인 데이터센터 데이터로 거릅니다</small></div>', unsafe_allow_html=True)
    read = "".join(f'<span class="fc-chip">{escape(c)}</span>' for c in filters.chips(ctx.finder.season_names()))
    st.markdown(f'<div class="fc-chips"><span class="fc-chip lead">AI가 읽은 조건</span>{read}</div>', unsafe_allow_html=True)
    if found.get("unknown"):
        st.caption("목록에서 찾지 못해 빼고 찾은 말: " + ", ".join(found["unknown"]))
    if not total:
        st.info("조건에 맞는 카드가 없습니다. 조건을 줄여 보세요.")
        return
    pages = (total + AI_PAGE - 1) // AI_PAGE
    page = min(st.session_state.get("ai_page", 0), pages - 1)
    shown = [get_player(ctx, str(s)) for s in spids[page * AI_PAGE: (page + 1) * AI_PAGE]]
    tile_grid(ctx, shown, key=f"ai{page}")
    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("◀ 이전", key="ai_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update(ai_page=page - 1))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages} · "
                          f"{page * AI_PAGE + 1}~{min(total, (page + 1) * AI_PAGE)}번째</div>", unsafe_allow_html=True)
        next_col.button("다음 ▶", key="ai_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update(ai_page=page + 1))


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


def render(ctx: Ctx) -> None:
    analyzed = sum(1 for p in ctx.players if p.get("ranker"))
    dates = [p["ranker"]["date"] for p in ctx.players if p.get("ranker")]
    new_cards = ctx.db.new_cards(days=7)
    st.markdown(
        '<div class="fc-hero"><h1>FC 온라인 선수, <b>한 번에</b> 찾고 비교하세요</h1>'
        "<p>랭커 실전 기록 · 실제 커리어 · AI 분석을 근거와 함께. 이름으로 찾거나, 원하는 선수를 말로 설명해 보세요.</p>"
        f'<div class="stats"><div><b>{len(ctx.catalog.cards):,}</b>전체 카드</div>'
        f'<div><b>{len(ctx.catalog.by_pid):,}</b>선수</div><div><b>{analyzed:,}</b>랭커 분석 카드</div>'
        f'<div><b>{max(dates) if dates else "-"}</b>랭커 집계일</div>'
        + (f'<div><b>+{new_cards:,}</b>최근 7일 새 카드</div>' if new_cards else "") + "</div></div>",
        unsafe_allow_html=True,
    )

    back_col, q_col, s_col, ai_col, clear_col = st.container(key="wrap_search").columns(
        [0.9, 4, 3.4, 1.4, 0.9], vertical_alignment="bottom")
    back_button("home", back_col)
    query = q_col.text_input("선수 검색", key="q", placeholder="선수 이름 (예: 손흥민, 호날두)  ·  또는 조건 (예: 발 빠른 한국 윙어)",
                             label_visibility="collapsed")
    with s_col:
        season = season_select(ctx, key="q_season")
    ai_clicked = ai_col.button("🤖 AI로 찾기", key="ai_search", type="primary", width="stretch")
    searching = bool(query.strip() or season or st.session_state.explore_results)
    clear_col.button("✕ 지우기", key="q_clear", on_click=_clear, width="stretch", disabled=not searching,
                     help="검색을 지우고 첫 화면으로")

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
    if (ai_clicked and query.strip()) or pending:
        with st.spinner("AI가 조건에 맞는 선수를 고르는 중... (메타데이터 색인 검색 → LLM 선별)"):
            _run_ai_search(ctx, pending or query.strip())
    elif ai_clicked:
        st.caption("찾고 싶은 조건을 먼저 적어 주세요.")

    found = st.session_state.explore_results
    if found and found.get("mode") == "finder":
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

    # 이름 · 시즌 검색 — 입력하는 즉시 (네트워크 없이). 보이는 카드는 백그라운드로 미리 분석해 둔다.
    results = search_players(ctx, query, season) if (query.strip() or season) else []
    ctx.prefetcher.schedule([p["spid"] for p in results if p.get("lite")])
    if results:   # 이름이 맞는 카드가 있을 때만 '이름 검색 결과' — 조건을 적었을 때 '0장'을 보이지 않는다
        title = f"'{query.strip()}'" if query.strip() else ""
        # 갈래를 합친 시즌(25IM - S&P · 25IM - FB)은 묶음 이름 하나로
        where = (" · ".join(dict.fromkeys(season_family(ctx.catalog.seasons[s]["className"])[0] for s in season))
                 if season else "전체 시즌")
        st.markdown(f'<div class="fc-section">🔍 {title} 이름 검색 결과<small>{where} · {len(results)}장'
                    f'{" (더 있으면 이름을 더 정확히)" if len(results) >= 24 else ""}</small></div>', unsafe_allow_html=True)
        tile_grid(ctx, results, key="res")
    elif query.strip() and not found:
        st.info(f"'{query.strip()}' 이(가) 이름에 들어간 카드는 없습니다. 조건(예: 일본 출신 미드필더)이라면 **🤖 AI로 찾기**를 누르세요 — "
                "국적 · 리그 · 클럽 · 포지션 · 특성으로 전체 카드에서 찾습니다.")
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
