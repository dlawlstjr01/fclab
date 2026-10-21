"""FC LAB — FC 온라인 선수 검색 · 분석 · 비교 (RAG 기반 도메인 지식 서비스) · UI 영역 진입점.

    streamlit run app.py

상단 메뉴로 페이지를 고른다: ← 뒤로 · 로고(홈) · 🏠 홈 · 👤 선수 찾기 · 🏆 파워 랭킹 · ⚖️ 비교 · 🧠 전술 추천 · ⭐ 즐겨찾기 · 🎮 내 기록
화면만 맡는다. 수집 · 색인 · 검색 · 생성은 다른 모듈이 한다 (NF2005).
오류가 나도 화면에는 안내 문구만 보이고, 상세(트레이스백 · 키 · 프롬프트 전문)는 터미널 로그에만 남긴다 (NF2004).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="FC LAB — FC 온라인 선수 분석", page_icon="⚽", layout="wide",
                   initial_sidebar_state="collapsed")
st.set_option("client.showErrorDetails", "none")

import ui_parts  # noqa: E402
from views import common, compare, favorites, home, me, player, players, ranking, tactics  # noqa: E402

PAGES = {"home": home.render, "players": players.render, "player": player.render, "ranking": ranking.render,
         "compare": compare.render, "tactics": tactics.render, "favorites": favorites.render, "me": me.render}

ctx = common.boot()
st.markdown(ui_parts.css(ctx.theme), unsafe_allow_html=True)
common.top_bar()
PAGES.get(st.session_state.page, home.render)(ctx)

st.divider()
st.caption("Data based on NEXON Open API · 선수 문서: 한국어 위키백과 (CC BY-SA 4.0) · "
           "넥슨 · EA 와 관계없는 학습용 프로젝트입니다. 시세 · OVR 은 Open API 에서 제공하지 않아 보여 주지 않습니다.")
