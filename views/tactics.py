"""🧠 전술 추천 — 포메이션 + 선수 11명 → LLM 이 팀 전술 · 개인 전술 · 운영 팁을 근거와 함께 추천. (F2010)

쓰기 쉽게: 들어오면 파워 랭킹 선수로 바로 채워져 있고, 포메이션 · 스타일은 버튼 한 번,
자리마다 '바꾸기'에서 이름 → 시즌으로 교체, '🎮 내 스쿼드'는 연결한 계정의 최근 경기 선발을 불러온다.
전술 항목 · 선택지는 data_prep/tactics.py 에만 있다 (게임 업데이트로 바뀌면 거기만 고친다).
"""

from __future__ import annotations

from html import escape

import streamlit as st

import ui_parts
from data_prep.players import GROUP_OF, stat_values
from data_prep.tactics import FORMATIONS, PITCH_XY, PLAY_STYLES, TEAM_TACTICS
from services.ai import recommend_tactics
from services.insights import latest_squad
from views.common import Ctx, enrich_many, friendly_error, get_player, go, log, render_sources
from views.widgets import card_picker

ATTACK = {"스트라이커", "윙어", "공격형 미드필더"}
DEFENCE = {"센터백", "풀백", "수비형 미드필더"}


def _score(p: dict) -> float:
    v = stat_values(p.get("ranker"))
    if not v:
        return -1
    group = p.get("position_group")
    if group in ATTACK:
        return v["goal"] + v["assist"]
    if group in DEFENCE:
        return v["tackle"] + v["block"] + v["pass_rate"] / 100
    if group == "골키퍼":
        return p["ranker"]["match_count"]
    return v["assist"] + v["pass_rate"] / 50


def _slots() -> list[str]:
    return FORMATIONS[st.session_state.tac_formation]


def _fill_power(ctx: Ctx) -> None:
    """자리마다 같은 포지션 그룹에서 랭커 기록이 가장 좋은 카드 (같은 선수는 한 번만)."""
    used: set[int] = set()
    st.session_state.tac_grades = {}
    for slot in _slots():
        pool = [p for p in ctx.players if p.get("position_group") == GROUP_OF.get(slot) and p.get("ranker")
                and p["ranker"]["match_count"] >= 10 and p["pid"] not in used]
        best = max(pool, key=_score, default=None)
        st.session_state[f"tac_{slot}"] = best["player_id"] if best else None
        if best:
            used.add(best["pid"])
    st.session_state.tac_msg = "⚡ 자리마다 랭커 기록이 가장 좋은 카드로 채웠습니다."


def fill_mine_and_go(ctx: Ctx) -> None:
    """🎮 내 기록의 '이 스쿼드로 전술 추천' — 내 스쿼드를 채우고 전술 페이지로."""
    _fill_mine(ctx)
    st.session_state.tac_ready = True
    go("tactics")


def _fill_mine(ctx: Ctx) -> None:
    """연결한 계정의 최근 공식경기 선발 → 가장 가까운 포메이션을 고르고 자리에 넣는다."""
    account = ctx.db.account()
    if not account:
        st.session_state.tac_msg = "🎮 내 기록에서 닉네임으로 계정을 먼저 연결하세요."
        return
    try:
        squad = latest_squad(ctx.nexon, ctx.catalog, account["ouid"])
    except Exception as exc:  # noqa: BLE001
        log.exception("내 스쿼드 불러오기 실패")
        st.session_state.tac_msg = friendly_error(exc)
        return
    if not squad:
        st.session_state.tac_msg = "최근 공식경기에서 선발 11명을 찾지 못했습니다."
        return
    ctx.db.put_gk_games(squad["gk_games"])   # 이미 받은 경기에서 골키퍼 선방 재료도 남긴다 (추가 호출 없음)
    positions = [p["position"] for p in squad["players"]]
    formation = max(FORMATIONS, key=lambda f: sum(1 for pos in positions if pos in FORMATIONS[f]))
    st.session_state.tac_formation = st.session_state.tac_formation_pick = formation
    left = list(squad["players"])
    st.session_state.tac_grades = {}
    for slot in FORMATIONS[formation]:
        pick = next((p for p in left if p["position"] == slot), None) or \
            next((p for p in left if GROUP_OF.get(p["position"]) == GROUP_OF.get(slot)), None) or (left[0] if left else None)
        st.session_state[f"tac_{slot}"] = str(pick["spid"]) if pick else None
        if pick:
            st.session_state.tac_grades[slot] = pick["grade"]
            left.remove(pick)
    ctx.prefetcher.schedule([p["spid"] for p in squad["players"]])
    st.session_state.tac_msg = (f"🎮 {account['nickname']}의 최근 공식경기({squad['match_date']}, {squad['result']}) 선발을 "
                                f"불러왔습니다 → 가장 가까운 포메이션 {formation}")


def _clear() -> None:
    for slot in PITCH_XY:
        st.session_state[f"tac_{slot}"] = None
    st.session_state.tac_grades = {}
    st.session_state.tac_msg = None


def _apply(slot: str, pick_key: str) -> None:
    spid = st.session_state.get(f"{pick_key}_card")
    if spid:
        st.session_state[f"tac_{slot}"] = str(spid)
        st.session_state.setdefault("tac_grades", {}).pop(slot, None)


def render(ctx: Ctx) -> None:
    st.markdown(ui_parts.PITCH_CSS, unsafe_allow_html=True)
    st.markdown('<div class="fc-section">🧠 AI 전술 추천<small>포메이션 · 스타일을 고르고 선수를 바꾼 뒤 추천받기 — '
                '랭커 기록과 실제 플레이 스타일을 근거로 팀 전술 · 개인 전술을 추천합니다</small></div>', unsafe_allow_html=True)
    st.session_state.setdefault("tac_formation", "4-2-3-1")
    first_visit = "tac_ready" not in st.session_state
    if first_visit:   # 들어오자마자 경기장이 차 있도록
        st.session_state.tac_ready = True
        _fill_power(ctx)

    f_col, s_col = st.columns([1.1, 1.4])
    st.session_state.setdefault("tac_formation_pick", st.session_state.tac_formation)
    st.session_state.setdefault("tac_style", PLAY_STYLES[0])
    with f_col:
        picked = st.pills("포메이션", list(FORMATIONS), key="tac_formation_pick")
        if picked and picked != st.session_state.tac_formation:
            st.session_state.tac_formation = picked
    with s_col:
        style = st.pills("원하는 스타일", PLAY_STYLES, key="tac_style") or PLAY_STYLES[0]
    formation = st.session_state.tac_formation
    slots = FORMATIONS[formation]

    b1, b2, b3, info = st.container(key="wrap_tacbtns").columns([1.3, 1.3, 0.9, 3], vertical_alignment="center")
    b1.button("⚡ 파워 랭킹 추천", key="tac_power", on_click=_fill_power, args=(ctx,), width="stretch")
    account = ctx.db.account()
    b2.button("🎮 내 스쿼드", key="tac_mine", on_click=_fill_mine, args=(ctx,), width="stretch",
              help="연결한 계정의 최근 공식경기 선발 11명" if account else "🎮 내 기록에서 계정을 먼저 연결하세요")
    b3.button("🧹 비우기", key="tac_clear", on_click=_clear, width="stretch")
    info.caption(f"연결한 계정: **{account['nickname']}**" if account else "계정 연결: 🎮 내 기록 → 닉네임으로 연결")
    if st.session_state.get("tac_msg"):
        st.info(st.session_state.tac_msg)

    squad = {slot: get_player(ctx, st.session_state[f"tac_{slot}"]) if st.session_state.get(f"tac_{slot}") else None
             for slot in slots}
    grades = st.session_state.get("tac_grades", {})
    pitch_col, list_col = st.columns([1.05, 1], gap="large")
    pitch_col.markdown(ui_parts.pitch(slots, squad, PITCH_XY, grades), unsafe_allow_html=True)

    with list_col:
        st.markdown("**자리별 선수** — '바꾸기'로 교체")
        for slot in slots:
            p = squad[slot]
            label_col, pop_col = st.columns([3.2, 1.2], vertical_alignment="center")
            grade = f" +{grades[slot]}" if grades.get(slot) else ""
            label_col.markdown(f"`{slot}` " + (f"**{escape(p['name'])}** · {escape(p['season_short'])}{grade}" if p else "_비어 있음_"),
                               unsafe_allow_html=True)
            with pop_col.popover("바꾸기", width="stretch"):
                pick_key = f"tac_pick_{slot}"
                current = p["player_id"] if p else None
                if st.session_state.get(f"{pick_key}_seen") != current:   # 자리 선수가 바뀌었으면 선택기를 그 카드로 다시 채운다
                    for suffix in ("_name", "_who", "_card"):
                        st.session_state.pop(f"{pick_key}{suffix}", None)
                    st.session_state[f"{pick_key}_seen"] = current
                card_picker(ctx, key=pick_key, default_id=current)
                st.button("이 카드로", key=f"tac_apply_{slot}", type="primary", on_click=_apply, args=(slot, pick_key))
        with st.expander("📋 FC 온라인 팀 전술 항목"):
            for name, options, desc in TEAM_TACTICS:
                st.markdown(f"**{name}** · {options}  \n{desc}")
            st.caption("항목 이름 · 선택지는 게임 업데이트로 바뀔 수 있습니다. data_prep/tactics.py 한 곳에서 고칩니다.")

    filled = [(slot, p) for slot, p in squad.items() if p]
    key = f"tactic:{formation}:{style}:" + ",".join(p["player_id"] for _, p in filled)
    saved = st.session_state.results.get(key)
    clicked = st.button(("🔁 다시 추천받기" if saved else "🧠 AI 전술 추천받기") + f"  ·  {formation} · {style}",
                        key="make_tactic", type="primary", width="stretch", disabled=len(filled) < 7,
                        help=None if len(filled) >= 7 else "7자리 이상 채워야 합니다")
    if clicked:
        try:
            with st.status("AI 코치가 스쿼드를 분석하는 중...", expanded=False) as status:
                lite = sum(1 for _, p in filled if p.get("lite"))
                if lite:
                    status.write(f"분석 안 된 카드 {lite}장의 랭커 기록 · 소개를 불러오는 중...")
                    full = enrich_many(ctx, [p for _, p in filled])
                    filled = [(slot, p) for (slot, _), p in zip(filled, full)]
                status.write("전술 항목 설명 + 선수들의 기록을 근거로 추천을 만드는 중... (LLM)")
                saved = recommend_tactics(formation, style, filled, ctx.model)
                st.session_state.results[key] = saved
                status.update(label="전술 추천 완료", state="complete")
        except Exception as exc:  # noqa: BLE001
            log.exception("전술 추천 실패")
            st.error(friendly_error(exc))
    if saved:
        st.markdown('<div class="fc-section">📝 추천 전술</div>', unsafe_allow_html=True)
        st.markdown(saved["answer"])
        render_sources(saved["answer"], saved["evidence"])
