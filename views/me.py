"""🎮 내 기록 — 닉네임으로 최근 경기 · 내가 쓴 선수의 내 기록 vs 같은 카드 · 같은 포지션 랭커 평균 · AI 코치 · 거래 기록."""

from __future__ import annotations

from html import escape

import streamlit as st

import ui_parts
from data_prep.nexon_api import FACE_IMAGE
from data_prep.players import season_short
from data_prep.tactics import PITCH_XY
from services.ai import coach
from services.insights import analyze_user, latest_squad
from views.common import Ctx, friendly_error, get_player, log, open_player, render_sources
from views.tactics import fill_mine_and_go

# 넥슨 matchtype 메타데이터 이름 그대로. 친선은 랭커 평균이 없어 공식경기 랭커 평균과 비교한다.
MATCH_TYPES = {50: "공식경기", 52: "감독모드", 60: "공식 친선", 30: "리그 친선"}


@st.cache_data(ttl=600, show_spinner=False)
def _analyze(_ctx, nickname: str, matchtype: int) -> dict | None:
    return analyze_user(_ctx.nexon, _ctx.catalog, nickname, matchtype)


@st.cache_data(ttl=600, show_spinner=False)
def _trades(_ctx, nickname: str) -> dict:
    ouid = _ctx.nexon.ouid(nickname)
    return {kind: _ctx.nexon.user_trades(ouid, kind) for kind in ("buy", "sell")} if ouid else {}


def _link(ctx: Ctx) -> None:
    nickname = (st.session_state.get("link_nick") or "").strip()
    if not nickname:
        st.session_state.link_msg = ("warning", "닉네임을 적어 주세요.")
        return
    try:
        ouid = ctx.nexon.ouid(nickname)
    except Exception as exc:  # noqa: BLE001
        log.exception("계정 연결 실패")
        st.session_state.link_msg = ("error", friendly_error(exc))
        return
    if not ouid:
        st.session_state.link_msg = ("warning", f"'{nickname}' 닉네임을 찾지 못했습니다.")
        return
    ctx.db.set_account(nickname, ouid)
    st.session_state.nickname = nickname
    st.session_state.link_msg = ("success", f"🔗 {nickname} 계정을 연결했습니다. 🧠 전술 추천의 '🎮 내 스쿼드'도 이 계정을 씁니다.")


def _unlink(ctx: Ctx) -> None:
    ctx.db.set_account(None)
    st.session_state.link_msg = ("info", "계정 연결을 해제했습니다.")


@st.cache_data(ttl=300, show_spinner=False)
def _squad(_ctx, ouid: str) -> dict | None:
    return latest_squad(_ctx.nexon, _ctx.catalog, ouid)


def render(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">🎮 내 기록<small>넥슨 Open API — 닉네임으로 계정을 연결하면 내 스쿼드 · 최근 경기 · AI 코치를 봅니다</small></div>',
                unsafe_allow_html=True)
    account = ctx.db.account()
    with st.container(border=True):
        if account:
            a_col, b_col = st.columns([4, 1], vertical_alignment="center")
            a_col.markdown(f"🔗 연결된 계정: **{escape(account['nickname'])}** · {account.get('linked_at', '')[:10]} 연결")
            b_col.button("연결 해제", key="unlink", on_click=_unlink, args=(ctx,), width="stretch")
        else:
            a_col, b_col = st.columns([4, 1], vertical_alignment="bottom")
            a_col.text_input("닉네임으로 계정 연결", key="link_nick", placeholder="FC 온라인 닉네임")
            b_col.button("🔗 연결", key="link", on_click=_link, args=(ctx,), type="primary", width="stretch")
        st.caption("넥슨 Open API 에는 외부 서비스용 로그인이 없어, 닉네임으로 공개된 경기 기록만 불러옵니다. 비밀번호는 받지 않습니다.")
        if st.session_state.get("link_msg"):
            kind, text = st.session_state.pop("link_msg")
            getattr(st, kind)(text)

    if account:
        try:
            squad = _squad(ctx, account["ouid"])
        except Exception as exc:  # noqa: BLE001
            log.exception("내 스쿼드 실패")
            squad = None
            st.caption(friendly_error(exc))
        if squad:
            ctx.db.put_gk_games(squad.get("gk_games", []))   # 이미 받은 경기에서 골키퍼 선방 재료도 (추가 호출 없음)
            st.markdown(f'<div class="fc-section">🧑‍🤝‍🧑 내 스쿼드<small>최근 공식경기 선발 · {squad["match_date"]} · {squad["result"]}</small></div>',
                        unsafe_allow_html=True)
            slots = [p["position"] for p in squad["players"]]
            players = {p["position"]: get_player(ctx, str(p["spid"])) for p in squad["players"]}
            grades = {p["position"]: p["grade"] for p in squad["players"]}
            ctx.prefetcher.schedule([p["spid"] for p in squad["players"]])
            st.markdown(ui_parts.PITCH_CSS, unsafe_allow_html=True)
            pitch_col, side_col = st.columns([1, 1.2], gap="large")
            pitch_col.markdown(ui_parts.pitch(slots, players, PITCH_XY, grades), unsafe_allow_html=True)
            with side_col:
                for p in squad["players"]:
                    card = players[p["position"]]
                    line, btn = st.columns([3, 1], vertical_alignment="center")
                    line.markdown(f"`{p['position']}` **{escape(card['name'])}** · {escape(card['season_short'])} +{p['grade']} · 평점 {p['rating']:.1f}")
                    btn.button("상세", key=f"sq_{p['spid']}_{p['position']}", on_click=open_player, args=(str(p["spid"]),),
                               width="stretch")
                st.button("🧠 이 스쿼드로 AI 전술 추천", key="sq_tactic", type="primary", width="stretch",
                          on_click=fill_mine_and_go, args=(ctx,))

    st.markdown('<div class="fc-section">📊 최근 경기 분석</div>', unsafe_allow_html=True)
    if account and not st.session_state.get("nickname"):
        st.session_state.nickname = account["nickname"]
    n_col, t_col, b_col = st.columns([3, 1.4, 1.2], vertical_alignment="bottom")
    nickname = n_col.text_input("닉네임", key="nickname", placeholder="FC 온라인 닉네임")
    matchtype = t_col.selectbox("경기 종류", list(MATCH_TYPES), format_func=MATCH_TYPES.get, key="me_type")
    if b_col.button("🔎 분석하기", key="load_me", type="primary", width="stretch") and nickname.strip():
        try:
            with st.spinner("최근 경기 10개를 불러와 분석하는 중..."):
                st.session_state.me_result = _analyze(ctx, nickname.strip(), matchtype)
                st.session_state.me_nick = nickname.strip()
            if st.session_state.me_result:   # 양 팀 골키퍼 선방 재료 — 선수 · 랭킹 화면의 골키퍼 선방률이 된다
                ctx.db.put_gk_games(st.session_state.me_result.get("gk_games", []))
        except Exception as exc:  # noqa: BLE001
            log.exception("내 기록 분석 실패")
            st.error(friendly_error(exc))

    summary = st.session_state.me_result
    if summary is None:
        if st.session_state.get("me_nick"):
            st.warning("그 닉네임을 찾지 못했습니다.")
        else:
            st.info("닉네임을 넣고 **분석하기**를 누르면 최근 경기 전적, 내가 쓴 선수의 기록과 랭커 평균 비교, AI 코치 조언을 보여 줍니다.")
        return

    rec = summary["record"]
    st.markdown(ui_parts.chips([
        ("닉네임", summary["nickname"]), ("레벨", str(summary["level"])),
        (f"{MATCH_TYPES.get(st.session_state.me_type, '')} 최고 등급",
         f"{summary['division']} ({summary['division_date']})" if summary["division"] else "기록 없음"),
        (f"최근 {len(summary['matches'])}경기", f"{rec['승']}승 {rec['무']}무 {rec['패']}패"),
        ("평균 평점", f"{summary['avg_rating']:.2f}" if summary.get("avg_rating") else "-"),
    ]), unsafe_allow_html=True)
    if not summary["matches"]:
        st.info("이 경기 종류의 최근 경기가 없습니다. 경기 종류를 바꿔 보세요.")
        return
    st.markdown(ui_parts.form_strip(summary["matches"]), unsafe_allow_html=True)

    match_tab, player_tab, coach_tab, trade_tab = st.tabs(["📅 최근 경기", "👥 내 선수 vs 랭커", "🤖 AI 코치", "💰 거래 기록"])
    with match_tab:
        st.dataframe({"날짜": [m["date"] for m in summary["matches"]], "결과": [m["result"] for m in summary["matches"]],
                      "스코어": [m["score"] for m in summary["matches"]], "상대": [m["opponent"] for m in summary["matches"]]},
                     hide_index=True, width="stretch")

    with player_tab:
        baseline = MATCH_TYPES.get(summary.get("ranker_matchtype", 50), "공식경기")
        st.caption(f"내가 그 카드를 그 포지션에 썼을 때의 경기당 기록과, 같은 카드 · 같은 포지션의 랭커 평균({baseline} 기준). "
                   "▲▼ = 랭커 대비 차이." + (" 친선경기는 랭커 평균이 없어 공식경기 랭커와 비교합니다."
                                          if st.session_state.me_type not in (50, 52) else ""))
        for p in summary["players"]:
            mine, rk = p["mine"], p["ranker"]
            face_col, info_col, btn_col = st.columns([0.6, 6, 1.2], vertical_alignment="center")
            face_col.image(FACE_IMAGE.format(pid=p["pid"]), width=52)
            cells = []
            if p.get("save"):   # 골키퍼 — 상대 유효슈팅 · 실점으로 계산한 내 선방률
                s = p["save"]
                cells.append(f"선방률 <b>{s['rate']:.0f}%</b> (상대 유효슈팅 {s['on_target']} · 실점 {s['goals']})")
            stat_list = ((("pass_rate", "패스", "%"),) if p["position"] == "GK" else
                         (("goal", "골", ""), ("assist", "도움", ""), ("shoot", "슈팅", ""), ("pass_rate", "패스", "%"),
                          ("tackle", "태클", "")))
            for key, name, unit in stat_list:
                value = f"{mine[key]:.0f}%" if unit else f"{mine[key]:.2f}"
                cells.append(f"{name} <b>{value}</b> {ui_parts.delta(mine[key], rk[key] if rk else None, unit)}")
            info_col.markdown(
                f"**{escape(p['name'])}** · {escape(p['season_short'])} · {escape(p['position'])} · {p['games']}경기 · 평점 {mine['rating']:.2f}  \n"
                + " &nbsp;·&nbsp; ".join(cells) + ("" if rk else " &nbsp;· 랭커 기록 없음"), unsafe_allow_html=True)
            btn_col.button("선수 보기", key=f"me_open_{p['spid']}_{p['position']}", on_click=open_player,
                           args=(str(p["spid"]),), width="stretch")

    with coach_tab:
        key = f"coach:{summary['nickname']}:{summary['fetched_at']}:{st.session_state.me_type}"
        saved = st.session_state.results.get(key)
        st.caption("🤖 AI 코치는 위 '내 기록'과 '랭커 평균'만 근거로 조언합니다. 근거에 없는 전술 지식은 지어내지 않습니다.")
        if st.button("다시 진단하기" if saved else "✨ AI 코치에게 진단받기", key="make_coach", type="secondary" if saved else "primary"):
            try:
                with st.spinner("AI 코치가 기록을 읽는 중..."):
                    st.session_state.results[key] = saved = coach(summary, ctx.model)
            except Exception as exc:  # noqa: BLE001
                log.exception("AI 코치 실패")
                st.error(friendly_error(exc))
        if saved:
            st.markdown(saved["answer"])
            render_sources(saved["answer"], saved["evidence"])

    with trade_tab:
        try:
            trades = _trades(ctx, summary["nickname"])
        except Exception as exc:  # noqa: BLE001
            log.exception("거래 기록 실패")
            st.caption(friendly_error(exc))
            trades = {}
        for kind, label in (("buy", "구매"), ("sell", "판매")):
            rows = trades.get(kind) or []
            st.markdown(f"**{label} {len(rows)}건**")
            if rows:
                st.dataframe({
                    "카드": [FACE_IMAGE.format(pid=r["spid"] % 1_000_000) for r in rows],
                    "선수": [ctx.catalog.by_spid.get(r["spid"], {}).get("name", r["spid"]) for r in rows],
                    "시즌": [season_short(ctx.catalog.season_name(r["spid"])) for r in rows],
                    "강화": [f"+{r.get('grade', 0)}" for r in rows],
                    "금액(BP)": [r.get("value", 0) for r in rows],
                    "거래일": [r.get("tradeDate", "")[:16].replace("T", " ") for r in rows],
                }, column_config={"카드": st.column_config.ImageColumn("", width="small"),
                                  "금액(BP)": st.column_config.NumberColumn(format="localized")},
                    hide_index=True, width="stretch")
