"""🏆 파워 랭킹 — 랭커가 쓴 카드의 기록 순위.

- 📅 데일리 (기본): FC 온라인 데이터센터 데일리 기록 — 어제 공식 경기(또는 감독 모드) 랭커 상위 1만 명이 실제로 쓴 카드의
  출전 · 공격 포인트 · 득점 · 도움 · 패스 · 드리블 · 태클 · 평점 … 기록마다 TOP 18. 시즌 · 강화 · 포지션 OVR · 급여 · 선수
  이미지와 함께 (https://fconline.nexon.com/datacenter/dailyrecord 의 '선수 기록'). 지금 쓰이는 카드가 나온다.
- 📊 랭커 분석 카드 (넥슨 Open API): 분석해 둔 카드의 '그 포지션 최근 최대 20경기 평균' — 예전 기준 (수집 시점의 카드).
"""

from __future__ import annotations

from html import escape

import streamlit as st

import ui_parts
from data_prep.datacenter import DAILY_STATS, MODES
from data_prep.players import POSITION_GROUPS, SORT_LABELS, SORT_STATS, STATS
from services.insights import leaderboard
from views.common import Ctx, daily_wait, get_player, open_player, tile_grid, with_saves
from views.players import SAVE_NOTE

RATE_STATS = ("PASS", "DRIBBLE", "AERIAL", "TACKLE", "BLOCK")   # 성공률(%) 칸
SHOWN_STATS = ("MATCHCOUNT", "ATTACKPOINT", "GOAL", "ASSIST", "PASS", "DRIBBLE", "TACKLE", "INTERCEPT", "RATING")
LINES = {"전체": None, "공격수": {"ST", "CF", "LW", "RW", "LS", "RS", "LF", "RF"},
         "미드필더": {"CAM", "CM", "CDM", "LM", "RM", "LAM", "RAM", "LCM", "RCM", "LDM", "RDM"},
         "수비수": {"CB", "LB", "RB", "LWB", "RWB", "SW", "LCB", "RCB"}, "골키퍼": {"GK"}}
MODE_LABELS = {"official": "공식 경기 1 ON 1", "manager": "감독 모드"}


def daily_value(key: str, stats: dict) -> str:
    """데일리 기록 한 칸 — 성공률은 %(시도), 경기당은 평균(합계), 출전은 경기, 평점은 그대로."""
    value = stats.get(key)
    if value is None:
        return "-"
    if key == "MATCHCOUNT":
        return f"{value:,.0f}경기"
    if key in RATE_STATS:
        tries = stats.get(key + "_try")
        return f"{value:.1f}%" + (f" ({tries:,.0f}회)" if tries else "")
    if key == "RATING":
        return f"{value:.1f}"
    total = stats.get(key + "_total")
    return f"{value:.1f}" + (f" ({total:,.0f})" if total is not None else "")


def _daily_board(ctx: Ctx) -> None:
    """데일리 기록 TOP — 기록 · 모드 · 구분(현역 · 레전드) · 줄(공격 · 미드 · 수비 · 골키퍼)을 골라 본다."""
    o_col, m_col, k_col, l_col = st.container(key="wrap_dailyfilters").columns([2, 1.6, 1.2, 3])
    names = {label: key for key, label in DAILY_STATS.items()}
    label = o_col.selectbox("기록", list(names), key="daily_order")
    mode = m_col.segmented_control("모드", list(MODE_LABELS), default="official", key="daily_mode",
                                   format_func=MODE_LABELS.get) or "official"
    kind = k_col.selectbox("구분", ["전체", "현역", "레전드"], key="daily_kind",
                           help="레전드 = 아이콘 카드가 있는 선수의 모든 시즌 카드")
    line = l_col.pills("줄", list(LINES), default="전체", key="daily_line") or "전체"
    order = names[label]
    data = ctx.daily.stats(order, mode)
    if data is None:
        if ctx.daily.pending():
            daily_wait(ctx)
        else:
            st.info("데일리 기록을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.")
        return
    rows = data["rows"]
    legends = ctx.finder.legends()
    if kind != "전체":
        rows = [r for r in rows if (r["spid"] % 1_000_000 in legends) == (kind == "레전드")]
    if LINES[line]:
        rows = [r for r in rows if r["position"] in LINES[line]]
    st.caption(f"{data['date']} · {MODE_LABELS[mode]} 랭커 상위 1만 명이 쓴 카드의 {label} TOP {len(data['rows'])} "
               f"(FC 온라인 데이터센터 데일리 기록){' · 거른 뒤 ' + str(len(rows)) + '장' if len(rows) != len(data['rows']) else ''}")
    if not rows:
        st.info("이 조건에 맞는 카드가 오늘 기록 TOP 에 없습니다.")
        return
    players = {str(r["spid"]): get_player(ctx, str(r["spid"])) for r in rows}
    by_id = {str(r["spid"]): r for r in rows}
    st.markdown(f'<div class="fc-section">🥇 TOP 3 — {escape(label)}</div>', unsafe_allow_html=True)
    podium = list({str(r["spid"]): r for r in reversed(rows[:6])}.values())[::-1][:3]   # 같은 카드는 한 번만 (높은 순위로)
    tile_grid(ctx, [players[str(r["spid"])] for r in podium], key="dpodium", cols=3, ranked=True,
              kpi=lambda p: f"{escape(label)} <b>{daily_value(order, by_id[p['player_id']]['stats'])}</b>")
    st.markdown(f'<div class="fc-section">📋 순위<small>{len(rows)}장 · 상세보기를 누르면 선수 페이지</small></div>',
                unsafe_allow_html=True)
    for rank, r in enumerate(rows, start=1):
        p = players[str(r["spid"])]
        stats = " · ".join((f"<b>{DAILY_STATS[k]} {daily_value(k, r['stats'])}</b>" if k == order
                            else f"{DAILY_STATS[k]} {daily_value(k, r['stats'])}") for k in SHOWN_STATS if k in r["stats"])
        grade_text = f" · +{r['grade']}강" if r.get("grade") else ""
        line_col, btn = st.columns([6, 1], vertical_alignment="center")
        line_col.markdown(
            f'<div class="fc-line daily"><div class="rk">{rank}</div>'
            f'<div class="cv">{ui_parts.card_visual(p, r["ovr"], r["position"], pay=r.get("pay"))}</div>'
            f'<div><div class="nm">{escape(p["name"])} <span class="sb">{escape(p["season_short"])} · {escape(r["position"])}'
            f'{grade_text}</span></div>'
            f'<div class="st">{stats}</div></div></div>', unsafe_allow_html=True)
        # 같은 카드가 강화 등급만 달리 두 번 나올 수 있다 — 키에 순위를 넣는다
        btn.button("상세보기", key=f"drank_open_{rank}_{r['spid']}", on_click=open_player, args=(str(r["spid"]),),
                   width="stretch")


def render(ctx: Ctx) -> None:
    if not ctx.daily.enabled:
        _ranker_board(ctx)
        return
    st.markdown('<div class="fc-section">🏆 파워 랭킹<small>랭커가 실제로 쓴 카드의 기록 순위 — 데일리(어제 · 데이터센터)가 기본, '
                '넥슨 Open API 로 분석해 둔 카드 순위는 두 번째 탭</small></div>', unsafe_allow_html=True)
    daily_tab, ranker_tab = st.tabs(["📅 데일리 랭킹 (데이터센터)", "📊 랭커 분석 카드 (넥슨 Open API)"])
    with daily_tab:
        _daily_board(ctx)
    with ranker_tab:
        _ranker_board(ctx)


def _keeper_stats(r: dict, key: str) -> str:
    s = r["player"].get("gk_saves")
    save = (f"선방률 {r['save_rate']:.0f}% (상대 유효슈팅 {s['on_target']} · 실점 {s['goals']} · {s['games']}경기)"
            if r["save_rate"] is not None else "선방 기록 부족")
    parts = (("save_rate", save), ("pass_rate", f"패스 성공률 {r['pass_rate']:.0f}%"))
    return " · ".join(f"<b>{escape(text)}</b>" if k == key else escape(text) for k, text in parts)


def _ranker_board(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">📊 랭커 분석 카드<small>공식경기 랭커 · 카드별 최근 최대 20경기 평균 · '
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
    rows = leaderboard(with_saves(ctx, ctx.players), group, kind or "전체", key, min_matches, ctx.finder.legends())
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
