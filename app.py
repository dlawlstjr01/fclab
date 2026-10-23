"""FC LAB — FC 온라인 선수 검색 · 분석 · 비교 (RAG 기반 도메인 지식 서비스) · UI 영역 진입점.

    streamlit run app.py

상단 메뉴로 페이지를 고른다: ← 뒤로 · 로고(홈) · 🏠 홈 · 👤 선수 찾기 · 🏆 파워 랭킹 · ⚖️ 비교 · 🧠 스쿼드 · 전술 · ⚒️ 강화 시뮬 ·
⭐ 즐겨찾기 · 🎮 내 기록
화면만 맡는다. 수집 · 색인 · 검색 · 생성은 다른 모듈이 한다 (NF2005).
오류가 나도 화면에는 안내 문구만 보이고, 상세(트레이스백 · 키 · 프롬프트 전문)는 터미널 로그에만 남긴다 (NF2004).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="FC LAB — FC 온라인 선수 분석", page_icon="⚽", layout="wide",
                   initial_sidebar_state="collapsed")
st.set_option("client.showErrorDetails", "none")

import runtime_guard  # noqa: E402

runtime_guard.quiet_connection_reset()   # 브라우저 연결이 갑자기 끊길 때의 WinError 10054 트레이스백 (무시해도 되는 것)
changed = runtime_guard.reload_changed()   # 켜 둔 채 코드가 바뀌었으면 새로 고쳐도 새 코드로 (아래 import 가 다시 읽는다)

import ui_parts  # noqa: E402
from views import common, compare, favorites, grow, home, me, player, players, ranking, tactics  # noqa: E402

runtime_guard.remember()
PAGES = {"home": home.render, "players": players.render, "player": player.render, "ranking": ranking.render,
         "compare": compare.render, "tactics": tactics.render, "grow": grow.render, "favorites": favorites.render,
         "me": me.render}

if changed:
    st.toast("코드가 바뀌어 새로 읽었습니다." + (" 데이터 쪽(서비스) 코드도 바뀌었으니, 이상하면 앱을 한 번 다시 켜 주세요."
                                             if runtime_guard.needs_restart(changed) else ""), icon="🔄")
ctx = common.boot()
st.markdown(ui_parts.css(ctx.theme), unsafe_allow_html=True)
common.top_bar()
PAGES.get(st.session_state.page, home.render)(ctx)
common.tile_watch(ctx)

st.divider()
st.caption("Data based on NEXON Open API · 시세 · 능력치 · 특성: FC 온라인 데이터센터 · "
           "선수 문서: 한국어 위키백과 (CC BY-SA 4.0) · AI로 찾기 명단 근거: 나무위키 (CC BY-NC-SA 2.0 KR) · "
           "넥슨 · EA 와 관계없는 학습용 프로젝트입니다.")
