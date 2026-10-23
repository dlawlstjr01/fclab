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
from data_prep.enhance import GRADE_OVR
from data_prep.players import POSITION_GROUPS, SORT_LABELS, SORT_STATS, STATS
from services.finder import Filters, club_label, members
from services.insights import leaderboard
from services.teamcolor import effect_bonus, enhance_details
from views.common import (Ctx, card_basics, crests_of, daily_wait, friendly_error, get_player, open_player, tile_grid,
                          with_saves)
from views.players import SAVE_NOTE

APPLIED = "OVR (강화 · 팀컬러 적용)"   # 랭커 분석의 정렬 — 상세 조건의 강화 등급 · 강화 팀컬러 · 팀컬러를 더한 OVR
COND_KEYS = ("nation", "club", "tc", "enh", "traits", "foot")


def _clear_conditions(prefix: str) -> None:
    """상세 조건을 처음으로 — 값을 직접 넣는다 (지우기만 하면 브라우저의 선택 칸에 예전 값이 남는다)."""
    for name in COND_KEYS:
        st.session_state[f"{prefix}_{name}"] = [] if name == "traits" else ("전체" if name == "foot" else None)
    st.session_state[f"{prefix}_grade"] = 1
    st.session_state.rank_page = 0


def _level_options(ctx: Ctx, tc: dict | None) -> list[dict]:
    """팀컬러의 단계 [{level, count, effects}] — 받아 둔 단계가 없으면 지금 받는다 (몇 개뿐)."""
    if not tc:
        return []
    detail = ctx.teamcolors.details([tc["id"]]).get(tc["id"])
    if detail is None and ctx.teamcolors.enabled:
        try:
            detail = ctx.teamcolors.detail_now(tc["id"])
        except Exception:  # noqa: BLE001 — 단계를 모르면 최고 단계 효과만
            detail = None
    return (detail or {}).get("levels") or [{"level": tc.get("max_level") or 1, "count": tc.get("max_count") or 0,
                                             "effects": tc.get("effects") or []}]


def _conditions(ctx: Ctx, prefix: str, ranker: bool) -> dict | None:
    """🔎 상세 조건 — 국적 · 클럽(클럽 경력) · 팀컬러(소속 · 특성, 단계) · 강화 팀컬러(단계) · 보유 특성 · 주발
    (+ 랭커 분석은 강화 등급 — 랭커 기록에는 강화 등급이 없어, 고른 강화 등급 · 강화 팀컬러로 쓸 때의 OVR 을 계산한다).
    고른 것이 없으면 None."""
    if not ctx.finder.enabled:
        return None
    chosen = any(st.session_state.get(f"{prefix}_{n}") not in (None, [], "전체") for n in COND_KEYS) \
        or (ranker and (st.session_state.get(f"{prefix}_grade") or 1) > 1)
    title = "🔎 상세 조건 — 국적 · 클럽 · 팀컬러 · 강화 팀컬러 · 보유 특성 · 주발" + (" · 강화 등급" if ranker else "")
    with st.expander(title, expanded=chosen):
        try:
            options = ctx.finder.options()
            teamcolors = ctx.teamcolors.all()
            enhance_info = enhance_details(ctx.teamcolors)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"검색 목록을 불러오지 못했습니다 — {friendly_error(exc)}")
            return None
        normal = sorted((tc for tc in teamcolors if tc["kind"] != "강화"), key=lambda tc: (tc["kind"], tc["name"]))
        enhance = sorted((tc for tc in teamcolors if tc["kind"] == "강화" and (enhance_info.get(tc["id"]) or {}).get("min_grade")),
                         key=lambda tc: enhance_info[tc["id"]]["min_grade"])
        c1, c2, c3, c4 = st.columns([1.3, 1.7, 2.2, 0.9], vertical_alignment="bottom")
        nation = c1.selectbox("국적", [None] + sorted(options["nations"]), key=f"{prefix}_nation",
                              format_func=lambda v: "전체" if v is None else v)
        club = c2.selectbox("클럽 (클럽 경력까지)", [None] + sorted(options["teams"]), key=f"{prefix}_club",
                            format_func=lambda v: "전체" if v is None else club_label(v),
                            help="실제 이름 · 줄임말로도 찾을 수 있습니다 (예: 인테르 → 롬바르디아 FC, AC 밀란 → 밀라노 FC)")
        tc = c3.selectbox("팀컬러 (소속 · 특성 — 이 팀컬러에 드는 카드만)", [None] + normal, key=f"{prefix}_tc",
                          format_func=lambda v: "전체" if v is None else f"{club_label(v['name'])} · {v['kind']}")
        tc_levels = _level_options(ctx, tc)
        tc_level = c4.selectbox("단계", tc_levels, index=len(tc_levels) - 1 if tc_levels else 0,
                                key=f"{prefix}_tclv_{tc['id'] if tc else 'none'}", disabled=not tc,
                                format_func=lambda lv: f"Lv.{lv['level']}") if tc_levels else \
            c4.selectbox("단계", ["-"], key=f"{prefix}_tclv_none", disabled=True)
        d1, d2, d3, d4 = st.columns([2.2, 0.9, 2.2, 1.5], vertical_alignment="bottom")
        enh = d1.selectbox("강화 팀컬러", [None] + enhance, key=f"{prefix}_enh",
                           format_func=lambda v: "없음" if v is None else f"{v['name']} ({enhance_info[v['id']]['min_grade']}강 이상)")
        enh_levels = (enhance_info.get(enh["id"]) or {}).get("levels") or [] if enh else []
        enh_level = d2.selectbox("단계", enh_levels, index=len(enh_levels) - 1 if enh_levels else 0,
                                 key=f"{prefix}_enhlv_{enh['id'] if enh else 'none'}", disabled=not enh,
                                 format_func=lambda lv: f"Lv.{lv['level']}") if enh_levels else \
            d2.selectbox("단계", ["-"], key=f"{prefix}_enhlv_none", disabled=True)
        traits = d3.multiselect("보유 특성 (최대 3개)", options["traits"], key=f"{prefix}_traits", max_selections=3,
                                placeholder="특성 이름 (쳐서 찾기)")
        st.session_state.setdefault(f"{prefix}_foot", "전체")
        foot = d4.segmented_control("주발", ["전체", "오른발", "왼발"], key=f"{prefix}_foot")
        grade = 1
        if ranker:
            st.session_state.setdefault(f"{prefix}_grade", 1)
            grade = st.select_slider("강화 등급 — 이 등급으로 쓸 때의 OVR (랭커 기록에는 강화 등급이 없다)", options=list(range(1, 14)),
                                     key=f"{prefix}_grade", format_func=lambda g: f"+{g}강")
            if enh and grade < enhance_info[enh["id"]]["min_grade"]:
                st.caption(f"{enh['name']} 은(는) {enhance_info[enh['id']]['min_grade']}강 이상이라, 적용 OVR 은 "
                           f"+{enhance_info[enh['id']]['min_grade']}강으로 계산합니다.")
        st.button("↺ 상세 조건 지우기", key=f"{prefix}_clear", on_click=_clear_conditions, args=(prefix,))
    cond = {"nation": nation, "club": club, "tc": tc, "tc_level": tc_level if tc and isinstance(tc_level, dict) else None,
            "enh": enh, "enh_level": enh_level if enh and isinstance(enh_level, dict) else None,
            "enh_min": enhance_info[enh["id"]]["min_grade"] if enh else None, "traits": tuple(traits),
            "foot": None if foot in (None, "전체") else foot, "grade": grade}
    if not (nation or club or tc or enh or traits or cond["foot"] or grade > 1):
        return None
    return cond


def _match(ctx: Ctx, cond: dict, spids: list[int]) -> set[int] | None:
    """상세 조건 중 카드를 거르는 것(국적 · 클럽 · 팀컬러 · 특성 · 주발)에 맞는 카드 — 거를 것이 없으면 None.
    데이터센터 선수 검색으로 (같은 조건은 하루 동안 다시 묻지 않는다)."""
    tc = cond["tc"]
    f = Filters(nation=cond["nation"], club=cond["club"], teamcolor=(tc["id"], tc["name"]) if tc else None,
                traits=cond["traits"], foot=cond["foot"])
    if f.empty():
        return None
    seasons: dict[int, int] = {}
    for spid in spids:
        seasons[spid // 1_000_000] = seasons.get(spid // 1_000_000, 0) + 1
    with st.spinner("데이터센터에서 조건에 맞는 카드를 찾는 중… (같은 조건은 하루 동안 다시 묻지 않습니다)"):
        found = members(ctx.finder, f, seasons)
    # 국적 · 클럽 경력 · 클럽/국가 팀컬러는 선수마다 정해진다 — 같은 선수의 다른 시즌 카드도 맞는다
    by_player = not cond["traits"] and not cond["foot"] and (not tc or tc["kind"] in ("클럽", "국가"))
    if not found["complete"]:
        st.caption("조건에 맞는 카드가 많아 카드가 많은 시즌부터 찾았습니다 — 적은 시즌의 카드는 빠질 수 있습니다.")
    return {s for s in spids if s in found["spids"] or (by_player and s % 1_000_000 in found["pids"])}


def _applied_ovr(cond: dict | None, base: int | None, member: bool) -> tuple[int | None, str]:
    """상세 조건의 강화 등급 · 강화 팀컬러 · 팀컬러(그 팀컬러에 드는 카드만)를 더한 OVR 과 그 내역."""
    if base is None:
        return None, ""
    if not cond:
        return base, ""
    grade = max(cond["grade"], cond["enh_min"] or 1)
    parts, total = [], base + GRADE_OVR[grade]
    if grade > 1:
        parts.append(f"+{grade}강 +{GRADE_OVR[grade]}")
    if cond["enh"] and cond["enh_level"]:
        add = effect_bonus(cond["enh_level"]["effects"])[0]
        total += add
        parts.append(f"{cond['enh']['name']} Lv.{cond['enh_level']['level']} +{add}")
    if cond["tc"] and cond["tc_level"] and member:
        add = effect_bonus(cond["tc_level"]["effects"])[0]
        total += add
        parts.append(f"{cond['tc']['name']} Lv.{cond['tc_level']['level']} +{add}")
    return total, " · ".join(parts)

RATE_STATS = ("PASS", "DRIBBLE", "AERIAL", "TACKLE", "BLOCK")   # 성공률(%) 칸
SHOWN_STATS = ("MATCHCOUNT", "ATTACKPOINT", "GOAL", "ASSIST", "PASS", "DRIBBLE", "TACKLE", "INTERCEPT", "RATING")
LINES = {"전체": None, "공격수": {"ST", "CF", "LW", "RW", "LS", "RS", "LF", "RF"},
         "미드필더": {"CAM", "CM", "CDM", "LM", "RM", "LAM", "RAM", "LCM", "RCM", "LDM", "RDM"},
         "수비수": {"CB", "LB", "RB", "LWB", "RWB", "SW", "LCB", "RCB"}, "골키퍼": {"GK"}}
MODE_LABELS = {"official": "공식 경기 1 ON 1", "manager": "감독 모드"}
MODE_SHORT = {"official": "공식 경기", "manager": "감독 모드"}   # 고르기 버튼 — 긴 이름은 칸을 넘겨 두 줄이 됐다


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
    o_col, m_col, k_col, l_col = st.container(key="wrap_dailyfilters").columns([2, 1.9, 1.2, 3])
    names = {label: key for key, label in DAILY_STATS.items()}
    label = o_col.selectbox("기록", list(names), key="daily_order")
    mode = m_col.segmented_control("모드", list(MODE_LABELS), default="official", key="daily_mode",
                                   format_func=MODE_SHORT.get, help="공식 경기 1 ON 1 · 감독 모드 랭커의 기록") or "official"
    kind = k_col.selectbox("구분", ["전체", "현역", "레전드"], key="daily_kind",
                           help="레전드 = 아이콘 카드가 있는 선수의 모든 시즌 카드 · 현역 = 최근 두 해의 현역 시즌(LIVE · TOTS · PL …) 카드가 있는 선수")
    line = l_col.pills("줄", list(LINES), default="전체", key="daily_line") or "전체"
    cond = _conditions(ctx, "dc", ranker=False)
    order = names[label]
    data = ctx.daily.stats(order, mode)
    if data is None:
        if ctx.daily.pending():
            daily_wait(ctx)
        else:
            st.info("데일리 기록을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.")
        return
    rows = data["rows"]
    legends, actives = ctx.finder.legends(), ctx.finder.actives()
    if kind != "전체":
        rows = [r for r in rows if r["spid"] % 1_000_000 in (legends if kind == "레전드" else actives)]
    if LINES[line]:
        rows = [r for r in rows if r["position"] in LINES[line]]
    if cond:
        if cond["enh_min"]:   # 강화 팀컬러 — 랭커가 그 강화 등급 이상으로 쓴 카드만 (데일리 기록에는 강화 등급이 있다)
            rows = [r for r in rows if (r.get("grade") or 0) >= cond["enh_min"]]
        matched = _match(ctx, cond, [r["spid"] for r in rows])
        if matched is not None:
            rows = [r for r in rows if r["spid"] in matched]
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
    items = []
    for rank, r in enumerate(rows, start=1):
        stats = " · ".join((f"<b>{DAILY_STATS[k]} {daily_value(k, r['stats'])}</b>" if k == order
                            else f"{DAILY_STATS[k]} {daily_value(k, r['stats'])}") for k in SHOWN_STATS if k in r["stats"])
        items.append({"rank": rank, "player": players[str(r["spid"])], "grade": r.get("grade") or 1, "ovr": r["ovr"],
                      "position": r["position"], "pay": r.get("pay"), "stats": stats})
    rank_rows(ctx, items, "drank_open")   # 같은 카드가 강화 등급만 달리 두 번 나올 수 있다 — 키에 순위가 들어간다


def rank_rows(ctx: Ctx, items: list[dict], key: str) -> None:
    """순위 줄 — 👤 선수 찾기 목록과 같은 줄(카드 그림 · 포지션 · 시즌 · 이름 · 급여 · 강화 / 시즌 이름 · 주발 / 국적 · 팀컬러 /
    요약 능력치 / 특성 · OVR) 앞에 순위, 밑에 그 카드의 랭커 기록, 오른쪽에 상세보기.
    items = [{rank, player, grade, ovr, position, pay, stats(HTML)}]. 카드 상세(국적 · 팀컬러 · 특성 · 능력치)를 받는 동안에는
    2초마다 이 목록만 다시 그린다."""
    spids = [it["player"]["spid"] for it in items]
    ctx.cards.want(spids)
    if ctx.cards.pending(spids):
        _rank_rows_live(ctx, items, key)
    else:
        _rank_rows_draw(ctx, items, key)


@st.fragment(run_every=2)
def _rank_rows_live(ctx: Ctx, items: list[dict], key: str) -> None:
    left = _rank_rows_draw(ctx, items, key)
    if left:
        st.caption(f"⏳ 카드 상세(국적 · 팀컬러 · 특성 · 능력치)를 데이터센터에서 받는 중 — {left}장 남음")
    else:
        st.rerun()   # 다 받았다 — 한 번 다시 그려 멈춘 목록으로


def _rank_rows_draw(ctx: Ctx, items: list[dict], key: str) -> int:
    spids = [it["player"]["spid"] for it in items]
    infos = ctx.cards.get(spids)
    known = ctx.db.card_stats([s for s in spids if s not in infos])
    for it in items:
        p = it["player"]
        info, row = infos.get(p["spid"]), known.get(p["spid"]) or {}
        line_col, btn = st.columns([6, 1], vertical_alignment="center")
        line_col.markdown(ui_parts.card_row(p, info, grade=it["grade"], ovr=it["ovr"], position=it["position"], pay=it["pay"],
                                            foot=(info or {}).get("foot_lr") or row.get("foot"), crests=crests_of(ctx, info),
                                            rank=it["rank"], extra=it["stats"], on_card=True), unsafe_allow_html=True)
        if btn.button("상세보기", key=f"{key}_{it['rank']}_{p['spid']}", width="stretch"):
            open_player(p["player_id"])   # 조각(fragment) 안에서도 화면이 바뀌게 — 콜백 대신 눌린 뒤 앱 전체를 다시
            st.rerun()
    return ctx.cards.pending(spids)


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
    kind = k_col.selectbox("구분", ["전체", "현역", "레전드"], key="rank_kind", on_change=first_page,
                           help="레전드 = 아이콘 카드가 있는 선수의 모든 시즌 카드 · 현역 = 최근 두 해의 현역 시즌(LIVE · TOTS · PL …) 카드가 있는 선수")
    names = {SORT_LABELS[k]: k for k in SORT_STATS.get(group, SORT_STATS[None])} | {APPLIED: "applied"}   # 포지션에 맞는 정렬만
    if st.session_state.get("rank_sort") not in names:
        st.session_state.rank_sort = next(iter(names))
    sort_name = s_col.selectbox("정렬 기준", list(names), key="rank_sort", on_change=first_page)
    st.session_state.setdefault("rank_min", 10)
    min_matches = m_col.number_input("최소 경기 수 (1~20)", min_value=1, max_value=20, key="rank_min", on_change=first_page,
                                     help="랭커 기록은 카드마다 최근 최대 20경기라 20까지 고를 수 있습니다. "
                                          "경기 수가 적은 카드는 평균이 흔들립니다.")
    if group == "골키퍼":
        st.caption(SAVE_NOTE)
    cond = _conditions(ctx, "rk", ranker=True)

    key = names[sort_name]
    rows = leaderboard(with_saves(ctx, ctx.players), group, kind or "전체", "match_count" if key == "applied" else key,
                       min_matches, ctx.finder.legends(), ctx.finder.actives())
    matched = _match(ctx, cond, [r["player"]["spid"] for r in rows]) if cond else None
    if matched is not None:
        rows = [r for r in rows if r["player"]["spid"] in matched]
    basics = card_basics(ctx, [r["player"]["spid"] for r in rows])
    for r in rows:   # 적용 OVR — 대표 포지션 OVR(+1)에 상세 조건의 강화 · 팀컬러를 더한 값
        base = (basics.get(r["player"]["spid"]) or {}).get("ovr")
        r["applied"], r["applied_note"] = _applied_ovr(cond, base, matched is None or r["player"]["spid"] in matched)
    if key == "applied":
        rows.sort(key=lambda r: (r["applied"] is None, -(r["applied"] or 0)))
    if not rows:
        st.info("조건에 맞는 카드가 없습니다. 상세 조건을 줄이거나 최소 경기 수를 낮춰 보세요.")
        return

    st.markdown(f'<div class="fc-section">🥇 TOP 3 — {sort_name}</div>', unsafe_allow_html=True)

    def shown(value: float | None) -> str:
        if value is None:   # 선방 표본이 없는 골키퍼 · OVR 을 모르는 카드
            return "기록 부족"
        if key == "applied":
            return f"{value:.0f}"
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
    grade = max(cond["grade"], cond["enh_min"] or 1) if cond else 1   # 상세 조건의 강화 등급으로 보인다
    items = []
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
        if r.get("applied") is not None:
            ovr_text = f"OVR {r['applied']}" + (f" ({r['applied_note']})" if r.get("applied_note") else "")
            stats = (f"<b>{escape(ovr_text)}</b>" if key == "applied" else escape(ovr_text)) + " · " + stats
        card = basics.get(p["spid"]) or {}
        base = card.get("ovr")
        items.append({"rank": rank, "player": p, "grade": grade, "pay": card.get("pay"),
                      "ovr": r["applied"] if r.get("applied") is not None else (base + GRADE_OVR[grade] if base else None),
                      "position": card.get("position") or next(iter(p.get("positions") or []), None),
                      "stats": f"랭커 {p['ranker']['match_count']}경기 · {stats}"})
    rank_rows(ctx, items, "rank_open")
    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("← 이전", key="rank_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update(rank_page=page - 1))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages}</div>", unsafe_allow_html=True)
        next_col.button("다음 →", key="rank_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update(rank_page=page + 1))
