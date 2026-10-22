"""🔎 상세 검색 — bstpg 의 상세 검색처럼 조건을 한 판에 늘어놓고 고른다 (F2011).

시즌(아이콘) · 팀컬러(국가 · 소속 · 특성) · 국적 · 리그 · 클럽 · 포지션(FW · MF · DF · GK) · 오버롤 · 급여 · 키 · 몸무게 ·
특성(신규 · 일반 — 하나라도 가진 카드, OR) · 강화 등급 · 주발 · 약발 · 체형 · 개인기 · 명성.
고르는 동안은 칸만 바뀌고 '🔎 검색'을 눌러야 조건이 적용된다 — 데이터센터에 묻는 검색이라 칸 하나 바꿀 때마다 묻지 않게.
선수 찾기 · 스쿼드의 선수 배치 창이 같이 쓴다 (prefix 로 칸을 나눈다 — 'pl' · 'pp').
"""

from __future__ import annotations

from html import escape

import streamlit as st

import ui_parts
from data_prep.datacenter import NEW_TRAIT_ICONS, PHYSICAL, POSITION_FILTER, REPUTATION, trait_icon
from services.finder import club_label
from views.common import Ctx, browser_component
from views.widgets import grade_buttons, sorted_seasons

DETAIL_FIELDS = ("applied", "seasons", "traits", "tc_nation", "tc_club", "tc_rel", "nation", "league", "club", "history",
                 "pos_FW", "pos_MF", "pos_DF", "pos_GK", "ovr_lo", "ovr_hi", "pay_lo", "pay_hi", "h_lo", "h_hi", "w_lo", "w_hi",
                 "grade", "foot", "weak", "body", "skill", "rep")
POSITION_ROWS ={"FW": ("ST", "CF", "RW", "LW"), "MF": ("CAM", "LM", "RM", "CM", "CDM"), "DF": ("LB", "RB", "CB", "RWB", "LWB"),
                 "GK": ("GK",)}
TC_SELECTS = {"tc_nation": ("국가", ("국가",)), "tc_club": ("소속", ("클럽",)), "tc_rel": ("특성", ("관계", "시즌", "연도별", "스페셜"))}
FEET = ("오른발", "왼발", "양발")   # 양발 = 약발 5
RANGES = {"ovr": ("오버롤", 40, 200), "pay": ("급여", 1, 50), "h": ("키(cm)", 140, 220), "w": ("몸무게(kg)", 40, 130)}
KEEP = "session"   # 다른 화면에 다녀와도 고른 칸이 그대로 (Streamlit 은 화면에 없는 입력칸의 값을 지운다)
ICON_CSS = """
.fc-icons { display: flex; flex-wrap: wrap; gap: 5px; }
.fc-icons button { display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px 3px 4px; border-radius: 8px;
  border: 1px solid rgba(127,127,127,.35); background: rgba(127,127,127,.06); color: inherit; font: inherit; font-size: 12px;
  line-height: 1.2; cursor: pointer; transition: border-color .12s, background .12s; }
.fc-icons button:hover { border-color: #16e08a; }
.fc-icons button img { height: 22px; width: auto; display: block; }
.fc-icons button.on { border-color: #16e08a; background: rgba(22,224,138,.16); box-shadow: inset 0 0 0 1px #16e08a; font-weight: 700; }
.fc-icons.icon-only button { padding: 3px; }
.fc-icons.icon-only button img { height: 26px; }
"""
ICON_JS = """
export default function(component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector(".fc-icons");
  if (!root) { root = document.createElement("div"); parentElement.appendChild(root); }
  root.className = "fc-icons" + (data.iconOnly ? " icon-only" : "");
  const on = new Set(data.selected);
  // 이름 · 주소는 앱(파이썬)이 이스케이프했다
  root.innerHTML = data.items.map((it) =>
    `<button type="button" data-id="${it.id}" class="${on.has(it.id) ? "on" : ""}" title="${it.title}">` +
    (it.img ? `<img src="${it.img}" alt="">` : "") + (it.label ? `<span>${it.label}</span>` : "") + `</button>`).join("");
  root.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    b.classList.toggle("on");   // 누르자마자 보이게 — 고른 목록 전체를 앱에 보낸다 (빨리 여러 번 눌러도 마지막 목록이 맞다)
    const ids = [...root.querySelectorAll("button.on")].map((x) => x.dataset.id);
    setTriggerValue("pick", { ids, at: Date.now() });
  }));
}
"""


def _key(prefix: str, field: str) -> str:
    return f"{prefix}_f_{field}"


def _defaults(prefix: str) -> None:
    """처음 값 — 고르기 칸(국적 · 클럽 · 팀컬러 · 명성)과 숫자 칸은 넣지 않는다: 넣어 두면 '전체' 대신 빈 칸으로 보인다."""
    for field in DETAIL_FIELDS:
        if field in ("seasons", "traits", "body") or field.startswith("pos_"):
            st.session_state.setdefault(_key(prefix, field), [])
        elif field in ("history", "grade", "applied"):
            st.session_state.setdefault(_key(prefix, field), {"history": True, "grade": 1, "applied": None}[field])


def reset(prefix: str) -> None:
    """↺ 초기화 — 모든 칸을 처음으로 (적용한 조건도 지운다)."""
    for field in DETAIL_FIELDS:
        st.session_state.pop(_key(prefix, field), None)
    _defaults(prefix)
    st.session_state[_key(prefix, "rev")] = st.session_state.get(_key(prefix, "rev"), 0) + 1   # 아이콘 판을 새로 그린다


def preset(prefix: str, positions: tuple[str, ...]) -> None:
    """처음 열 때 고를 포지션 (선수 배치 창 — 그 자리의 포지션)."""
    _defaults(prefix)
    for group, codes in POSITION_ROWS.items():
        st.session_state[_key(prefix, f"pos_{group}")] = [c for c in codes if c in positions]


def _icon_picker(key: str, items: list[dict], selected: list[str], on_pick, icon_only: bool = False) -> None:
    """아이콘 버튼 묶음 — 누를 때마다 고른 목록 전체가 on_pick(ids) 로 온다. 런타임이 없으면(점검) 여러 개 고르기 칸."""
    component = browser_component("fclab_icons", css=ICON_CSS, js=ICON_JS)
    if component is None:
        labels = {it["id"]: it["title"] for it in items}
        picked = st.multiselect("고르기", list(labels), default=[s for s in selected if s in labels], key=f"{key}_ms",
                                format_func=lambda i: labels[i], label_visibility="collapsed")
        if picked != selected:
            on_pick(picked)
        return

    def changed() -> None:
        event = (st.session_state.get(key) or {}).get("pick") or {}
        on_pick([str(i) for i in event.get("ids", [])])

    component(key=key, data={"items": items, "selected": selected, "iconOnly": icon_only}, on_pick_change=changed)


def _row(label: str, small: str = ""):
    """왼쪽에 칸 이름, 오른쪽에 고르기 — bstpg 상세 검색의 줄."""
    head, body = st.columns([1.05, 7], vertical_alignment="center", gap="small")
    head.markdown(f'<div class="fc-flabel">{escape(label)}{f"<small>{escape(small)}</small>" if small else ""}</div>',
                  unsafe_allow_html=True)
    return body


def _range(prefix: str, field: str) -> None:
    """최소 ~ 최대 (비우면 끝까지)."""
    label, low, high = RANGES[field]
    with st.container(horizontal=True, gap="small", vertical_alignment="center", key=f"frange_{prefix}_{field}"):
        st.markdown(f'<div class="fc-flabel sub">{escape(label)}</div>', unsafe_allow_html=True, width="content")
        st.number_input(f"{label} 최소", min_value=low, max_value=high, value=None, step=1, key=_key(prefix, f"{field}_lo"),
                        placeholder="최소", label_visibility="collapsed", width=96, persist_state=KEEP)
        st.markdown('<div class="fc-tilde">~</div>', unsafe_allow_html=True, width="content")
        st.number_input(f"{label} 최대", min_value=low, max_value=high, value=None, step=1, key=_key(prefix, f"{field}_hi"),
                        placeholder="최대", label_visibility="collapsed", width=96, persist_state=KEEP)


def _season_items(ctx: Ctx) -> list[dict]:
    items = []
    for ids, label in sorted_seasons(ctx.catalog, len(ctx.catalog.cards)):
        icon = ui_parts.image(ctx.catalog.seasons.get(ids[0], {}).get("seasonImg"))
        items.append({"id": ",".join(map(str, ids)), "img": escape(icon), "title": escape(label),
                      "label": "" if icon else escape(label.split(" · ")[0])})
    return items


def _trait_items(names: list[str]) -> list[dict]:
    return [{"id": escape(n), "img": escape(ui_parts.image(trait_icon(n))), "title": escape(n), "label": escape(n)} for n in names]


def _set_list(key: str, ids: list[str]) -> None:
    st.session_state[key] = ids


def _values(prefix: str, options: dict) -> dict | None:
    """칸들 → Filters 에 넣을 조건 (고른 것이 없으면 None)."""
    get = lambda field: st.session_state.get(_key(prefix, field))   # noqa: E731
    seasons = tuple(sorted({int(s) for ids in get("seasons") or [] for s in ids.split(",")}))
    positions = tuple(c for group in POSITION_ROWS for c in get(f"pos_{group}") or [])
    colors = [(tc["id"], tc["name"]) for field in TC_SELECTS if (tc := get(field))]
    traits = tuple(t for t in options.get("traits", []) if t in (get("traits") or []))
    foot = get("foot")
    ranges = {}
    for field in RANGES:
        lo, hi = get(f"{field}_lo"), get(f"{field}_hi")
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        ranges[field] = (lo, hi)
    body = {"seasons": seasons, "positions": positions, "position_label": "·".join(positions),
            "teamcolor": colors[0] if colors else None, "teamcolor_also": tuple(colors[1:]),
            "nation": get("nation"), "league": get("league"), "club": get("club"), "club_history": bool(get("history")),
            "traits": traits if len(traits) == 1 else (), "traits_any": traits if len(traits) > 1 else (),
            "foot": foot if foot in ("오른발", "왼발") else None, "weak_foot": 5 if foot == "양발" else get("weak"),
            "skill_moves": get("skill"), "reputation": get("rep"), "physical": tuple(get("body") or ()),
            "ovr_min": ranges["ovr"][0], "ovr_max": ranges["ovr"][1], "pay_min": ranges["pay"][0], "pay_max": ranges["pay"][1],
            "height": None if ranges["h"] == (None, None) else (ranges["h"][0] or RANGES["h"][1], ranges["h"][1] or RANGES["h"][2]),
            "weight": None if ranges["w"] == (None, None) else (ranges["w"][0] or RANGES["w"][1], ranges["w"][1] or RANGES["w"][2])}
    chosen = any(v not in (None, (), "", False) for k, v in body.items() if k not in ("club_history", "position_label"))
    grade = get("grade") or 1
    return {**body, "grade": grade} if chosen or grade > 1 else None


def _apply(prefix: str, options: dict) -> None:
    st.session_state[_key(prefix, "applied")] = _values(prefix, options)


def detail_panel(ctx: Ctx, prefix: str, expanded: bool = False, title: str = "🔎 상세 검색") -> dict | None:
    """상세 검색 판 — '🔎 검색'으로 적용한 조건(Filters 에 넣을 dict, 고른 것이 없으면 None)을 돌려준다."""
    _defaults(prefix)
    applied = st.session_state.get(_key(prefix, "applied"))
    if not ctx.finder.enabled:
        st.caption("데이터센터 기능이 꺼져 있어 상세 검색을 쓸 수 없습니다.")
        return None
    with st.expander(title + (f" — 조건 {len(_chips(applied, {}))}개 적용 중" if applied else ""), expanded=expanded or bool(applied)):
        try:
            options = ctx.finder.options()
        except Exception as exc:  # noqa: BLE001
            st.caption(f"검색 목록을 불러오지 못했습니다: {exc}")
            return applied
        rev = st.session_state.get(_key(prefix, "rev"), 0)
        with st.container(key=f"fpanel_{prefix}"):
            with _row("시즌", "여러 개"):
                _icon_picker(f"{prefix}_ficon_season_{rev}", _season_items(ctx), st.session_state[_key(prefix, "seasons")],
                             lambda ids: _set_list(_key(prefix, "seasons"), ids), icon_only=True)
            teamcolors = ctx.teamcolors.all()
            with _row("팀컬러"):
                cols = st.columns(3, gap="small")
                for col, (field, (label, kinds)) in zip(cols, TC_SELECTS.items()):
                    choices = sorted((tc for tc in teamcolors if tc["kind"] in kinds), key=lambda tc: tc["name"])
                    if st.session_state.get(_key(prefix, field)) not in [None] + choices:
                        st.session_state[_key(prefix, field)] = None
                    col.selectbox(f"{label} 팀컬러", [None] + choices, key=_key(prefix, field), label_visibility="collapsed",
                                  persist_state=KEEP, placeholder=f"{label} 팀컬러 — 전체 (쳐서 찾기)",
                                  format_func=lambda tc, label=label: f"{label} 팀컬러 — 전체" if tc is None else (
                                      club_label(tc["name"]) + ("" if tc["kind"] in ("국가", "클럽") else f" · {tc['kind']}")))
            with _row("국적 · 클럽"):
                n_col, l_col, c_col, h_col = st.columns([1, 1, 1.25, 0.8], vertical_alignment="center", gap="small")
                n_col.selectbox("국적", [None] + sorted(options["nations"]), key=_key(prefix, "nation"), persist_state=KEEP,
                                label_visibility="collapsed", format_func=lambda v: "국적 — 전체" if v is None else v,
                                placeholder="국적 — 전체")
                league = l_col.selectbox("리그", [None] + list(options["leagues"]), key=_key(prefix, "league"),
                                         persist_state=KEEP, label_visibility="collapsed", placeholder="리그 — 전체",
                                         format_func=lambda v: "리그 — 전체" if v is None else v)
                league_id = options["leagues"].get(league) if league else None
                clubs = sorted(n for n, i in options["teams"].items()
                               if not league_id or options["team_league"].get(str(i), options["team_league"].get(i)) == league_id)
                if st.session_state.get(_key(prefix, "club")) not in [None] + clubs:
                    st.session_state[_key(prefix, "club")] = None
                c_col.selectbox("클럽", [None] + clubs, key=_key(prefix, "club"), label_visibility="collapsed", persist_state=KEEP,
                                placeholder="클럽 — 전체", help="실제 이름 · 줄임말로도 찾습니다 (인테르 → 롬바르디아 FC, 맨유 …)",
                                format_func=lambda v: "클럽 — 전체" if v is None else club_label(v))
                h_col.checkbox("클럽 경력 포함", key=_key(prefix, "history"), persist_state=KEEP,
                               help="켜면 그 클럽에서 뛴 적이 있는 선수까지, 끄면 지금 그 클럽 소속인 카드만")
            with _row("포지션"):
                with st.container(horizontal=True, gap="medium", key=f"fpos_{prefix}"):
                    for group, codes in POSITION_ROWS.items():
                        st.pills(group, codes, selection_mode="multi", key=_key(prefix, f"pos_{group}"), persist_state=KEEP)
            with _row("오버롤 · 급여"):
                with st.container(horizontal=True, gap="large", key=f"franges1_{prefix}"):
                    _range(prefix, "ovr")
                    _range(prefix, "pay")
            with _row("키 · 몸무게"):
                with st.container(horizontal=True, gap="large", key=f"franges2_{prefix}"):
                    _range(prefix, "h")
                    _range(prefix, "w")
            with _row("특성", "하나라도 (OR)"):
                new = [t for t in options["traits"] if t in NEW_TRAIT_ICONS]
                normal = [t for t in options["traits"] if t not in NEW_TRAIT_ICONS]
                picked = st.session_state[_key(prefix, "traits")]
                new_tab, normal_tab = st.tabs([f"신규 특성 {len(new)}", f"일반 특성 {len(normal)}"])
                with new_tab:   # 탭마다 따로 고르지만 목록은 하나 — 다른 탭에서 고른 것은 그대로 둔다
                    _icon_picker(f"{prefix}_ficon_new_{rev}", _trait_items(new), [escape(t) for t in picked if t in new],
                                 lambda ids: _set_list(_key(prefix, "traits"),
                                                       [t for t in picked if t not in new] + [t for t in new if escape(t) in ids]))
                with normal_tab:
                    _icon_picker(f"{prefix}_ficon_normal_{rev}", _trait_items(normal),
                                 [escape(t) for t in picked if t in normal],
                                 lambda ids: _set_list(_key(prefix, "traits"),
                                                       [t for t in picked if t not in normal] + [t for t in normal if escape(t) in ids]))
            with _row("강화 등급", "OVR 범위 기준"):
                grade_buttons("", _key(prefix, "grade"))
            with _row("주발 · 약발"):
                with st.container(horizontal=True, gap="large", key=f"ffoot_{prefix}"):
                    st.pills("주발", FEET, key=_key(prefix, "foot"), label_visibility="collapsed", persist_state=KEEP,
                             help="양발 = 약발 5")
                    st.pills("약발", [1, 2, 3, 4, 5], key=_key(prefix, "weak"), label_visibility="collapsed", persist_state=KEEP,
                             format_func=lambda v: f"약발 {v}", disabled=st.session_state.get(_key(prefix, "foot")) == "양발")
            with _row("체형"):
                st.pills("체형", list(PHYSICAL), selection_mode="multi", key=_key(prefix, "body"), label_visibility="collapsed",
                         persist_state=KEEP, help="고유 = 선수만의 체형 모델 (펠레 · 메시 · 호나우두 …)")
            with _row("개인기 · 명성"):
                with st.container(horizontal=True, gap="large", vertical_alignment="center", key=f"fetc_{prefix}"):
                    st.pills("개인기", [1, 2, 3, 4, 5, 6], key=_key(prefix, "skill"), label_visibility="collapsed",
                             persist_state=KEEP, format_func=lambda v: "★" * v)
                    st.selectbox("명성", [None] + list(REPUTATION), key=_key(prefix, "rep"), label_visibility="collapsed",
                                 persist_state=KEEP, width=170, placeholder="명성 — 전체",
                                 format_func=lambda v: "명성 — 전체" if v is None else v)
            draft = _values(prefix, options)
            a_col, r_col, n_col = st.columns([1.3, 1, 3.2], vertical_alignment="center")
            a_col.button("🔎 검색", key=f"{prefix}_f_go", type="primary", width="stretch", on_click=_apply, args=(prefix, options))
            r_col.button("↺ 초기화", key=f"{prefix}_f_reset", width="stretch", on_click=reset, args=(prefix,))
            if draft != applied:
                n_col.caption("바꾼 조건은 '🔎 검색'을 눌러야 적용됩니다.")
            elif applied:
                n_col.caption("적용 중: " + " · ".join(_chips(applied, ctx.finder.season_names())))
    return st.session_state.get(_key(prefix, "applied"))


def _chips(applied: dict | None, seasons: dict[int, str]) -> list[str]:
    from services.finder import Filters

    return Filters(**applied).chips(seasons)[:-1] if applied else []


def positions_valid(codes: tuple[str, ...]) -> tuple[str, ...]:
    """데이터센터 포지션 검색값이 있는 것만 (POSITION_FILTER)."""
    return tuple(c for c in codes if c in POSITION_FILTER)
