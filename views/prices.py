"""💰 강화 단계별 시세 · 사기/팔기 판단 · 시세 비교 — 선수 상세 · 비교 화면이 함께 쓴다.

시세는 FC 온라인 홈페이지 데이터센터에서 백그라운드로 받는다 (services/market.py). 화면은 SQLite 에 있는 것을
바로 그리고, 받는 중이면 2초마다 이 부분만 다시 그린다 (st.fragment). 다 받으면 페이지를 한 번 새로 그려 멈춘다.
"""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from data_prep.market import MAX_GRADE, bp, change, signal
from views.common import Ctx

NOTE = ("시세 출처: FC 온라인 홈페이지 데이터센터 (넥슨 Open API 가 아님 — 약관 확인 필요, 공개 서비스로 배포할 때는 "
        "settings.toml 의 [market] enabled = false). 판단은 최근 가격 추이만 본 규칙 기반 참고용입니다 — 이벤트 · 새 시즌 출시는 반영하지 못합니다.")
BOX = {"buy": st.success, "sell": st.warning, "wait": st.info, "hold": st.info, "neutral": st.info}


def _rows(table: dict[int, dict]) -> dict[int, dict]:
    """시세가 있는 단계만 — 단계마다 30일 범위 · 판단을 붙인다."""
    out = {}
    for grade in range(1, MAX_GRADE + 1):
        row = table.get(grade)
        if row and row["current"] is not None:
            out[grade] = {**row, "change": change(row["history"]), "signal": signal(row["history"], row["current"])}
    return out


def _progress(ctx: Ctx, spids: list[int], table_sizes: int) -> bool:
    waiting = sum(ctx.market.pending(s) for s in spids)
    if waiting:
        total = MAX_GRADE * len(spids)
        st.progress(min(1.0, table_sizes / total),
                    text=f"강화 단계별 시세를 받는 중 — 남은 요청 {waiting}건 (요청 간격을 지켜 카드 1장에 약 16초)")
    return bool(waiting)


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
        st.caption("시세 기능이 꺼져 있습니다 (settings.toml 의 [market] enabled).")
        return
    ctx.market.want([player["spid"]])
    if ctx.market.pending(player["spid"]):
        _price_live(ctx, player, mine)
    else:
        _price_body(ctx, player, mine)


def _price_view(ctx: Ctx, player: dict, mine: list[dict] | None) -> None:
    spid = player["spid"]
    table = ctx.market.table(spid)
    loading = _progress(ctx, [spid], len(table))
    if not loading and st.session_state.get("_price_live"):
        st.session_state._price_live = False
        st.rerun()   # 다 받았다 — 2초마다 다시 그리기를 멈추려고 페이지를 한 번 새로 그린다
    if ctx.market.blocked:
        st.warning("데이터센터 robots.txt 를 확인하지 못해 시세를 받지 않았습니다.")
    rows = _rows(table)
    if not rows:
        if not loading:
            st.caption(ctx.market.errors.get(spid) or "시세 기록이 없는 카드입니다 (이적시장에 매물이 없거나 거래할 수 없는 카드).")
        return

    mine_by = {r["grade"]: r for r in mine or []}
    data = {
        "강화": [f"+{g}" for g in rows],
        "현재가": [bp(r["current"]) for r in rows.values()],
        "30일 최저": [bp(r["change"]["low"]) if r["change"] else "-" for r in rows.values()],
        "30일 최고": [bp(r["change"]["high"]) if r["change"] else "-" for r in rows.values()],
        "30일 등락": [f"{r['change']['pct']:+.1f}%" if r["change"] else "-" for r in rows.values()],
        "판단": [r["signal"]["label"] for r in rows.values()],
    }
    if mine_by:   # 시세 비교 — 내가 산 값 · 판 값과 지금 시세
        data["내 최근 거래"] = [f"{mine_by[g]['kind']} {bp(mine_by[g]['value'])}" if g in mine_by else "-" for g in rows]
        data["지금 시세와"] = [f"{100 * (r['current'] / mine_by[g]['value'] - 1):+.1f}%" if g in mine_by and mine_by[g]["value"] else "-"
                          for g, r in rows.items()]
    st.dataframe(data, hide_index=True, width="stretch")

    grades = list(rows)
    default = next((g for g in grades if g in mine_by), grades[0])
    grade = st.selectbox("자세히 볼 강화 단계", grades, index=grades.index(default), format_func=lambda g: f"+{g}",
                         key=f"price_grade_{spid}")
    row = rows[grade]
    sig = row["signal"]
    chart_col, sig_col = st.columns([1.5, 1], gap="medium")
    with chart_col:
        if row["history"]:
            frame = pd.DataFrame(row["history"], columns=["날짜", "BP"]).set_index("날짜")
            st.line_chart(frame, y="BP", height=240)
            st.caption(f"+{grade} 일별 시세 {len(row['history'])}일 · 받은 시각 {row['fetched_at'][:16].replace('T', ' ')}")
        else:
            st.caption("일별 시세 기록이 없습니다.")
    with sig_col:
        text = f"**+{grade} 판단: {sig['label']}**\n\n" + "\n".join(f"- {escape(r)}" for r in sig["reasons"])
        if grade in mine_by and mine_by[grade]["value"]:
            m = mine_by[grade]
            diff = 100 * (row["current"] / m["value"] - 1)
            text += (f"\n- 내 최근 {m['kind']}가 {bp(m['value'])} → 지금 시세가 {abs(diff):.1f}% "
                     f"{'높다' if diff >= 0 else '낮다'}" + (" (수수료 제외)" if m["kind"] == "구매" else ""))
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
        st.caption("시세 기능이 꺼져 있습니다 (settings.toml 의 [market] enabled).")
        return
    spids = [pa["spid"], pb["spid"]]
    ctx.market.want(spids)
    if any(ctx.market.pending(s) for s in spids):
        _compare_live(ctx, pa, pb)
    else:
        _compare_body(ctx, pa, pb)


def _compare_view(ctx: Ctx, pa: dict, pb: dict) -> None:
    ta, tb = ctx.market.table(pa["spid"]), ctx.market.table(pb["spid"])
    loading = _progress(ctx, [pa["spid"], pb["spid"]], len(ta) + len(tb))
    ra, rb = _rows(ta), _rows(tb)
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

    st.dataframe({
        "강화": [f"+{g}" for g in grades],
        na: [bp(ra[g]["current"]) if g in ra else "-" for g in grades],
        nb: [bp(rb[g]["current"]) if g in rb else "-" for g in grades],
        "더 비싼 쪽": [ratio(g) for g in grades],
        "A 판단": [ra[g]["signal"]["label"] if g in ra else "-" for g in grades],
        "B 판단": [rb[g]["signal"]["label"] if g in rb else "-" for g in grades],
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
                         f"({ra[ga]['signal']['label']} / {rb[gb]['signal']['label']})")
    st.caption(NOTE)


@st.fragment(run_every=2)
def _compare_live(ctx: Ctx, pa: dict, pb: dict) -> None:
    st.session_state._compare_live = True
    _compare_view(ctx, pa, pb)


@st.fragment
def _compare_body(ctx: Ctx, pa: dict, pb: dict) -> None:
    _compare_view(ctx, pa, pb)
