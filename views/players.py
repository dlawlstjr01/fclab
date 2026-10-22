"""👤 선수 — 전체 선수 검색 · 둘러보기. 이름 · 시즌 · 포지션 · 현역/레전드 · 정렬, 한 페이지 24장.

- 이름 · 시즌만 고르면 전체 88,359장에서 찾는다 (네트워크 없이).
- 포지션 · 랭커 기록으로 거르거나 정렬하면 랭커 기록이 있는(분석된) 카드에서 찾는다.
- 🔎 상세 조건(국적 · 리그 · 클럽 · 팀컬러 · 보유 특성 · 주발 · 약발 · 개인기 · 명성 · OVR · 급여)을 하나라도 고르면 전체 카드에서
  데이터센터 선수 검색으로 찾는다 (services/finder.py — 홈의 AI 찾기와 같은 검색).
- 레전드 = 아이콘 카드가 있는 선수의 모든 시즌 카드 (아이콘 시즌만으로 한정하지 않는다).
"""

from __future__ import annotations

import streamlit as st

from data_prep.datacenter import REPUTATION
from data_prep.players import POSITION_GROUPS, SAVE_MIN_SHOTS, SORT_LABELS, SORT_STATS, sort_value
from services.finder import GROUP_CODES, Filters
from views.common import Ctx, back_button, card_ovrs, card_row_list, get_player, search_players, tile_grid, with_saves
from views.widgets import season_select, semantic_players

DETAIL_KEYS = ("pl_nation", "pl_league", "pl_club", "pl_teamcolor", "pl_traits", "pl_foot", "pl_weak", "pl_skill", "pl_rep",
               "pl_ovr_min", "pl_pay_max")


def _clear_detail() -> None:
    for key in DETAIL_KEYS:
        st.session_state[key] = [] if key == "pl_traits" else ("전체" if key == "pl_foot" else (0 if key in ("pl_ovr_min", "pl_pay_max") else None))
    st.session_state.pl_page = 0


def _detail_filters(ctx: Ctx) -> dict | None:
    """🔎 상세 조건 — 고른 것이 있으면 Filters 에 넣을 값, 없으면 None (그때는 예전 검색)."""
    if not ctx.finder.enabled:
        return None
    chosen = any(st.session_state.get(k) not in (None, [], 0, "전체") for k in DETAIL_KEYS)
    with st.expander("🔎 상세 조건 — 국적 · 리그 · 클럽 · 팀컬러 · 보유 특성 · 주발 · 약발 · 개인기 · 명성 · OVR · 급여", expanded=chosen):
        try:
            options = ctx.finder.options()
        except Exception as exc:  # noqa: BLE001
            st.caption(f"검색 목록을 불러오지 못했습니다: {exc}")
            return None
        c1, c2, c3, c4 = st.columns(4)
        nation = c1.selectbox("국적", [None] + sorted(options["nations"]), key="pl_nation", on_change=_reset_page,
                              format_func=lambda v: "전체" if v is None else v, placeholder="국적 (쳐서 찾기)")
        league = c2.selectbox("리그", [None] + list(options["leagues"]), key="pl_league", on_change=_reset_page,
                              format_func=lambda v: "전체" if v is None else v)
        league_id = options["leagues"].get(league) if league else None
        clubs = sorted(n for n, i in options["teams"].items()
                       if not league_id or options["team_league"].get(str(i), options["team_league"].get(i)) == league_id)
        club = c3.selectbox("클럽 (클럽 경력까지)", [None] + clubs, key="pl_club", on_change=_reset_page,
                            format_func=lambda v: "전체" if v is None else v)
        teamcolors = sorted(ctx.teamcolors.all(), key=lambda tc: (tc["kind"], tc["name"]))
        tc = c4.selectbox("팀컬러", [None] + teamcolors, key="pl_teamcolor", on_change=_reset_page,
                          format_func=lambda v: "전체" if v is None else f"{v['name']} · {v['kind']}")
        d1, d2, d3, d4, d5, d6 = st.columns([2.4, 1.3, 0.9, 0.9, 1.1, 1.1])
        traits = d1.multiselect("보유 특성 (최대 3개)", options["traits"], key="pl_traits", max_selections=3,
                                on_change=_reset_page)
        st.session_state.setdefault("pl_foot", "전체")
        foot = d2.segmented_control("주발", ["전체", "오른발", "왼발"], key="pl_foot", on_change=_reset_page)
        weak = d3.selectbox("약발", [None, 1, 2, 3, 4, 5], key="pl_weak", on_change=_reset_page,
                            format_func=lambda v: "전체" if v is None else str(v))
        skill = d4.selectbox("개인기", [None, 1, 2, 3, 4, 5, 6], key="pl_skill", on_change=_reset_page,
                             format_func=lambda v: "전체" if v is None else "★" * v)
        rep_ = d5.selectbox("명성", [None] + list(REPUTATION), key="pl_rep", on_change=_reset_page,
                            format_func=lambda v: "전체" if v is None else v)
        ovr_min = d6.number_input("OVR 이상", min_value=0, max_value=200, step=1, key="pl_ovr_min", on_change=_reset_page)
        e1, e2 = st.columns([1, 5], vertical_alignment="bottom")
        pay_max = e1.number_input("급여 이하", min_value=0, max_value=40, step=1, key="pl_pay_max", on_change=_reset_page)
        e2.button("↺ 상세 조건 지우기", key="pl_detail_clear", on_click=_clear_detail)
    values = {"nation": nation, "league": league, "club": club, "teamcolor": (tc["id"], tc["name"]) if tc else None,
              "traits": tuple(traits), "foot": None if foot in (None, "전체") else foot, "weak_foot": weak, "skill_moves": skill,
              "reputation": rep_, "ovr_min": ovr_min or None, "pay_max": pay_max or None}
    return values if any(v not in (None, ()) for v in values.values()) else None

PAGE = 24
SAVE_NOTE = (f"골키퍼 선방률은 넥슨 랭커 API 에 없어, 🎮 내 기록 · 내 스쿼드가 불러온 경기(최근 30일)의 상대 유효슈팅 · 실점으로 "
             f"계산합니다. 상대 유효슈팅이 {SAVE_MIN_SHOTS}개 이상 모인 카드만 선방률이 보입니다.")


def sort_options(group: str | None) -> dict[str, str | None]:
    """포지션에 맞는 정렬만 — 예) 골키퍼: 선방률 · 랭커 경기 수 · 패스 성공률, 센터백: 태클 · 블록 · 패스."""
    return ({"OVR 높은 순": "ovr", "추천 (최신 시즌 · 대표 스탯)": None, "이름순": "name"}
            | {SORT_LABELS[key] if key == "save_rate" else f"랭커 {SORT_LABELS[key]}": key
               for key in SORT_STATS.get(group, SORT_STATS[None])})


def _stats_note(ctx: Ctx) -> str:
    """모든 카드의 능력치 · 급여 수집(services/stats.py) 진행 — 다 모으기 전에는 OVR 을 모르는 카드가 정렬 뒤쪽에 있다."""
    status = ctx.stats.status()
    if not status["cards"]:
        return ""
    done = f"{status['parts_done']}/{status['parts']}" if status["parts"] else "-"
    doing = f" · {status['current']}" if status["running"] and status["current"] else ""
    return f" · 능력치 DB {status['cards']:,}장 (시즌 {status['seasons']}개 · 능력치 다 모은 조각 {done}{doing})"


def _reset_page() -> None:
    st.session_state.pl_page = 0


def _rows(ctx: Ctx, shown: list[dict]) -> None:
    """선수 줄 목록 — 카드 상세(특성 · 팀컬러 · 주발 · 요약 능력치)를 받는 동안에는 2초마다 이 목록만 다시 그린다."""
    ctx.cards.want([p["spid"] for p in shown])
    if ctx.cards.pending([p["spid"] for p in shown]):
        _rows_live(ctx, shown)
    else:
        card_row_list(ctx, shown, key="pl")


@st.fragment(run_every=2)
def _rows_live(ctx: Ctx, shown: list[dict]) -> None:
    waiting = card_row_list(ctx, shown, key="pl")
    if waiting:
        st.caption(f"⏳ 카드 상세(특성 · 팀컬러 · 주발 · 능력치)를 데이터센터에서 받는 중 — {waiting}장 남음")
    else:
        st.rerun()   # 다 받았다 — 한 번 전체를 다시 그려 멈춘 목록으로


def render(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">👤 선수 찾기<small>전체 88,359장 · 카드를 누르면 상세 페이지</small></div>',
                unsafe_allow_html=True)
    st.session_state.setdefault("pl_page", 0)
    chosen_group = st.session_state.get("pl_group")
    chosen_group = None if chosen_group in (None, "전체") else chosen_group
    sorts = sort_options(chosen_group)
    if st.session_state.get("pl_sort") not in sorts:   # 포지션을 바꾸면 그 포지션의 정렬로
        st.session_state.pl_sort = list(sorts)[3 if chosen_group else 0]   # 포지션을 고르면 그 포지션의 대표 스탯 순
    with st.container(key="wrap_plfilters"):
        back_col, n_col, s_col, k_col, o_col = st.columns([0.9, 2.2, 3.2, 1.3, 2], vertical_alignment="bottom")
        back_button("players", back_col)
        name = n_col.text_input("이름", key="pl_q", placeholder="선수 이름", on_change=_reset_page)
        with s_col:
            season = season_select(ctx, key="pl_season")
        kind = k_col.selectbox("구분", ["전체", "현역", "레전드"], key="pl_kind", on_change=_reset_page)
        sort = o_col.selectbox("정렬", list(sorts), key="pl_sort", on_change=_reset_page)
    st.session_state.setdefault("pl_group", "전체")   # 기본값은 세션으로 — 뒤로 돌아올 때 되살린 값과 겹치지 않게
    group = st.pills("포지션", ["전체"] + list(POSITION_GROUPS), key="pl_group", on_change=_reset_page)
    group = None if group in (None, "전체") else group
    if group == "골키퍼":
        st.caption(SAVE_NOTE)
    sort_key = sorts.get(sort)
    needs_stats = bool(group) or (sort_key not in (None, "name", "ovr")) or (kind not in (None, "전체"))
    st.session_state.pl_page = st.session_state.get("pl_page", 0)
    detail = _detail_filters(ctx)

    if detail is not None:
        # 상세 조건 — 전체 카드에서 데이터센터 선수 검색 (국적 · 클럽 같은 사실은 데이터로)
        filters = Filters(name=name.strip(), seasons=tuple(season or ()), positions=GROUP_CODES.get(group, ()) if group else (),
                          position_label=group or "", legend=None if kind in (None, "전체") else kind == "레전드", **detail)
        try:
            rows = ctx.finder.search(filters)
        except Exception as exc:  # noqa: BLE001
            st.error(f"데이터센터 검색에 실패했습니다: {exc}")
            rows = []
        pool = [get_player(ctx, str(r["spid"])) for r in rows]
        if sort_key == "name":
            pool.sort(key=lambda p: (p["name"], -p["season_id"]))
        source = "데이터센터 상세 조건 검색 (OVR 높은 순 200장까지) · " + " · ".join(filters.chips()[:-1])
    elif needs_stats or not (name.strip() or season):
        # 랭커 기록으로 거르기 · 정렬 — 분석된 카드 안에서
        pool = [p for p in ctx.players if (not name.strip() or name.strip().replace(" ", "") in p["name"].replace(" ", ""))
                and (season is None or p["season_id"] in season)
                and (group is None or p.get("position_group") == group)
                and (kind in (None, "전체") or (kind == "레전드") == (p["pid"] in ctx.finder.legends()))]
        pool = with_saves(ctx, pool)
        if sort_key == "name":
            pool.sort(key=lambda p: (p["name"], -p["season_id"]))
        elif sort_key == "ovr":
            ovrs = card_ovrs(ctx, [p["spid"] for p in pool])
            pool.sort(key=lambda p: (p["spid"] not in ovrs, -ovrs.get(p["spid"], 0), -p["season_id"]))
        elif sort_key:
            pool.sort(key=lambda p: -sort_value(p, sort_key))
        else:   # 추천: 최신 시즌 → 그 포지션의 대표 스탯
            pool.sort(key=lambda p: (-p["season_id"], -sort_value(p, (SORT_STATS.get(p.get("position_group")) or ["points"])[0])))
        source = "랭커 기록이 있는 분석 카드"
    else:
        pool = search_players(ctx, name, season, limit=600)
        if sort_key == "name":
            pool.sort(key=lambda p: (p["name"], -p["season_id"]))
        source = "전체 카드"

    total = len(pool)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(st.session_state.pl_page, pages - 1)
    shown = pool[page * PAGE : (page + 1) * PAGE]
    ctx.prefetcher.schedule([p["spid"] for p in shown if p.get("lite")])
    c_col, v_col = st.columns([4, 1.3], vertical_alignment="center")
    c_col.caption(f"{source}에서 {total:,}장{' (600장까지)' if total >= 600 else ''} · {page + 1}/{pages} 페이지"
                  f"{_stats_note(ctx)}")
    st.session_state.setdefault("pl_view", "☰ 목록")
    v_col.segmented_control("보기", ["☰ 목록", "🗂 카드"], key="pl_view", label_visibility="collapsed")

    if shown:
        if st.session_state.get("pl_view", "☰ 목록") == "🗂 카드":
            tile_grid(ctx, shown, key="pl")
        else:
            _rows(ctx, shown)
    elif name.strip():
        similar = semantic_players(ctx, name.strip())
        if similar:
            st.markdown('<div class="fc-section">📚 이름이 맞는 카드는 없어서 뜻이 가까운 선수를 보여 줍니다</div>',
                        unsafe_allow_html=True)
            tile_grid(ctx, similar, key="pl_sem")
        st.info("조건으로 찾고 싶다면 🏠 홈의 **🤖 AI로 찾기**를 쓰세요.")
    else:
        st.info("조건에 맞는 카드가 없습니다. 필터를 줄여 보세요.")

    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("← 이전", key="pl_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update(pl_page=page - 1))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages}</div>", unsafe_allow_html=True)
        next_col.button("다음 →", key="pl_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update(pl_page=page + 1))
