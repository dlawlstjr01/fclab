"""여러 페이지가 함께 쓰는 입력 부품 — 정렬된 시즌 선택, 이름 → 시즌 카드 선택, 의미 검색."""

from __future__ import annotations

import re
from html import escape

import streamlit as st

from data_prep.nexon_api import split_spid
from data_prep.players import season_short
from views.common import Ctx, by_ovr, card_ovrs, get_player

# ── 강화 등급 고르기 ─────────────────────────────────────────────

def grade_buttons(label: str, key: str, options=range(1, 14), on_change=None, args: tuple = ()) -> int:
    """강화 등급을 버튼으로 (드롭다운 · 슬라이더 대신) — 등급마다 공식 색(1 회색 · 2~4 동 · 5~7 은 · 8~10 금 · 11~13 백금),
    고른 등급은 흰 테두리. 값은 st.session_state[key] 에 두고 돌려준다. on_change(*args) 는 고를 때 한 번."""
    import ui_parts   # 순환 import 를 피하려고 여기서

    options = list(options)
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    value = st.session_state[key]

    def choose(grade: int) -> None:
        st.session_state[key] = grade
        if on_change:
            on_change(*args)

    if label:
        st.markdown(f'<div class="fc-gradelabel">{escape(label)}</div>', unsafe_allow_html=True)
    with st.container(horizontal=True, gap="small", key=f"gradebox_{key}"):
        for grade in options:
            state = "on" if grade == value else "off"
            with st.container(key=f"gbtn_{ui_parts.grade_tier(grade)}_{state}_{key}_{grade}", width="content"):
                st.button(f"+{grade}", key=f"{key}__{grade}", on_click=choose, args=(grade,))
    return value


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


def season_family(class_name: str) -> tuple[str, str]:
    """한 시즌의 갈래들을 하나로 묶을 이름 — '25IM - S&P (25 ICONS MATCH - Show & Prove)' · '25IM - FB (…)' →
    ('25IM', '25 ICONS MATCH'). 사람들은 갈래를 나눠 찾지 않는다. '20 TOTY-N' 처럼 띄어 쓰지 않은 '-' 는 다른 시즌이다."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", class_name)
    short, long = (m.group(1), m.group(2)) if m else (class_name, "")
    return short.split(" - ")[0].strip(), long.split(" - ")[0].strip()


@st.cache_data(show_spinner=False)
def sorted_seasons(_catalog, count_key: int) -> list[tuple[tuple[int, ...], str]]:
    """[(시즌 ID 들, 보여 줄 이름)] — ICON 계열 → 연도 최신순(같은 해는 최근 출시순).
    갈래만 다른 시즌(25IM - S&P · 25IM - FB)은 한 줄로 합친다 — 고르면 갈래 모두."""
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
    families: dict[str, list[int]] = {}   # 먼저 나온 갈래의 자리에 — 순서는 위 정렬 그대로
    for sid in icons + others:
        families.setdefault(season_family(seasons[sid])[0], []).append(sid)
    out = []
    for sids in families.values():
        if len(sids) == 1:
            out.append(((sids[0],), f"{season_name(seasons[sids[0]])} · {counts[sids[0]]:,}장"))
            continue
        short, long = season_family(seasons[sids[0]])
        out.append((tuple(sorted(sids)), f"{short} ({long}) · {sum(counts[s] for s in sids):,}장"))
    return out


def season_select(ctx: Ctx, key: str, label: str = "시즌", all_label: str = "전체 시즌") -> tuple[int, ...] | None:
    """고른 시즌의 ID 들 (갈래를 합친 시즌이면 여럿) — 전체면 None."""
    options = sorted_seasons(ctx.catalog, len(ctx.catalog.cards))
    labels = dict(options)
    if st.session_state.get(key) is not None and st.session_state[key] not in labels:   # 예전 값(시즌 ID 하나) · 사라진 시즌
        st.session_state[key] = next((ids for ids in labels if st.session_state[key] in ids), None)
    return st.selectbox(label, [None] + [ids for ids, _ in options], key=key, label_visibility="collapsed",
                        format_func=lambda ids: all_label if ids is None else labels[ids],
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
    cards = by_ovr(ctx, ctx.catalog.cards_of(pid))   # 이 선수의 시즌만 — OVR 높은 순
    if st.session_state.get(card_key) not in cards:
        st.session_state[card_key] = cards[0]
    chosen = st.session_state[card_key]

    def pick(spid: int) -> None:
        st.session_state[card_key] = spid

    who_col, again_col = st.columns([3, 1.3], vertical_alignment="center")   # 버튼 칸을 넉넉히 — '다시 고르기'가 두 줄로 넘어가지 않게
    who_col.markdown(f"**{name}** · {len(cards)}장 · 고른 카드 **{season_short(ctx.catalog.season_name(chosen))}**")
    with again_col.container(key=f"again_{key}"):
        st.button("↺ 다시 고르기", key=f"{key}_again", on_click=reset, width="stretch")
    # 시즌마다 그 시즌 카드 배경 위 선수 액션샷 · OVR — 드롭다운 글자만으로는 어떤 카드인지 알기 어렵다
    ovrs = card_ovrs(ctx, cards)
    items = []
    for spid in cards:
        full = ctx.catalog.season_name(spid)
        items.append((spid, ctx.catalog.basic_player(spid), season_short(full),
                      full + (" · 분석된 카드" if str(spid) in ctx.by_id else ""), ovrs.get(spid)))
    card_tiles(f"{key}_c", items, chosen, pick)
    return str(chosen)


TILE_WIDTH = 78     # 카드 그림 타일 한 칸 (px) — 작게, 칸이 좁으면 줄을 바꿔 채운다
SCROLL_AFTER = 14   # 이보다 많으면 높이를 묶고 스크롤 (두 줄 남짓)


def card_tiles(prefix: str, items: list[tuple], chosen, on_pick) -> None:
    """카드 그림 타일 묶음 — items = [(값, 선수 dict, 이름, 설명, OVR)]. 타일을 누르면 on_pick(값), 고른 것은 초록 테두리.
    st.columns 대신 고정 너비 타일을 가로로 이어 붙여 줄바꿈 — 칸이 좁아져도 마지막 카드가 한 줄을 다 차지하지 않는다.
    버튼 키는 f"{prefix}_{값}" (타일 전체가 버튼 — ui_parts.css 의 .st-key-tile_*)."""
    import ui_parts   # 순환 import 를 피하려고 여기서

    with st.container(key=f"grid_ssn_{prefix}", height=262 if len(items) > SCROLL_AFTER else "content"):
        with st.container(horizontal=True, gap="small", key=f"row_ssn_{prefix}"):
            for value, player, label, title, ovr in items:
                with st.container(key=f"tile_ssn_{prefix}_{value}", width=TILE_WIDTH):
                    st.markdown(ui_parts.season_tile(player, label, title, ovr, on=value == chosen), unsafe_allow_html=True)
                    st.button(f"{label} 고르기", key=f"{prefix}_{value}", on_click=on_pick, args=(value,),
                              disabled=value == chosen)


# ── 의미 검색 (이름으로 못 찾았을 때) ───────────────────────────────

def semantic_players(ctx: Ctx, query: str, k: int = 12) -> list[dict]:
    """메타데이터 색인(player_meta)에서 질문과 뜻이 가까운 선수 — LLM 없이 임베딩 검색만 (F2011 의 1단계)."""
    docs = ctx.store.search_meta(query, k=k)
    return [ctx.by_id[d.metadata["player_id"]] for d in docs if d.metadata.get("player_id") in ctx.by_id]
