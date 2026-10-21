"""👤 선수 — 전체 선수 검색 · 둘러보기. 이름 · 시즌 · 포지션 · 현역/레전드 · 정렬, 한 페이지 24장.

- 이름 · 시즌만 고르면 전체 88,359장에서 찾는다 (네트워크 없이).
- 포지션 · 랭커 기록으로 거르거나 정렬하면 랭커 기록이 있는(분석된) 카드에서 찾는다.
"""

from __future__ import annotations

import streamlit as st

from data_prep.players import POSITION_GROUPS, SAVE_MIN_SHOTS, SORT_LABELS, SORT_STATS, sort_value
from views.common import Ctx, back_button, has_save_samples, search_players, tile_grid, with_saves
from views.widgets import season_select, semantic_players

PAGE = 24
SAVE_NOTE = (f"골키퍼 선방률은 넥슨 랭커 API 에 없어, 🎮 내 기록 · 내 스쿼드가 불러온 경기(최근 30일)의 상대 유효슈팅 · 실점으로 "
             f"계산합니다. 상대 유효슈팅이 {SAVE_MIN_SHOTS}개 이상 모인 카드만 선방률이 보입니다.")


def sort_options(group: str | None) -> dict[str, str | None]:
    """포지션에 맞는 정렬만 — 예) 골키퍼: 선방률 · 랭커 경기 수 · 패스 성공률, 센터백: 태클 · 블록 · 패스."""
    return ({"추천 (최신 시즌 · 대표 스탯)": None, "이름순": "name"}
            | {SORT_LABELS[key] if key == "save_rate" else f"랭커 {SORT_LABELS[key]}": key
               for key in SORT_STATS.get(group, SORT_STATS[None])})


def _reset_page() -> None:
    st.session_state.pl_page = 0


def render(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">👤 선수 찾기<small>전체 88,359장 · 카드를 누르면 상세 페이지</small></div>',
                unsafe_allow_html=True)
    st.session_state.setdefault("pl_page", 0)
    chosen_group = st.session_state.get("pl_group")
    chosen_group = None if chosen_group in (None, "전체") else chosen_group
    sorts = sort_options(chosen_group)
    if st.session_state.get("pl_sort") not in sorts:   # 포지션을 바꾸면 그 포지션의 정렬로
        first = 2 if chosen_group else 0
        if chosen_group == "골키퍼" and not has_save_samples(ctx):   # 선방 표본이 아직 없으면 랭커 경기 수로
            first = 3
        st.session_state.pl_sort = list(sorts)[first]
    with st.container(key="wrap_plfilters"):
        back_col, n_col, s_col, k_col, o_col = st.columns([0.9, 2.2, 3.2, 1.3, 2], vertical_alignment="bottom")
        back_button("players", back_col)
        name = n_col.text_input("이름", key="pl_q", placeholder="선수 이름", on_change=_reset_page)
        with s_col:
            season = season_select(ctx, key="pl_season")
        kind = k_col.selectbox("구분", ["전체", "현역", "레전드"], key="pl_kind", on_change=_reset_page)
        sort = o_col.selectbox("정렬", list(sorts), key="pl_sort", on_change=_reset_page)
    group = st.pills("포지션", ["전체"] + list(POSITION_GROUPS), default="전체", key="pl_group", on_change=_reset_page)
    group = None if group in (None, "전체") else group
    if group == "골키퍼":
        st.caption(SAVE_NOTE)
    sort_key = sorts.get(sort)
    needs_stats = bool(group) or (sort_key not in (None, "name")) or (kind not in (None, "전체"))
    st.session_state.pl_page = st.session_state.get("pl_page", 0)

    if needs_stats or not (name.strip() or season):
        # 랭커 기록으로 거르기 · 정렬 — 분석된 카드 안에서
        pool = [p for p in ctx.players if (not name.strip() or name.strip().replace(" ", "") in p["name"].replace(" ", ""))
                and (season is None or p["season_id"] == season)
                and (group is None or p.get("position_group") == group)
                and (kind in (None, "전체") or (kind == "레전드") == ("ICON" in p["season"]))]
        pool = with_saves(ctx, pool)
        if sort_key == "name":
            pool.sort(key=lambda p: (p["name"], -p["season_id"]))
        elif sort_key:
            pool.sort(key=lambda p: -sort_value(p, sort_key))
        else:   # 추천: 최신 시즌 → 그 포지션의 대표 스탯
            pool.sort(key=lambda p: (-p["season_id"], -sort_value(p, (SORT_STATS.get(p.get("position_group")) or ["points"])[0])))
        source = "랭커 기록이 있는 분석 카드"
    else:
        pool = search_players(ctx, name, season, limit=600)
        if sort_key == "name":
            pool.sort(key=lambda p: (p["name"], -p["season_id"]))
        source = "전체 카드"

    total = len(pool)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(st.session_state.pl_page, pages - 1)
    shown = pool[page * PAGE : (page + 1) * PAGE]
    ctx.prefetcher.schedule([p["spid"] for p in shown if p.get("lite")])
    st.caption(f"{source}에서 {total:,}장{' (600장까지)' if total >= 600 else ''} · {page + 1}/{pages} 페이지")

    if shown:
        tile_grid(ctx, shown, key="pl")
    elif name.strip():
        similar = semantic_players(ctx, name.strip())
        if similar:
            st.markdown('<div class="fc-section">📚 이름이 맞는 카드는 없어서 뜻이 가까운 선수를 보여 줍니다</div>',
                        unsafe_allow_html=True)
            tile_grid(ctx, similar, key="pl_sem")
        st.info("조건으로 찾고 싶다면 🏠 홈의 **🤖 AI로 찾기**를 쓰세요.")
    else:
        st.info("조건에 맞는 카드가 없습니다. 필터를 줄여 보세요.")

    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("← 이전", key="pl_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update(pl_page=page - 1))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages}</div>", unsafe_allow_html=True)
        next_col.button("다음 →", key="pl_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update(pl_page=page + 1))
