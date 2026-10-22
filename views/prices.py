"""💰 강화 단계별 시세 · 사기/팔기 판단 · 시세 비교 — 선수 상세 · 비교 화면이 함께 쓴다.

시세는 FC 온라인 홈페이지 데이터센터에서 백그라운드로 받는다 (services/market.py). 화면은 SQLite 에 있는 것을
바로 그린다 — 현재가는 선수 검색 1번(1~2초)이면 강화 1~13 이 한꺼번에 오고, 사기/팔기 판단에 쓰는 일별 시세는 뒤이어 채운다.
받는 중이면 2초마다 이 부분만 다시 그리고 (st.fragment), 다 받으면 페이지를 한 번 새로 그려 멈춘다.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from data_prep.market import MAX_GRADE, bp, change, signal
from views.widgets import grade_buttons
from views.common import Ctx

NOTE = ("시세 출처: FC 온라인 홈페이지 데이터센터. 사기/팔기 판단은 최근 가격 흐름만 본 참고용이며, "
        "이벤트 · 새 시즌 출시는 반영하지 않습니다.")
BOX = {"buy": st.success, "sell": st.warning, "wait": st.info, "hold": st.info, "neutral": st.info}


def _ago(at: str) -> str:
    minutes = int((datetime.now() - datetime.fromisoformat(at)).total_seconds() // 60)
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    return f"{minutes // 60}시간 전" if minutes < 60 * 48 else f"{minutes // (60 * 24)}일 전"


def _rows(table: dict[int, dict]) -> dict[int, dict]:
    """시세가 있는 단계만 — 단계마다 30일 범위 · 판단을 붙인다. 일별 시세를 아직 못 받은 단계는 판단이 None."""
    out = {}
    for grade in range(1, MAX_GRADE + 1):
        row = table.get(grade)
        if row and row["current"] is not None:
            history = row["history"]
            out[grade] = {**row, "change": change(history),
                          "signal": signal(history, row["current"]) if history else None}
    return out


def _moved(row: dict) -> str:
    """바로 전에 기록된 시세와 비교 — '▲ 2.1% (3시간 전 1억 200만)'."""
    prev = row.get("prev")
    if not prev or not prev["value"]:
        return "-"
    pct = 100 * (row["current"] / prev["value"] - 1)
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "=")
    return f"{arrow} {abs(pct):.1f}% ({_ago(prev['at'])} {bp(prev['value'])})"


def _progress(ctx: Ctx, spids: list[int], rows: dict[int, dict]) -> bool:
    """받는 중이면 안내하고 True. 현재가가 아직 하나도 없을 때만 막대를 보인다 (현재가는 1~2초면 온다)."""
    waiting = sum(ctx.market.pending(s) for s in spids)
    if not waiting:
        return False
    if not rows:
        st.progress(0.3 if any(ctx.market.loading_current(s) for s in spids) else 0.6,
                    text="강화 단계별 현재가를 받는 중 — 잠시만요")
    else:
        st.caption(f"⏳ 사기/팔기 판단에 쓰는 일별 시세를 받는 중 — 받는 대로 채웁니다 (남은 요청 {waiting}건)")
    return True


def _history_chart(history: list[list], theme: str) -> alt.Chart:
    """일별 시세 선 그래프 — 날짜 축(가로 라벨 몇 개만) · 금액은 억/만 · 마우스를 올린 날의 값도 '15억 7,545만'처럼."""
    frame = pd.DataFrame(history, columns=["날짜", "BP"])
    frame["날짜"] = pd.to_datetime(frame["날짜"])
    frame["시세"] = frame["BP"].map(bp)
    span = (frame["날짜"].iloc[-1] - frame["날짜"].iloc[0]).days
    color = "#3987e5" if theme == "dark" else "#2a78d6"
    x = alt.X("날짜:T", title=None, axis=alt.Axis(format="%y.%m" if span > 180 else "%m.%d", labelAngle=0,
                                                 tickCount=6, labelOverlap="greedy", grid=False))
    y = alt.Y("BP:Q", title=None, scale=alt.Scale(zero=False),
              axis=alt.Axis(tickCount=5, labelExpr="datum.value >= 1e12 ? format(datum.value / 1e12, '.2~f') + '조'"
                            " : datum.value >= 1e8 ? format(datum.value / 1e8, '.2~f') + '억'"
                            " : datum.value >= 1e4 ? format(datum.value / 1e4, ',.0f') + '만' : format(datum.value, ',')"))
    hover = alt.selection_point(fields=["날짜"], nearest=True, on="pointerover", empty=False)
    base = alt.Chart(frame).encode(x=x, y=y)
    line = base.mark_line(strokeWidth=2, color=color)
    points = base.mark_point(size=60, filled=True, color=color).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), alt.Tooltip("시세:N")]).add_params(hover)
    rule = base.mark_rule(color="#888888", strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(0.6), alt.value(0)))
    return (line + rule + points).properties(height=240)


def _filled_note(ctx: Ctx, spid: int) -> None:
    days = {g: n for (s, g), n in ctx.market.filled.items() if s == spid}
    if days:
        st.caption("⏪ 앱이 꺼져 있던 동안 비어 있던 시세를 채웠습니다 — "
                   + ", ".join(f"+{g} {n}일" for g, n in sorted(days.items())))


# ── 선수 상세 ───────────────────────────────────────────────────

def price_section(ctx: Ctx, player: dict, mine: list[dict] | None) -> None:
    """mine = 연결한 계정의 이 카드 거래 (강화 단계별 최근 거래가). 없으면 None."""
    st.markdown('<div class="fc-section">💰 강화 단계별 시세<small>현재가 · 30일 범위 · 사기/팔기 판단 · 내 거래가와 비교</small></div>',
                unsafe_allow_html=True)
    if not ctx.market.enabled:
        st.caption("시세 기능이 꺼져 있습니다.")
        return
    ctx.market.want([player["spid"]])
    if ctx.market.pending(player["spid"]):
        _price_live(ctx, player, mine)
    else:
        _price_body(ctx, player, mine)


def _price_view(ctx: Ctx, player: dict, mine: list[dict] | None) -> None:
    spid = player["spid"]
    rows = _rows(ctx.market.table(spid))
    loading = _progress(ctx, [spid], rows)
    if not loading and st.session_state.get("_price_live"):
        st.session_state._price_live = False
        st.rerun()   # 다 받았다 — 2초마다 다시 그리기를 멈추려고 페이지를 한 번 새로 그린다
    if ctx.market.blocked:
        st.warning("지금은 데이터센터에서 시세를 받을 수 없습니다. 잠시 뒤 다시 시도하세요.")
    if not rows:
        if not loading:
            st.caption(ctx.market.errors.get(spid) or "시세 기록이 없는 카드입니다 (이적시장에 매물이 없거나 거래할 수 없는 카드).")
        return

    newest = max(r["fetched_at"] for r in rows.values())
    st.caption(f"현재가 {_ago(newest)} 기준 · 시세가 바뀌면 바뀐 값을 시각과 함께 기록해 두고, 다음에 열 때 바로 보여 줍니다")
    mine_by = {r["grade"]: r for r in mine or []}
    data = {
        "강화": [f"+{g}" for g in rows],
        "현재가": [bp(r["current"]) for r in rows.values()],
        "직전 기록 대비": [_moved(r) for r in rows.values()],
        "30일 최저": [bp(r["change"]["low"]) if r["change"] else "-" for r in rows.values()],
        "30일 최고": [bp(r["change"]["high"]) if r["change"] else "-" for r in rows.values()],
        "30일 등락": [f"{r['change']['pct']:+.1f}%" if r["change"] else "-" for r in rows.values()],
        "판단": [r["signal"]["label"] if r["signal"] else ("분석 중…" if loading else "-") for r in rows.values()],
    }
    if mine_by:   # 시세 비교 — 내가 산 값 · 판 값과 지금 시세
        data["내 최근 거래"] = [f"{mine_by[g]['kind']} {bp(mine_by[g]['value'])}" if g in mine_by else "-" for g in rows]
        data["지금 시세와"] = [f"{100 * (r['current'] / mine_by[g]['value'] - 1):+.1f}%" if g in mine_by and mine_by[g]["value"] else "-"
                          for g, r in rows.items()]
    st.dataframe(data, hide_index=True, width="stretch")

    grades = list(rows)
    default = next((g for g in grades if g in mine_by), grades[0])
    st.session_state.setdefault(f"price_grade_{spid}", default)
    grade = grade_buttons("자세히 볼 강화 단계", f"price_grade_{spid}", options=grades)
    row = rows[grade]
    sig = row["signal"]
    if not row["history_fresh"] and loading:   # 고른 단계의 일별 시세를 먼저
        ctx.market.want_history(spid, grade)
    chart_col, sig_col = st.columns([1.5, 1], gap="medium")
    with chart_col:
        if row["history"]:
            st.altair_chart(_history_chart(row["history"], ctx.theme), width="stretch")
            st.caption(f"+{grade} 일별 시세 {len(row['history'])}일 · 현재가 {_ago(row['fetched_at'])} 기준")
        else:
            st.caption("일별 시세를 받는 중입니다…" if loading else "일별 시세 기록이 없습니다.")
    with sig_col:
        if not sig:
            st.caption(f"+{grade} 판단은 일별 시세를 받으면 보입니다.")
            sig = {"label": "", "reasons": [], "action": "unknown"}
        text = f"**+{grade} 판단: {sig['label']}**\n\n" + "\n".join(f"- {escape(r)}" for r in sig["reasons"])
        if grade in mine_by and mine_by[grade]["value"]:
            m = mine_by[grade]
            diff = 100 * (row["current"] / m["value"] - 1)
            text += (f"\n- 내 최근 {m['kind']}가 {bp(m['value'])} → 지금 시세가 {abs(diff):.1f}% "
                     f"{'높다' if diff >= 0 else '낮다'}" + (" (수수료 제외)" if m["kind"] == "구매" else ""))
        if sig["label"]:
            BOX.get(sig["action"], st.caption)(text)
    _filled_note(ctx, spid)
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    c1.button("🔄 지금 시세 다시 받기", key=f"price_force_{spid}", width="stretch",
              on_click=lambda: ctx.market.want([spid], force=True))
    c2.caption(NOTE)


@st.fragment(run_every=2)
def _price_live(ctx: Ctx, player: dict, mine: list[dict] | None) -> None:
    st.session_state._price_live = True
    _price_view(ctx, player, mine)


@st.fragment
def _price_body(ctx: Ctx, player: dict, mine: list[dict] | None) -> None:
    _price_view(ctx, player, mine)


# ── 비교 화면 ───────────────────────────────────────────────────

def compare_prices(ctx: Ctx, pa: dict, pb: dict) -> None:
    st.markdown('<div class="fc-section">💰 강화 단계별 시세 비교<small>같은 강화끼리 · 다른 강화끼리 맞춰 보기 · 사기/팔기 판단</small></div>',
                unsafe_allow_html=True)
    if not ctx.market.enabled:
        st.caption("시세 기능이 꺼져 있습니다.")
        return
    spids = [pa["spid"], pb["spid"]]
    ctx.market.want(spids)
    if any(ctx.market.pending(s) for s in spids):
        _compare_live(ctx, pa, pb)
    else:
        _compare_body(ctx, pa, pb)


def _compare_view(ctx: Ctx, pa: dict, pb: dict) -> None:
    ra, rb = _rows(ctx.market.table(pa["spid"])), _rows(ctx.market.table(pb["spid"]))
    loading = _progress(ctx, [pa["spid"], pb["spid"]], ra or rb)
    if not loading and st.session_state.get("_compare_live"):
        st.session_state._compare_live = False
        st.rerun()
    if not ra and not rb:
        if not loading:
            st.caption("두 카드 모두 시세 기록이 없습니다.")
        return
    na, nb = f"A {pa['name']} {pa['season_short']}", f"B {pb['name']} {pb['season_short']}"
    grades = [g for g in range(1, MAX_GRADE + 1) if g in ra or g in rb]

    def ratio(g: int) -> str:
        if g in ra and g in rb and rb[g]["current"]:
            r = ra[g]["current"] / rb[g]["current"]
            return f"A가 {r:.2f}배" if r >= 1 else f"B가 {1 / r:.2f}배"
        return "-"

    def label(rows: dict[int, dict], g: int) -> str:
        if g not in rows:
            return "-"
        return rows[g]["signal"]["label"] if rows[g]["signal"] else ("분석 중…" if loading else "-")

    st.dataframe({
        "강화": [f"+{g}" for g in grades],
        na: [bp(ra[g]["current"]) if g in ra else "-" for g in grades],
        nb: [bp(rb[g]["current"]) if g in rb else "-" for g in grades],
        "더 비싼 쪽": [ratio(g) for g in grades],
        "A 판단": [label(ra, g) for g in grades],
        "B 판단": [label(rb, g) for g in grades],
    }, hide_index=True, width="stretch")

    # 다른 강화끼리 — 예) A +5 와 B +8 중 어느 쪽이 싼가
    a_col, b_col, out_col = st.columns([1, 1, 2.2], vertical_alignment="bottom")
    ga = a_col.selectbox("A 강화", list(ra) or [1], format_func=lambda g: f"+{g}", key="pc_a")
    gb = b_col.selectbox("B 강화", list(rb) or [1], format_func=lambda g: f"+{g}", key="pc_b")
    if ga in ra and gb in rb:
        a_val, b_val = ra[ga]["current"], rb[gb]["current"]
        cheaper = "A" if a_val < b_val else "B"
        gap = abs(a_val - b_val)
        out_col.markdown(f"**A +{ga} {bp(a_val)}** vs **B +{gb} {bp(b_val)}** → **{cheaper}가 {bp(gap)} 싸다** "
                         f"({label(ra, ga)} / {label(rb, gb)})")
    st.caption(NOTE)


@st.fragment(run_every=2)
def _compare_live(ctx: Ctx, pa: dict, pb: dict) -> None:
    st.session_state._compare_live = True
    _compare_view(ctx, pa, pb)


@st.fragment
def _compare_body(ctx: Ctx, pa: dict, pb: dict) -> None:
    _compare_view(ctx, pa, pb)
