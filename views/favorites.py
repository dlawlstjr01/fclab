"""⭐ 즐겨찾기 — 자주 보는 선수 카드를 모아 한눈에. (fcalrimi 의 즐겨찾기 방식)"""

from __future__ import annotations

import streamlit as st

from views.common import Ctx, get_player, go, tile_grid


def render(ctx: Ctx) -> None:
    ids = ctx.favorites.ids()
    st.markdown(f'<div class="fc-section">⭐ 즐겨찾기<small>{len(ids)}장 · 선수 페이지의 ☆ 버튼으로 추가 · 해제</small></div>',
                unsafe_allow_html=True)
    if not ids:
        st.info("아직 즐겨찾기한 선수가 없습니다. 선수 페이지에서 **☆ 즐겨찾기**를 눌러 보세요.")
        st.button("🏠 홈에서 선수 찾기", key="fav_go_home", on_click=go, args=("home",))
        return
    players = []
    for pid in ids:
        try:
            players.append(get_player(ctx, pid))
        except KeyError:
            continue
    tile_grid(ctx, players, key="favs", cols=6)
