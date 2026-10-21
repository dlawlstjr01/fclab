"""⚖️ 비교 — 두 카드의 랭커 기록을 나란히 + AI 커리어 비교(근거 포함). (F2010)

카드 고르기는 이름 → 선수 → 시즌 순서 (같은 선수의 시즌만 골라 볼 수 있어 빠르다).
"""

from __future__ import annotations

import streamlit as st

import ui_parts
from data_prep.players import STATS, player_label, stat_values
from views.common import Ctx, back_button, enrich_many, friendly_error, get_player, log, render_sources, run_with_status
from views.prices import compare_prices
from views.widgets import card_picker


def _swap() -> None:
    a, b = st.session_state.cmp_a, st.session_state.cmp_b
    st.session_state.cmp_a, st.session_state.cmp_b = b, a
    for side in ("cmp_a", "cmp_b"):   # 선택기도 새로 채우도록
        for suffix in ("_name", "_who", "_card"):
            st.session_state.pop(f"{side}_pick{suffix}", None)


def render(ctx: Ctx) -> None:
    back_col, _ = st.columns([1, 9])
    back_button("compare", back_col)
    st.markdown('<div class="fc-section">⚖️ 선수 비교<small>이름을 입력 → 선수 카드를 누르고 → 시즌 · 선수 페이지의 "⚖️ 비교하기"에서도 됩니다</small></div>',
                unsafe_allow_html=True)
    # 다른 페이지에서 담아 온 카드가 바뀌었으면 선택기를 그 카드로 다시 채운다
    for side in ("cmp_a", "cmp_b"):
        if st.session_state.get(f"{side}_seen") != st.session_state.get(side):
            for suffix in ("_name", "_who", "_card"):
                st.session_state.pop(f"{side}_pick{suffix}", None)
            st.session_state[f"{side}_seen"] = st.session_state.get(side)

    with st.container(border=True):
        st.markdown("**🔵 선수 A**")
        a = card_picker(ctx, key="cmp_a_pick", default_id=st.session_state.cmp_a)
    st.button("⇅ A · B 바꾸기", key="cmp_swap", on_click=_swap)
    with st.container(border=True):
        st.markdown("**🟠 선수 B**")
        b = card_picker(ctx, key="cmp_b_pick", default_id=st.session_state.cmp_b)
    st.session_state.cmp_a = st.session_state.cmp_a_seen = a
    st.session_state.cmp_b = st.session_state.cmp_b_seen = b
    if not a or not b:
        st.info("두 선수를 고르면 카드와 랭커 기록을 나란히 보여 주고, 실제 커리어를 근거와 함께 비교합니다.")
        return
    if a == b:
        st.warning("서로 다른 카드를 골라 주세요.")
        return

    try:
        with st.spinner("카드 정보를 불러오는 중..."):
            pa, pb = enrich_many(ctx, [get_player(ctx, a), get_player(ctx, b)])
    except Exception as exc:  # noqa: BLE001
        log.exception("비교 카드 불러오기 실패")
        st.error(friendly_error(exc))
        return
    va, vb = stat_values(pa.get("ranker")), stat_values(pb.get("ranker"))
    left, mid, right = st.columns([1, 2.2, 1], gap="medium")
    left.markdown(ui_parts.player_card(pa), unsafe_allow_html=True)
    right.markdown(ui_parts.player_card(pb), unsafe_allow_html=True)
    with mid:
        if va and vb:
            wins_a = sum(1 for k, _, _ in STATS if va[k] > vb[k])
            wins_b = sum(1 for k, _, _ in STATS if vb[k] > va[k])
            st.markdown(ui_parts.chips([(pa["name"], f"{wins_a}개 우세"), (pb["name"], f"{wins_b}개 우세"),
                                        ("비교 스탯", f"{len(STATS)}개")]), unsafe_allow_html=True)
        st.markdown(ui_parts.compare_chart(pa, pb, ui_parts.stat_maxima(ctx.players), ctx.theme), unsafe_allow_html=True)
        with st.expander("표로 보기"):
            def shown(values: dict, key: str, unit: str) -> str:
                if not values:
                    return "기록 없음"
                return f"{values[key]:.0f}%" if unit else f"{values[key]:.2f}"

            st.table({
                "스탯 (랭커 경기당 · 성공률)": [name for _, name, _ in STATS],
                player_label(pa): [shown(va, k, u) for k, _, u in STATS],
                player_label(pb): [shown(vb, k, u) for k, _, u in STATS],
            })

    compare_prices(ctx, pa, pb)

    key = f"compare:{a}:{b}"
    saved = st.session_state.results.get(key)
    st.markdown('<div class="fc-section">🤖 AI 커리어 비교<small>두 선수의 위키백과 문서 + 랭커 기록만 근거로</small></div>',
                unsafe_allow_html=True)
    if st.button("다시 비교하기" if saved else "✨ AI 커리어 비교 만들기", key="make_compare", type="secondary" if saved else "primary"):
        reply = run_with_status(ctx, "compare", [pa, pb], "")
        if reply:
            st.session_state.results[key] = saved = reply
    if saved:
        st.markdown(saved["answer"])
        render_sources(saved["answer"], saved["evidence"])
