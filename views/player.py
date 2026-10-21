"""👤 선수 — 카드 · AI 스카우팅 · 소개 전문 · 포지션 내 순위 · 랭커 포지션 사용 · 시즌별 카드 · Q&A · AI 리포트.

빠르게 보이도록: 위에서부터 그릴 수 있는 것을 먼저 그리고, 네트워크가 필요한 칸(분석 보강 · 포지션 사용 · AI 한줄)은
자리만 잡아 두었다가 맨 마지막에 채운다. 사용자는 카드와 소개를 기다리지 않고 본다.
"""

from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

import ui_parts
from data_prep.prompts import TASK_LABELS
from services.ai import scout_line
from services.insights import percentiles, position_usage, trade_prices
from views.common import (Ctx, add_to_compare, back_button, enrich, friendly_error, get_player, log, render_sources,
                          run_with_status, tile_grid, with_saves)
from views.prices import compare_prices, price_section
from views.widgets import card_picker

SAMPLES = ["프로 데뷔는 언제, 어느 팀에서 했어?", "국가대표 경력을 알려 줘", "가장 큰 우승 경력은?", "어떤 플레이 스타일이야?"]


def _ask(question: str) -> None:
    st.session_state.pending_question = question


def _meters(ctx: Ctx, player: dict) -> str:
    """랭커 스탯 막대 — 골키퍼는 경기 기록으로 모은 선방률을 맨 위에."""
    return ui_parts.meters(with_saves(ctx, [player])[0], percentiles(player, ctx.players))


@st.cache_data(ttl=300, show_spinner=False)
def _trades(_ctx, ouid: str) -> dict:
    """연결한 계정의 최근 거래 기록 (구매 · 판매 각 100건) — 5분마다 API 에서 다시 받는다."""
    return {kind: _ctx.nexon.user_trades(ouid, kind, limit=100) for kind in ("buy", "sell")}


def _render_prices(ctx: Ctx, player: dict, slot) -> None:
    """💰 강화 단계별 시세(데이터센터) + 연결한 계정의 실제 거래가(넥슨 Open API 거래 기록)와 비교."""
    with slot.container():
        mine, account = None, ctx.db.account()
        if account:
            try:
                mine = trade_prices(_trades(ctx, account["ouid"]), player["spid"])
            except Exception as exc:  # noqa: BLE001 — 거래 기록이 없어도 시세는 보여 준다
                log.exception("거래가 불러오기 실패")
                st.caption(friendly_error(exc))
        price_section(ctx, player, mine)
        if not account:
            st.caption("🎮 내 기록에서 닉네임으로 계정을 연결하면, 내가 이 카드를 사고판 값과 지금 시세를 강화 단계별로 비교해 줍니다.")
        elif not mine:
            st.caption(f"{account['nickname']} 계정의 최근 거래 200건 중 이 카드를 사고판 기록이 없습니다.")


@st.dialog("⚖️ 바로 비교", width="large")
def compare_dialog(ctx: Ctx, player: dict, others: list[int]) -> None:
    """선수 상세의 '비교하기' 모달 — 다른 시즌은 버튼 한 번, 다른 선수는 이름 → 시즌. 모달 안에서 스탯 차트까지."""
    targets = st.session_state.setdefault("qc", {})   # 선수마다 마지막으로 비교한 카드

    def pick(target: str | None) -> None:
        if target:
            targets[player["player_id"]] = target

    if others:
        st.caption(f"{player['name']}의 다른 시즌과 비교")
        chip_cols = st.columns(6)
        for i, spid in enumerate(others[:12]):
            chip_cols[i % 6].button(ctx.catalog.season_name(spid).split(" (")[0], key=f"qc_{spid}", width="stretch",
                                    on_click=pick, args=(str(spid),))
    st.caption("다른 선수와 비교 — 이름을 적고 선수 · 시즌을 고르세요")
    picked = card_picker(ctx, key="qc_pick")
    st.button("이 카드와 비교", key="qc_go", disabled=not picked or picked == player["player_id"],
              on_click=pick, args=(picked,))

    target = targets.get(player["player_id"])
    if not target or target == player["player_id"]:
        return
    try:
        other = get_player(ctx, target)
        if other.get("lite"):
            with st.spinner("비교할 카드의 랭커 기록을 불러오는 중..."):
                other = enrich(ctx, other)
    except Exception as exc:  # noqa: BLE001
        log.exception("바로 비교 실패")
        st.warning(friendly_error(exc))
        return
    left, mid, right = st.columns([1, 2.4, 1], gap="medium")
    left.markdown(ui_parts.tile(player), unsafe_allow_html=True)
    right.markdown(ui_parts.tile(other), unsafe_allow_html=True)
    mid.markdown(ui_parts.compare_chart(player, other, ui_parts.stat_maxima(ctx.players), ctx.theme), unsafe_allow_html=True)
    compare_prices(ctx, player, other)
    if st.button("✨ 이 두 선수 AI 커리어 비교 (비교 페이지로)", key="qc_ai", type="primary", width="stretch"):
        add_to_compare(player["player_id"], other["player_id"])
        st.rerun()   # 모달을 닫고 비교 페이지로


def render(ctx: Ctx) -> None:
    player_id = st.session_state.player_id
    if not player_id:
        st.info("🏠 홈에서 선수를 검색해 고르세요. 전체 88,359장의 카드를 이름이나 시즌으로 찾을 수 있습니다.")
        return
    try:
        player = get_player(ctx, player_id)
    except KeyError:
        st.warning("카드를 찾지 못했습니다. 홈에서 다시 검색해 주세요.")
        return

    others = [s for s in ctx.catalog.cards_of(player["pid"]) if str(s) != player["player_id"]]

    # ── 헤더: ← 뒤로 · 카드 + 정보 ────────────────────────────────
    back_col, _ = st.columns([1, 9])
    back_button("player", back_col)
    card_col, info_col = st.columns([1, 2.3], gap="large")
    card_col.markdown(ui_parts.player_card(player), unsafe_allow_html=True)
    with info_col:
        st.markdown(f"# {escape(player['name'])}")
        ranker = player.get("ranker") or {}
        st.markdown(ui_parts.chips([
            ("시즌", player["season_short"]),
            ("랭커 주 포지션", ", ".join(player["positions"]) or ("불러오는 중" if player.get("lite") else "기록 없음")),
            ("포지션 그룹", player.get("position_group") or "-"),
            ("이 선수 카드", f"{player['card_count']}장"),
            ("랭커 집계", ranker.get("date", "-")),
        ]), unsafe_allow_html=True)
        ai_slot = st.empty()
        intro_slot = st.empty()
        favorite = player["player_id"] in ctx.favorites.ids()
        b1, b2, b3 = st.columns(3)
        b1.button("★ 즐겨찾기 해제" if favorite else "☆ 즐겨찾기", key="fav_toggle", width="stretch",
                  on_click=ctx.favorites.toggle, args=(player["player_id"],))
        if b2.button("⚖️ 비교하기", key="to_compare", width="stretch", type="primary"):
            compare_dialog(ctx, player, others)
        if player.get("wiki_url"):
            b3.link_button("📖 위키백과 원문", player["wiki_url"], width="stretch")
        else:
            b3.button("📖 위키 문서 없음", disabled=True, width="stretch", key="no_wiki")

    # ── 스탯 · 포지션 사용 ─────────────────────────────────────────
    meter_col, usage_col = st.columns(2, gap="medium")
    meter_slot, usage_slot = meter_col.empty(), usage_col.empty()
    if not player.get("lite"):
        meter_slot.markdown(_meters(ctx, player), unsafe_allow_html=True)
    else:
        meter_slot.info("랭커 기록을 불러오는 중...")
    usage_slot.caption("랭커 포지션 사용을 불러오는 중...")
    price_slot = st.empty()

    # ── 시즌별 다른 카드 ─────────────────────────────────────────
    if others:
        st.markdown(f'<div class="fc-section">🗂️ {escape(player["name"])}의 다른 시즌 카드<small>{len(others)}장 · 최신 시즌부터</small></div>',
                    unsafe_allow_html=True)
        shown = [get_player(ctx, str(s)) for s in others[:12]]
        ctx.prefetcher.schedule([p["spid"] for p in shown if p.get("lite")])   # 누르기 전에 미리 분석
        tile_grid(ctx, shown, key="other", cols=6)
        if len(others) > 12:
            st.caption(f"외 {len(others) - 12}장 — 🏠 홈에서 이름과 시즌으로 찾을 수 있습니다.")

    # ── 탭: Q&A · AI 리포트 · 근거 문서 (비교는 위의 '⚖️ 비교하기' 모달) ─────
    qa_tab, intro_tab, docs_tab = st.tabs([f"💬 {TASK_LABELS['qa']}", f"🪪 AI {TASK_LABELS['intro']} 리포트",
                                           "📚 수집한 근거 문서"])

    with qa_tab:
        history = st.session_state.chats.setdefault(player["player_id"], [])
        if not history:
            st.caption("커리어 · 이적 · 대표팀 · 우승 · 플레이 스타일을 물어보세요. 위키백과와 넥슨 데이터에 있는 것만 근거와 함께 답합니다.")
            for col, sample in zip(st.columns(len(SAMPLES)), SAMPLES):
                col.button(sample, key=f"sample_{sample}", on_click=_ask, args=(sample,), width="stretch")
        for message in history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message["role"] == "assistant":
                    render_sources(message["content"], message.get("evidence", []))
        typed = st.chat_input(f"{player['name']} 선수에 대해 물어보세요", key="qa_input")
        question = typed or st.session_state.pending_question
        st.session_state.pending_question = None
        if question:
            history.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                reply = run_with_status(ctx, "qa", [player], question)
                if reply:
                    st.markdown(reply["answer"])
                    render_sources(reply["answer"], reply["evidence"])
            if reply:
                history.append({"role": "assistant", "content": reply["answer"], "evidence": reply["evidence"]})
            player = ctx.by_id.get(player["player_id"], player)

    with intro_tab:
        key = f"intro:{player['player_id']}"
        saved = st.session_state.results.get(key)
        st.caption("위키백과 선수 문서와 넥슨 랭커 기록만으로 소개를 만듭니다. 문서에 없는 내용은 쓰지 않습니다.")
        if st.button("다시 만들기" if saved else "✨ AI 선수 리포트 만들기", key="make_intro", type="secondary" if saved else "primary"):
            reply = run_with_status(ctx, "intro", [player], "")
            if reply:
                st.session_state.results[key] = saved = reply
        if saved:
            st.markdown(saved["answer"])
            render_sources(saved["answer"], saved["evidence"])

    with docs_tab:
        docs = ctx.collector.stored_docs(player["player_id"])
        if not docs:
            st.caption("아직 수집한 문서가 없습니다. 질문하거나 리포트를 만들면 위키백과에서 모읍니다.")
        for d in docs:
            tag = "선수 문서" if d.get("is_self") else f"검색어: {d.get('search_query', '')}"
            st.markdown(f"- [{d['title']}]({d['url']}) · {tag} · {len(d['text']):,}자 · 수집 {d.get('collected_at', '')}")

    # ── 맨 마지막: 네트워크가 필요한 칸 채우기 (백그라운드가 끝냈으면 즉시) ──
    if player["player_id"] not in ctx.curated_ids:
        try:
            with meter_slot.container():
                with st.spinner("랭커 기록 · 위키 소개를 불러오는 중..."):
                    player = enrich(ctx, player)
            meter_slot.markdown(_meters(ctx, player), unsafe_allow_html=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("카드 보강 실패")
            meter_slot.warning(friendly_error(exc))

    if player.get("intro"):
        with intro_slot.container():
            st.markdown(player["intro"])
            st.caption(f"출처: [위키백과 — {player['wiki_title']}]({player['wiki_url']}) (CC BY-SA 4.0)")
    else:
        intro_slot.caption("이 카드와 연결된 위키백과 문서를 찾지 못했습니다. 랭커 기록과 카드 정보만 보여 줍니다.")

    _render_prices(ctx, player, price_slot)

    try:   # 포지션별 랭커 기록 — players.json(빌드 때 받음) → SQLite → 넥슨 1회 순서
        stored = player.get("ranker_positions")
        rows = ([{**r, "date": date.fromisoformat(r["date"])} for r in stored] if stored
                else ctx.prefetcher.rows(player["spid"]))
        usage_slot.markdown(ui_parts.usage(position_usage(rows)), unsafe_allow_html=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("포지션 사용 불러오기 실패")
        usage_slot.caption(friendly_error(exc))

    try:
        with ai_slot.container():
            with st.spinner("🤖 AI 스카우팅 리포트 작성 중..."):
                line = scout_line(player, ctx.model, ctx.ai_cache, ctx.settings.chat_model)
        if line:
            ai_slot.markdown(ui_parts.ai_box(escape(line).replace("\n", "<br>"), "AI 한줄 스카우팅 · 근거: 선수 메타데이터"),
                             unsafe_allow_html=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI 한줄 스카우팅 실패")
        ai_slot.caption(friendly_error(exc))
