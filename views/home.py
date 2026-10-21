"""🏠 홈 — 검색이 첫 화면이다. 이름 · 시즌으로 바로 찾고(네트워크 0회), 말로 찾고(AI), 랭커 픽을 둘러본다."""

from __future__ import annotations

import streamlit as st

from html import escape

from data_prep.players import player_label, stat_values
from services.explore import describe, explore_players, suggest_conditions
from views.common import Ctx, back_button, friendly_error, log, search_players, tile_grid
from views.widgets import season_select, semantic_players


def _run_ai_search(ctx: Ctx, condition: str) -> None:
    try:
        found = explore_players(condition, ctx.store, ctx.model, ctx.by_id, ctx.settings)
        st.session_state.explore_results = {"condition": condition, **found}
        ctx.db.log_search(condition)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI 검색 실패")
        st.error(friendly_error(exc))


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
    chip_cols = st.container(key="wrap_chips").columns([1] * len(st.session_state.chips) + [0.32],
                                                       vertical_alignment="center")
    for col, example in zip(chip_cols, st.session_state.chips):
        col.button(f"💡 {example}", key=f"chip_{example}", on_click=_use_example, args=(example,), width="stretch",
                   type="tertiary")
    chip_cols[-1].button("🔀", key="chip_shuffle", on_click=_shuffle_chips, args=(ctx,), type="tertiary",
                         help="다른 추천 검색어", width="stretch")

    pending = st.session_state.pop("pending_ai", None)
    if (ai_clicked and query.strip()) or pending:
        with st.spinner("AI가 조건에 맞는 선수를 고르는 중... (메타데이터 색인 검색 → LLM 선별)"):
            _run_ai_search(ctx, pending or query.strip())
    elif ai_clicked:
        st.caption("찾고 싶은 조건을 먼저 적어 주세요.")

    found = st.session_state.explore_results
    if found:
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
        excluded = [e for e in found.get("excluded", []) if e["player_id"] in ctx.by_id]
        if excluded:
            with st.expander(f"AI가 제외한 후보 {len(excluded)}장 — 왜 빠졌는지"):
                st.markdown("\n".join(f"- **{escape(player_label(ctx.by_id[e['player_id']]))}** — {escape(e['reason'])}"
                                      for e in excluded))

    # 이름 · 시즌 검색 — 입력하는 즉시 (네트워크 없이). 보이는 카드는 백그라운드로 미리 분석해 둔다.
    results = search_players(ctx, query, season) if (query.strip() or season) else []
    ctx.prefetcher.schedule([p["spid"] for p in results if p.get("lite")])
    if query.strip() or season:
        title = f"'{query.strip()}'" if query.strip() else ""
        where = ctx.catalog.seasons[season]["className"] if season else "전체 시즌"
        st.markdown(f'<div class="fc-section">🔍 {title} 이름 검색 결과<small>{where} · {len(results)}장'
                    f'{" (더 있으면 이름을 더 정확히)" if len(results) >= 24 else ""}</small></div>', unsafe_allow_html=True)
        if results:
            tile_grid(ctx, results, key="res")
        elif query.strip() and not found:
            # 이름이 아니라 조건을 적은 경우 — 분석해 둔 선수 중 뜻이 가까운 선수를 먼저 보여 준다 (LLM 없이)
            try:
                similar = semantic_players(ctx, query.strip())
            except Exception as exc:  # noqa: BLE001
                log.exception("의미 검색 실패")
                similar = []
                st.caption(friendly_error(exc))
            if similar:
                st.markdown(f'<div class="fc-section">📚 "{query.strip()}"와 비슷한 선수<small>이름과 맞는 카드는 없어서, '
                            "분석해 둔 선수 중 뜻이 가까운 선수를 보여 줍니다</small></div>", unsafe_allow_html=True)
                tile_grid(ctx, similar, key="sem")
            st.info("원하는 선수가 없나요? **🤖 AI로 찾기**를 누르면 AI가 조건에 정확히 맞는 선수만 골라 줍니다.")

    if searching:
        return

    favorite_ids = [i for i in ctx.favorites.ids() if i in ctx.by_id]
    if favorite_ids:
        st.markdown('<div class="fc-section">⭐ 즐겨찾기</div>', unsafe_allow_html=True)
        tile_grid(ctx, [ctx.by_id[i] for i in favorite_ids][:6], key="fav")

    def points(p: dict) -> float:
        v = stat_values(p.get("ranker"))
        return v["goal"] + v["assist"] if v else -1

    active = [p for p in ctx.players if p.get("ranker") and p["ranker"]["match_count"] >= 10]
    st.markdown('<div class="fc-section">🏆 파워 랭킹 — 랭커 경기 공격 포인트 TOP<small>공식경기 랭커 경기당 골 + 도움</small></div>',
                unsafe_allow_html=True)
    tile_grid(ctx, sorted(active, key=points, reverse=True)[:12], key="top", ranked=True)

    legends = [p for p in active if "ICON" in p["season"]]
    st.markdown('<div class="fc-section">👑 랭커가 쓰는 레전드 — ICON · ICON TM</div>', unsafe_allow_html=True)
    tile_grid(ctx, sorted(legends, key=points, reverse=True)[:6], key="legend")

    defenders = [p for p in active if p.get("position_group") in ("센터백", "풀백", "수비형 미드필더")]
    st.markdown('<div class="fc-section">🛡️ 수비 핵심 — 태클 + 블록 TOP</div>', unsafe_allow_html=True)
    tile_grid(ctx, sorted(defenders, key=lambda p: -(p["ranker"]["tackle"] + p["ranker"]["block"]))[:6], key="def")
