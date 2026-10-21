"""🏆 파워 랭킹 — 공식경기 랭커가 쓴 카드의 경기당 기록으로 포지션별 순위. (bstpg 의 파워 랭킹을 넥슨 데이터로)

랭커 스탯은 '그 카드를 그 포지션에 쓴 최근 최대 20경기의 평균'이다. 사용률(몇 명이 쓰나)은 Open API 에 없다.
"""

from __future__ import annotations

from html import escape

import streamlit as st

import ui_parts
from data_prep.players import POSITION_GROUPS, SORT_LABELS, SORT_STATS, STATS
from services.insights import leaderboard
from views.common import Ctx, open_player, tile_grid, with_saves
from views.players import SAVE_NOTE


def _keeper_stats(r: dict, key: str) -> str:
    s = r["player"].get("gk_saves")
    save = (f"선방률 {r['save_rate']:.0f}% (상대 유효슈팅 {s['on_target']} · 실점 {s['goals']} · {s['games']}경기)"
            if r["save_rate"] is not None else "선방 기록 부족")
    parts = (("save_rate", save), ("pass_rate", f"패스 성공률 {r['pass_rate']:.0f}%"))
    return " · ".join(f"<b>{escape(text)}</b>" if k == key else escape(text) for k, text in parts)


def render(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">🏆 파워 랭킹<small>공식경기 랭커 · 카드별 최근 최대 20경기 평균 · '
                '분석해 둔 카드 기준 (선수 페이지를 연 카드도 더해진다)</small></div>', unsafe_allow_html=True)
    g_col, k_col, s_col, m_col = st.container(key="wrap_rkfilters").columns([3.4, 1.2, 1.6, 1.3])
    def first_page() -> None:
        st.session_state.rank_page = 0

    st.session_state.setdefault("rank_group", "전체")   # 기본값은 세션으로 — 뒤로 돌아올 때 되살린 값과 겹치지 않게
    group = g_col.pills("포지션", ["전체"] + list(POSITION_GROUPS), key="rank_group", on_change=first_page)
    group = None if group in (None, "전체") else group
    kind = k_col.selectbox("구분", ["전체", "현역", "레전드"], key="rank_kind", on_change=first_page)
    names = {SORT_LABELS[k]: k for k in SORT_STATS.get(group, SORT_STATS[None])}   # 포지션에 맞는 정렬만
    if st.session_state.get("rank_sort") not in names:
        st.session_state.rank_sort = next(iter(names))
    sort_name = s_col.selectbox("정렬 기준", list(names), key="rank_sort", on_change=first_page)
    st.session_state.setdefault("rank_min", 10)
    min_matches = m_col.number_input("최소 경기 수 (1~20)", min_value=1, max_value=20, key="rank_min", on_change=first_page,
                                     help="랭커 기록은 카드마다 최근 최대 20경기라 20까지 고를 수 있습니다. "
                                          "경기 수가 적은 카드는 평균이 흔들립니다.")
    if group == "골키퍼":
        st.caption(SAVE_NOTE)

    key = names[sort_name]
    rows = leaderboard(with_saves(ctx, ctx.players), group, kind or "전체", key, min_matches)
    if not rows:
        st.info("조건에 맞는 카드가 없습니다. 최소 경기 수를 낮춰 보세요.")
        return

    st.markdown(f'<div class="fc-section">🥇 TOP 3 — {sort_name}</div>', unsafe_allow_html=True)

    def shown(value: float | None) -> str:
        if value is None:   # 선방 표본이 없는 골키퍼
            return "기록 부족"
        if key.endswith("_rate"):
            return f"{value:.0f}%"
        return f"{value:.0f}경기" if key == "match_count" else f"{value:.2f}"

    tile_grid(ctx, [r["player"] for r in rows[:3]], key="podium", cols=3, ranked=True,
              kpi=lambda p: f"{sort_name} <b>{shown(next(r[key] for r in rows if r['player'] is p))}</b>")

    st.markdown(f'<div class="fc-section">📋 전체 순위<small>{len(rows)}장 · 상세보기를 누르면 선수 페이지</small></div>',
                unsafe_allow_html=True)
    per_page = 20
    pages = (len(rows) + per_page - 1) // per_page
    page = min(st.session_state.get("rank_page", 0), pages - 1)
    for rank, r in enumerate(rows[page * per_page : (page + 1) * per_page], start=page * per_page + 1):
        p = r["player"]
        if p.get("position_group") == "골키퍼":   # 골 · 도움 · 태클 대신 선방 · 패스
            stats = _keeper_stats(r, key)
        else:
            stats = " · ".join(
                (f"<b>{name} {r[k]:.0f}%</b>" if u else f"<b>{name} {r[k]:.2f}</b>") if k == key
                else (f"{name} {r[k]:.0f}%" if u else f"{name} {r[k]:.2f}")
                for k, name, u in STATS
            )
        line, btn = st.columns([6, 1], vertical_alignment="center")
        line.markdown(
            f'<div class="fc-line"><div class="rk">{rank}</div>'
            f'<img src="{escape(ui_parts.image(p.get("face")) or ui_parts.image(p.get("image")))}" alt="">'
            f'<div><div class="nm">{escape(p["name"])} <span class="sb">{escape(p["season_short"])} · '
            f'{escape(", ".join(p["positions"]))} · {p["ranker"]["match_count"]}경기</span></div>'
            f'<div class="st">{stats}</div></div></div>', unsafe_allow_html=True)
        btn.button("상세보기", key=f"rank_open_{p['player_id']}", on_click=open_player, args=(p["player_id"],),
                   width="stretch")
    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("← 이전", key="rank_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update(rank_page=page - 1))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages}</div>", unsafe_allow_html=True)
        next_col.button("다음 →", key="rank_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update(rank_page=page + 1))
