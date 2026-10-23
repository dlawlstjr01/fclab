"""⚖️ 비교 — 두 카드의 랭커 기록을 나란히 + AI 커리어 비교(근거 포함). (F2010)

카드 고르기는 이름 → 선수 → 시즌 순서 (같은 선수의 시즌만 골라 볼 수 있어 빠르다).
"""

from __future__ import annotations

import streamlit as st

import ui_parts
from data_prep.players import STATS, player_label, stat_values
from views.common import (Ctx, back_button, dialog_open, enrich_many, friendly_error, get_player, log, render_sources,
                          run_with_status)
from views.player import bonus_controls, boosted, exact_watch
from views.prices import compare_prices
from views.widgets import card_picker


def _swap() -> None:
    a, b = st.session_state.cmp_a, st.session_state.cmp_b
    st.session_state.cmp_a, st.session_state.cmp_b = b, a
    for side in ("cmp_a", "cmp_b"):   # 선택기도 새로 채우도록
        for suffix in ("_name", "_who", "_card"):
            st.session_state.pop(f"{side}_pick{suffix}", None)


def _ability_section(ctx: Ctx, pa: dict, pb: dict) -> None:
    """📊 카드 능력치 비교 — 카드마다 강화 · 적응도 · 팀컬러(강화 · 소속 · 특성)를 골라 능력치 · OVR 을 나란히 (선수 상세와 같은 계산)."""
    st.markdown('<div class="fc-section">📊 카드 능력치 비교<small>카드마다 강화 · 적응도 · 팀컬러를 골라 비교 — 선수 상세와 같은 계산 · '
                '선택도 같이 쓴다 · FC 온라인 데이터센터</small></div>', unsafe_allow_html=True)
    spids = [pa["spid"], pb["spid"]]
    infos = ctx.cards.get(spids)
    if len(infos) < 2:
        if ctx.cards.enabled:
            ctx.cards.want(spids)
            _cards_waiting(ctx, spids)
        else:
            st.caption("데이터센터 기능이 꺼져 있어 카드 능력치를 비교할 수 없습니다 (settings.toml [market] enabled).")
        return
    _ability_panel(ctx, pa, pb, infos)


@st.fragment(run_every=2)
def _cards_waiting(ctx: Ctx, spids: list[int]) -> None:
    if len(ctx.cards.get(spids)) == len(spids) and not dialog_open():
        st.rerun()
    st.info("⏳ 데이터센터에서 두 카드의 능력치 · 포지션별 오버롤 · 팀컬러를 받는 중… (몇 초)")


@st.fragment
def _ability_panel(ctx: Ctx, pa: dict, pb: dict, infos: dict) -> None:
    """고르는 칸이 바뀌면 이 칸만 다시 그린다."""
    views = []
    for col, p, tag in zip(st.columns(2, gap="large"), (pa, pb), ("🔵 A", "🟠 B")):
        with col, st.container(border=True):
            st.markdown(f"**{tag} · {p['name']}** <small>{p.get('season_short', '')}</small>", unsafe_allow_html=True)
            pick = bonus_controls(ctx, p, infos[p["spid"]], compact=True)
            views.append({**boosted(ctx, p, infos[p["spid"]], pick), "name": f"{p['name']} {p.get('season_short', '')}",
                          "spid": p["spid"], "combo": pick["combo"], "position": infos[p["spid"]].get("position", "")})
    st.markdown(ui_parts.ability_compare(views[0], views[1]), unsafe_allow_html=True)   # OVR 은 각자 카드의 대표 포지션
    if any(not v["exact"] for v in views):
        st.caption("≈ 특정 능력치만 올리는 팀컬러가 OVR 에 미치는 정도는 데이터센터 계산을 받는 대로 정확한 값으로 바뀝니다 "
                   "(능력치 숫자는 정확합니다).")
    waiting = next((v for v in views if v["waiting"]), None)
    if waiting:   # 하나씩 — 받으면 다시 그리고, 다른 쪽이 남았으면 그것을 지켜본다
        exact_watch(ctx, waiting["spid"], waiting["combo"])


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

    # A · B 를 좌우로 나란히 (가운데에 바꾸기) — 세로로 쌓으면 두 선수를 한눈에 못 본다
    # 위쪽 맞춤 — 가운데 맞춤이면 한쪽에 카드 목록이 열릴 때 다른 쪽 입력란이 아래로 밀려 내려갔다
    # 넓은 화면은 한 줄(⇄ 칸은 52px), 900px 이하에서는 A → ⇅ → B 로 위아래 (ui_parts.css 의 .st-key-cmpsel)
    a_col, s_col, b_col = st.container(key="cmpsel").columns([1, 0.09, 1], gap="small", vertical_alignment="top")
    with a_col, st.container(border=True):
        st.markdown("**🔵 선수 A**")
        a = card_picker(ctx, key="cmp_a_pick", default_id=st.session_state.cmp_a)
    s_col.markdown('<div class="fc-swapgap"></div>', unsafe_allow_html=True)   # ⇄ 를 이름 입력란 높이에
    s_col.button("⇄", key="cmp_swap", on_click=_swap, width="stretch", help="A · B 바꾸기")
    with b_col, st.container(border=True):
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

    _ability_section(ctx, pa, pb)
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
