"""⚒️ 강화 시뮬레이터 — FC 온라인 공식 확률표(data_prep/enhance.py)로 한 번씩 강화해 보고, 목표 등급까지의 확률을 계산한다.

- 부스트 게이지: 직접 정하거나(%), 소모 선수를 넣어 데이터센터 '강화 부스트 도우미'로 정확히 계산한다
  (https://fconline.nexon.com/datacenter/playergrow 와 같은 계산).
- 같은 선수 소모: 실패했을 때 복구 등급 표와 크리티컬(+2, 1~4강) 확률이 바뀐다.
- 시세: 이 카드의 강화 단계별 현재가(받아 둔 것) — 강화하는 비용과 목표 등급을 바로 사는 값을 견줘 본다.
- 목표까지: 등급을 상태로 둔 마르코프 연쇄로 정확히 — 평균 시도 횟수 · N번 안에 닿을 확률 · 50% / 90% 가 닿는 횟수.
확률 · 복구 표는 공식 공지 그대로다. '한 번 강화'는 그 확률로 뽑는 모의 실험이라 실제 게임 결과를 보장하지 않는다.
"""

from __future__ import annotations

import random
from html import escape

import pandas as pd
import streamlit as st

import ui_parts
from data_prep.enhance import CRITICAL, GRADE_OVR, MAX_GRADE, RECOVER, SUCCESS, attempt, failure_table, odds, success_rate
from data_prep.market import bp
from views.common import Ctx, card_basics, friendly_error, get_player, log


def ovr_at_grade(base: int | None, grade: int) -> int | None:
    """+1 OVR → 그 강화 등급의 OVR."""
    return base + GRADE_OVR[grade] if base else None
from views.widgets import card_picker, grade_buttons

MAX_MATERIALS = 5
GOAL_CAP = 20_000   # '목표 등급까지 강화' 한 번에 최대 시도 (높은 등급은 수만 번이 들 수 있다)
HISTORY = 30        # 화면에 남겨 두는 최근 시도


def _count(n: float | None) -> str:
    if n is None:
        return "닿지 않음"
    if n >= 10**8:
        return f"약 {n / 10**8:,.0f}억 번"
    if n >= 10**4:
        return f"약 {n / 10**4:,.0f}만 번"
    return f"{n:,.0f}번"


def _reset_run(spid: int, start: int) -> None:
    st.session_state.gr_run = {"spid": spid, "start": start, "grade": start, "history": [], "tries": 0, "ok": 0, "crit": 0,
                               "fail": 0, "goal": None, "steps": {}, "fx": None}


def _step(run: dict, boost: float, same_grade: int | None, rng: random.Random) -> str:
    """한 번 강화하고 기록한다 — 단계(+N → +N+1)마다 시도 · 성공 · 실패를 따로 센다 (성공 비율은 단계마다 봐야 뜻이 있다).
    '지금 등급 고정'(등급 보호)이면 실패해도 등급이 내려가지 않는다."""
    before = run["grade"]
    after, outcome = attempt(before, boost, same_grade, rng, protect=bool(st.session_state.get("gr_lock")))
    run["grade"] = after
    run["tries"] += 1
    kind = {"성공": "ok", "크리티컬": "crit", "실패": "fail"}[outcome]
    run[kind] += 1
    step = run["steps"].setdefault(str(before), {"tries": 0, "ok": 0, "crit": 0, "fail": 0})
    step["tries"] += 1
    step[kind] += 1
    run["history"].insert(0, {"from": before, "to": after, "outcome": outcome, "rate": success_rate(before, boost)})
    del run["history"][HISTORY:]
    return outcome


def _one_try(boost: float, same_grade: int | None) -> None:
    run = st.session_state.gr_run
    if run["grade"] >= MAX_GRADE:
        return
    run["goal"] = None
    outcome = _step(run, boost, same_grade, random.Random())
    run["fx"] = {"n": run["tries"], "outcome": outcome}   # 연출 — 성공 불꽃 · 실패 연기 (시도마다 새로)


def _to_goal(boost: float, same_grade: int | None, target: int) -> None:
    """목표 등급에 처음 닿을 때까지 계속 강화하고 멈춘다 (최대 GOAL_CAP 번). 등급 보호가 꺼져 있으면 실패로 등급이 내려가
    다시 올라오느라 다른 단계의 성공도 섞인다 — 그래서 가장 낮게 내려간 등급과 마지막 단계의 시도 수를 함께 남긴다."""
    run = st.session_state.gr_run
    rng, start, before = random.Random(), run["grade"], run["tries"]
    lowest, last_step = start, {"tries": 0, "ok": 0}
    while run["grade"] < target and run["tries"] - before < GOAL_CAP:
        grade = run["grade"]
        outcome = _step(run, boost, same_grade, rng)
        lowest = min(lowest, run["grade"])
        if grade == target - 1 or (outcome == "크리티컬" and grade == target - 2):
            last_step["tries"] += 1
            last_step["ok"] += outcome != "실패"
    reached = run["grade"] >= target
    run["goal"] = {"from": start, "target": target, "tries": run["tries"] - before, "reached": reached, "lowest": lowest,
                   "last_tries": last_step["tries"], "protect": bool(st.session_state.get("gr_lock"))}
    run["fx"] = {"n": run["tries"], "outcome": "성공" if reached else "실패"}


def _materials(ctx: Ctx, spid: int, grade: int) -> tuple[float | None, int | None]:
    """소모 선수를 넣어 데이터센터에서 부스트 % 를 계산 → (부스트 %, 소모 선수 가치 합)."""
    mats = st.session_state.setdefault("gr_mats", [])
    for i, m in enumerate(list(mats)):
        n_col, x_col = st.columns([4, 1], vertical_alignment="center")
        n_col.markdown(f"**{escape(m['name'])}** · {escape(m['season'])} +{m['grade']}")
        if x_col.button("빼기", key=f"gr_mat_x_{i}", width="stretch"):
            mats.pop(i)
            st.session_state.pop("gr_boost", None)
            st.rerun()
    if len(mats) < MAX_MATERIALS:
        with st.expander(f"➕ 소모 선수 넣기 ({len(mats)}/{MAX_MATERIALS})", expanded=not mats):
            picked = card_picker(ctx, key="gr_mat_pick", label="소모할")
            m_grade = grade_buttons("소모 선수 강화", "gr_mat_grade")
            if picked and st.button("이 선수 소모하기", key="gr_mat_add", type="primary"):
                card = get_player(ctx, picked)
                mats.append({"spid": card["spid"], "name": card["name"], "season": card["season_short"], "grade": m_grade})
                st.session_state.pop("gr_boost", None)
                st.rerun()
    if not mats:
        return None, None
    key = (spid, grade, tuple((m["spid"], m["grade"]) for m in mats))
    saved = st.session_state.get("gr_boost")
    if not saved or saved["key"] != key:
        if ctx.datacenter is None:
            st.caption("데이터센터 기능이 꺼져 있어 부스트를 계산할 수 없습니다. 게이지를 직접 정하세요.")
            return None, None
        try:
            with st.spinner("데이터센터 '강화 부스트 도우미'로 계산하는 중…"):
                result = ctx.datacenter.boost(spid, grade, [(m["spid"], m["grade"]) for m in mats])
        except Exception as exc:  # noqa: BLE001
            log.exception("강화 부스트 계산 실패")
            st.caption(friendly_error(exc))
            return None, None
        saved = st.session_state.gr_boost = {"key": key, "result": result}
    result = saved["result"]
    if not result:
        st.caption("부스트를 계산하지 못했습니다. 소모 선수를 바꾸거나 게이지를 직접 정하세요.")
        return None, None
    return min(result["boost"], 100.0), result.get("materials_bp")


def _table_html() -> str:
    rows = "".join(
        f"<tr><td>+{g} → +{g + 1}</td><td>{SUCCESS[g]}%</td><td>OVR +{GRADE_OVR[g + 1] - GRADE_OVR[g]}</td>"
        f"<td>{', '.join(f'+{k} {v:g}%' for k, v in sorted(RECOVER[g].items(), reverse=True))}</td></tr>"
        for g in range(1, MAX_GRADE))
    return ('<div style="overflow-x:auto"><table class="fc-grow-table"><thead><tr><th>강화</th><th>성공 (게이지 100%)</th>'
            '<th>오르는 OVR</th><th>실패 시 복구 (같은 선수 소모 안 함)</th></tr></thead><tbody>' + rows + "</tbody></table></div>")


def render(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">⚒️ 강화 시뮬레이터<small>공식 확률표 그대로 — 한 번씩 강화해 보고, 목표 등급까지 '
                '몇 번이 드는지 계산합니다 · 부스트는 데이터센터 강화 부스트 도우미로 계산</small></div>', unsafe_allow_html=True)
    st.markdown("<style>.fc-grow-table{width:100%;border-collapse:collapse;font-size:13px}.fc-grow-table th,.fc-grow-table td"
                "{padding:6px 8px;border-bottom:1px solid rgba(127,127,127,.25);text-align:left;white-space:nowrap}</style>",
                unsafe_allow_html=True)
    set_col, out_col = st.columns([1, 1.25], gap="large")
    with set_col:
        picked = card_picker(ctx, key="gr_pick", label="강화할")
    if not picked:
        with out_col:   # 고르기 전에도 같은 자리 — 빈 카드 · 등급 판 · 버튼(누를 수 없음)을 먼저 보여 준다
            st.markdown(ui_parts.grow_stage(ui_parts.empty_card(), None), unsafe_allow_html=True)
            st.markdown(ui_parts.grow_board(1, 100.0, 100.0, SUCCESS[1], None, None, False), unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            b1.button("⚒️ 한 번 강화", key="gr_try_empty", type="primary", width="stretch", disabled=True)
            b2.button("🎯 목표까지 강화", key="gr_goal_empty", type="primary", width="stretch", disabled=True)
            st.caption("왼쪽에서 강화할 선수를 고르면 이 카드에 그 선수가 들어갑니다. 아래 표는 공식 강화 확률입니다.")
        with st.expander("📋 공식 강화 확률표 (26.4 기준)", expanded=True):
            st.markdown(_table_html(), unsafe_allow_html=True)
        return
    player = get_player(ctx, picked)
    spid = player["spid"]
    with set_col:
        start = grade_buttons("지금 등급", "gr_start", options=range(1, MAX_GRADE))
        target = grade_buttons("목표 등급", "gr_target", options=range(start + 1, MAX_GRADE + 1))
        mode = st.segmented_control("부스트 게이지", ["직접 정하기", "소모 선수로 계산"], default="직접 정하기", key="gr_mode",
                                    help="게이지가 100% 보다 낮으면 성공 확률 = 공식 확률 × 게이지 비율 (공식 공지)")
        cost = None
        if mode == "소모 선수로 계산":
            boost, cost = _materials(ctx, spid, start)
            if boost is None:
                boost = 100.0
                st.caption("소모 선수를 넣으면 게이지를 계산합니다 — 그전에는 100% 로 봅니다.")
            else:
                st.success(f"강화 부스트 게이지 **{boost:.1f}%** · 소모 선수 가치 합 {bp(cost)}")
        else:
            boost = float(st.slider("게이지 (%)", 0, 100, 100, key="gr_boost_pct"))
        same = st.toggle("같은 선수 카드를 소모 (실패 시 복구 · 크리티컬이 달라짐)", key="gr_same")
        same_grade = None
        if same:
            st.session_state.setdefault("gr_same_g", start)
            same_grade = grade_buttons("소모하는 같은 선수의 등급", "gr_same_g", options=range(1, start + 1))
        if cost is None:
            per_try = st.number_input("1회 강화에 쓰는 소모 선수 값 (억 BP, 선택)", min_value=0.0, value=0.0, step=0.1,
                                      key="gr_cost", help="넣으면 목표까지의 예상 비용을 계산합니다")
            cost = int(per_try * 10**8) or None

    run = st.session_state.get("gr_run")
    if not run or run["spid"] != spid or run["start"] != start or "steps" not in run:   # 예전 형식(단계별 기록 없음)도 새로
        _reset_run(spid, start)
        run = st.session_state.gr_run
    rate = success_rate(run["grade"], boost) if run["grade"] < MAX_GRADE else 0.0
    critical = CRITICAL.get((run["grade"], min(same_grade, run["grade"]))) if same_grade else None
    basics = card_basics(ctx, [spid]).get(spid, {})
    base_ovr, position = basics.get("ovr"), basics.get("position")

    info = ctx.cards.get([spid]).get(spid) or {}
    locked = bool(st.session_state.get("gr_lock"))
    with out_col:
        # 공식 강화 시뮬레이터처럼 가운데에 — 큰 카드(OVR · 포지션 · 급여 · 강화 · 이름 · 국기) → 지금 › 다음 등급 → 확률 · 게이지 칸.
        # 방금 강화한 결과는 카드 아래 양옆에서 — 성공이면 불꽃, 실패면 연기 (시도마다 새로 재생)
        card = ui_parts.pitch_card(player, position or "", ovr_at_grade(base_ovr, run["grade"]), True, run["grade"],
                                   basics.get("pay"), None, info.get("nation_flag", ""), "", high=True)
        st.markdown(ui_parts.grow_stage(card, run.get("fx")), unsafe_allow_html=True)
        st.markdown(ui_parts.grow_board(run["grade"], rate, boost, SUCCESS.get(run["grade"], 0), critical,
                                        run["history"][0] if run["history"] else None, locked), unsafe_allow_html=True)
        if run["grade"] < MAX_GRADE:
            fails = failure_table(run["grade"], same_grade)
            st.caption("실패하면 → " + ", ".join(f"+{g} {p:g}%" for g, p in sorted(fails.items(), reverse=True)))
        reached = run["grade"] >= target
        with st.container(key="gr_actions"):
            b1, b2 = st.columns(2)
            b1.button("⚒️ 한 번 강화", key="gr_try", type="primary", width="stretch", on_click=_one_try, args=(boost, same_grade),
                      disabled=run["grade"] >= MAX_GRADE)
            b2.button(f"🎯 +{target} 까지 강화", key="gr_goal", type="primary", width="stretch", on_click=_to_goal,
                      args=(boost, same_grade, target), disabled=reached,
                      help="이미 목표 등급입니다" if reached else f"+{target} 에 처음 닿으면 멈춥니다 (최대 {GOAL_CAP:,}번)")
            st.checkbox("지금 등급 고정 — 실패해도 등급이 내려가지 않음 (BP 강화 등급 보호처럼)", key="gr_lock",
                        help="끄면 공식 복구표대로 실패할 때 등급이 내려갑니다 (예: +7 실패 → +1 ~ +5)")
        goal = run.get("goal")
        if goal:
            if goal["reached"]:
                text = f"🎯 +{goal['from']} → +{goal['target']}: **{goal['tries']:,}번** 만에 처음 닿아 멈췄습니다"
                if goal["tries"] > goal["last_tries"] and goal["lowest"] < goal["from"]:
                    text += (f" — 실패로 등급이 +{goal['lowest']} 까지 내려가 다시 올리느라 다른 단계 시도가 섞였습니다. "
                             f"+{goal['target'] - 1} → +{goal['target']} 시도는 {goal['last_tries']:,}번 (마지막 1번 성공). "
                             "'지금 등급 고정'을 켜면 실패해도 내려가지 않아 첫 성공에 바로 멈춥니다")
                st.success(text + (f" · 소모 선수 약 {bp(cost * goal['tries'])}" if cost else ""))
            else:
                st.warning(f"🎯 +{goal['from']} → +{goal['target']}: {goal['tries']:,}번 강화했지만 아직 +{run['grade']} "
                           f"(한 번에 최대 {GOAL_CAP:,}번)")
        # 단계별 기록 — 노리는 단계(지금 등급부터)의 성공 비율이 맨 위. 실패로 내려가 다시 올린 시도는 따로 한 줄
        # (전체 성공 비율 하나만 보이면 쉬운 단계가 섞여 +7 → +8 을 노렸는데 69% 처럼 보였다)
        st.markdown(ui_parts.grow_record(run.get("steps") or {}, start, boost), unsafe_allow_html=True)
        if not locked and any(int(g) < start for g in (run.get("steps") or {})):
            st.caption(f"실패하면 공식 복구표대로 등급이 내려가(예: +7 실패 → +1 ~ +5) 그 아래 단계를 다시 올리는 시도가 생깁니다 — "
                       "'다시 올리기' 줄. '지금 등급 고정'을 켜면 실패해도 내려가지 않습니다 (게임의 BP 강화 등급 보호처럼).")
        _, r_col, _ = st.columns([1, 1, 1])
        r_col.button("↺ 초기화", key="gr_reset", width="stretch", on_click=_reset_run, args=(spid, start),
                     help=f"+{start} 로 되돌리고 기록을 지웁니다")
        if run["history"]:
            icon = {"성공": "✅", "크리티컬": "💥", "실패": "❌"}
            with st.expander(f"최근 시도 {min(len(run['history']), HISTORY)}번", expanded=False):
                st.markdown("  \n".join(f"{icon[h['outcome']]} +{h['from']} → **+{h['to']}** {h['outcome']} "
                                        f"<small>(확률 {h['rate']:.2f}%)</small>" for h in run["history"]),
                            unsafe_allow_html=True)

    st.markdown(f'<div class="fc-section">🎲 +{start} → +{target} 까지<small>공식 확률로 정확히 계산 (모의 실험 아님) · 게이지 '
                f'{boost:.0f}%{" · 같은 선수 +" + str(same_grade) + " 소모" if same_grade else ""}'
                f'{" · 등급 보호 (실패해도 그대로)" if locked else " · 실패하면 공식 복구표대로 내려감"}</small></div>',
                unsafe_allow_html=True)
    result = odds(start, target, boost, same_grade, protect=locked)
    cells = [("평균 시도", _count(result["mean"])), ("절반은 이 안에", _count(result["median"])),
             ("90%는 이 안에", _count(result["p90"])), ("100번 안에 닿을 확률", f"{result['within'][100] * 100:.2f}%")]
    if cost and result["mean"]:
        cells.append(("예상 소모 비용 (평균)", bp(int(cost * result["mean"]))))
    prices = ctx.market.table(spid) if ctx.market.enabled else {}
    if not prices and ctx.market.enabled:
        ctx.market.want([spid])
    buy = (prices.get(target) or {}).get("current")
    own = (prices.get(start) or {}).get("current")
    if buy:
        cells.append((f"+{target} 바로 사기 (현재가)", bp(buy)))
    st.markdown('<div class="fc-sum">' + "".join(f"<div><span>{escape(k)}</span><b>{escape(v)}</b></div>" for k, v in cells)
                + "</div>", unsafe_allow_html=True)
    if buy and own and cost and result["mean"]:
        diff = buy - own - int(cost * result["mean"])
        st.caption(f"+{start} 현재가 {bp(own)} + 평균 소모 비용 {bp(int(cost * result['mean']))} vs +{target} 현재가 {bp(buy)} → "
                   + (f"강화가 평균 {bp(diff)} 싸다" if diff > 0 else f"바로 사는 쪽이 평균 {bp(-diff)} 싸다")
                   + " (평균값 기준 · 실패 때 등급이 내려가 값이 떨어지는 것은 반영하지 않음)")
    within = " · ".join(f"{t}번 안에 {p * 100:.2f}%" for t, p in result["within"].items())
    st.caption(f"목표에 닿을 확률 — {within}")
    if result["curve"] and result["mean"]:
        curve = pd.DataFrame([(t, p * 100) for t, p in result["curve"]], columns=["시도 횟수", "누적 도달 확률 (%)"])
        st.line_chart(curve, x="시도 횟수", y="누적 도달 확률 (%)", height=220)
    first = ", ".join(f"+{g} {p * 100:.1f}%" for g, p in sorted(result["first"].items(), reverse=True))
    st.caption(f"첫 시도 결과 (등급별): {first}")
    with st.expander("📋 공식 강화 확률표 (26.4 기준)"):
        st.markdown(_table_html(), unsafe_allow_html=True)
        st.caption("출처: FC 온라인 공지 '선수 강화 성공 확률, 강화 실패 시 복구 확률 및 크리티컬 확률 안내'. 강화 보호권 · BP 강화 등급 "
                   "보호를 쓰지 않았을 때. 같은 선수를 소모하면 복구 표와 크리티컬이 달라집니다 (위 설정에 반영).")
