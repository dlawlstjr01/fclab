"""여러 페이지가 함께 쓰는 입력 부품 — 정렬된 시즌 선택, 이름 → 시즌 카드 선택, 의미 검색."""

from __future__ import annotations

import re

import streamlit as st

from data_prep.nexon_api import split_spid
from views.common import Ctx, get_player

# ── 시즌 정렬 ───────────────────────────────────────────────────
# 시즌 ID 는 출시 순서가 아니고 성격이 다른 묶음이 섞여 있다(1xx ICON · 2xx · 3xx/5xx 라이브 · 8xx).
# ICON 계열을 맨 위에(ICON TM → ICON → …), 나머지는 연도 최신순 → 같은 해 안에서는 최근에 나온(ID 가 큰) 순.
# 이름에 연도가 없는 시즌(UC · SPT · PTG …)은 같은 번호대에서 가장 가까운 시즌의 연도를 빌려 쓴다.

YEAR_PATTERNS = [re.compile(r"^\s*20(\d{2})\b"), re.compile(r"^\s*(\d{2})(?!\d)"), re.compile(r"\b[A-Z]{2,4}(\d{2})\b")]


def _year(name: str) -> int | None:
    for pattern in YEAR_PATTERNS:
        m = pattern.search(name)
        if m:
            return int(m.group(1))
    return None


def season_name(class_name: str) -> str:
    """'ICON (ICON)' → 'ICON', '26 TOTS (26 Team Of The Season)' 은 그대로."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", class_name)
    if m and m.group(1).replace(" ", "") == m.group(2).replace(" ", ""):
        return m.group(1)
    return class_name


@st.cache_data(show_spinner=False)
def sorted_seasons(_catalog, count_key: int) -> list[tuple[int, str]]:
    """[(시즌 ID, 보여 줄 이름)] — ICON 계열 → 연도 최신순(같은 해는 최근 출시순)."""
    counts: dict[int, int] = {}
    for c in _catalog.cards:
        sid = split_spid(c["id"])[0]
        counts[sid] = counts.get(sid, 0) + 1
    seasons = {sid: s["className"] for sid, s in _catalog.seasons.items() if counts.get(sid)}
    known = {sid: y for sid, name in seasons.items() if (y := _year(name)) is not None}
    # 연도를 빌려줄 수 있는 시즌 — '2012 KH' 같은 기념 시즌(출시 연도가 아님)은 빼고, 게임 연도(17~)만
    donors = {sid: y for sid, y in known.items() if y >= 17 and not re.match(r"^\s*20\d{2}", seasons[sid])}

    def year_of(sid: int) -> int:
        if sid in known:
            return known[sid]
        same_series = [s for s in donors if s // 100 == sid // 100] or list(donors)
        return donors[min(same_series, key=lambda s: abs(s - sid))] if same_series else 0

    icons = sorted(sid for sid, name in seasons.items() if "ICON" in name.upper())
    others = sorted((sid for sid in seasons if sid not in icons), key=lambda sid: (-year_of(sid), -sid))
    return [(sid, f"{season_name(seasons[sid])} · {counts[sid]:,}장") for sid in icons + others]


def season_select(ctx: Ctx, key: str, label: str = "시즌", all_label: str = "전체 시즌") -> int | None:
    options = sorted_seasons(ctx.catalog, len(ctx.catalog.cards))
    labels = dict(options)
    return st.selectbox(label, [None] + [sid for sid, _ in options], key=key, label_visibility="collapsed",
                        format_func=lambda sid: all_label if sid is None else labels[sid],
                        placeholder="시즌 (이름을 쳐서 찾을 수 있어요)")


# ── 이름 → 시즌 카드 선택 ─────────────────────────────────────────

def _persons(ctx: Ctx, query: str) -> list[int]:
    """이름이 맞는 선수(pid) — 이름이 딱 맞는 사람 먼저, 카드가 많은(유명한) 사람 먼저."""
    seen, pids = set(), []
    for hit in ctx.catalog.search(query, limit=3000):
        pid = split_spid(hit["spid"])[1]
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
    q = query.replace(" ", "")
    pids.sort(key=lambda pid: (ctx.catalog.by_spid[ctx.catalog.cards_of(pid)[0]]["name"].replace(" ", "") != q,
                               -ctx.catalog.cards_per_pid[pid]))
    return pids[:30]


def card_picker(ctx: Ctx, key: str, default_id: str | None = None, label: str = "", cols: int = 4) -> str | None:
    """이름을 치면 아래에 이름이 들어간 선수 카드가 열린다 → 선수를 누르면 그 선수의 시즌만 고를 수 있다.

    고른 카드의 player_id(spid 문자열)를 돌려준다. 이름을 바꾸면 고른 선수가 풀린다.
    """
    import ui_parts   # 순환 import 를 피하려고 여기서

    name_key, who_key, card_key = f"{key}_name", f"{key}_who", f"{key}_card"
    if default_id and name_key not in st.session_state:   # 처음 그릴 때 기본 카드로 채운다
        p = get_player(ctx, default_id)
        st.session_state[name_key] = p["name"]
        st.session_state[who_key] = p["pid"]
        st.session_state[card_key] = int(default_id)

    def reset() -> None:
        st.session_state.pop(who_key, None)
        st.session_state.pop(card_key, None)

    def choose(pid: int) -> None:
        st.session_state[who_key] = pid
        st.session_state.pop(card_key, None)

    query = st.text_input(f"{label} 선수 이름".strip(), key=name_key, placeholder="이름을 입력하세요 (예: 손흥민)", on_change=reset)
    pid = st.session_state.get(who_key)

    if pid is None:
        # 이름이 들어간 선수들 — 카드를 누르면 그 선수로 정해진다
        pids = _persons(ctx, query)[:12] if query.strip() else []
        if query.strip() and not pids:
            st.caption("그 이름의 선수가 없습니다.")
        if pids:
            st.caption(f"'{query.strip()}' 이(가) 들어간 선수 {len(pids)}명 — 눌러서 고르세요")
            with st.container(key=f"grid_pick_{key}"):
                for start in range(0, len(pids), cols):
                    for col, p in zip(st.columns(cols), pids[start : start + cols]):
                        name = ctx.catalog.by_spid[ctx.catalog.cards_of(p)[0]]["name"]
                        with col, st.container(key=f"tile_pick_{key}_{p}"):
                            st.markdown(ui_parts.person_tile(name, p, ctx.catalog.cards_per_pid[p]), unsafe_allow_html=True)
                            st.button(f"{name} 고르기", key=f"{key}_p_{p}", on_click=choose, args=(p,), width="stretch")
        return None

    name = ctx.catalog.by_spid[ctx.catalog.cards_of(pid)[0]]["name"]
    cards = ctx.catalog.cards_of(pid)   # 이 선수의 시즌만

    def card_label(spid: int) -> str:
        mark = " ✓분석" if str(spid) in ctx.by_id or ctx.db.get_player(str(spid)) else ""
        return f"{season_name(ctx.catalog.season_name(spid))}{mark}"

    who_col, season_col, again_col = st.columns([1.4, 2.6, 1], vertical_alignment="bottom")
    who_col.markdown(f"**{name}** · 카드 {len(cards)}장")
    card_args = {} if card_key in st.session_state else {"index": 0}   # 세션 값이 있으면 index 를 주지 않는다
    spid = season_col.selectbox(f"{name}의 시즌", cards, key=card_key, format_func=card_label, **card_args)
    again_col.button("다시 고르기", key=f"{key}_again", on_click=reset, width="stretch")
    return str(spid) if spid is not None else None


# ── 의미 검색 (이름으로 못 찾았을 때) ───────────────────────────────

def semantic_players(ctx: Ctx, query: str, k: int = 12) -> list[dict]:
    """메타데이터 색인(player_meta)에서 질문과 뜻이 가까운 선수 — LLM 없이 임베딩 검색만 (F2011 의 1단계)."""
    docs = ctx.store.search_meta(query, k=k)
    return [ctx.by_id[d.metadata["player_id"]] for d in docs if d.metadata.get("player_id") in ctx.by_id]
