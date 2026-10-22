"""👤 선수 — 카드 · AI 스카우팅 · 소개(길면 접기) · Q&A · AI 리포트 · 포지션 내 순위 · 랭커 포지션 사용 · 시즌별 카드.

빠르게 보이도록: 위에서부터 그릴 수 있는 것을 먼저 그리고, 네트워크가 필요한 칸(분석 보강 · 포지션 사용 · AI 한줄)은
자리만 잡아 두었다가 맨 마지막에 채운다. 사용자는 카드와 소개를 기다리지 않고 본다.
질문 답변 · AI 리포트 생성은 가장 느려서, 탭은 소개 바로 아래에 두되 생성은 그 칸들을 다 채운 뒤에 한다.
"""

from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

import ui_parts
from data_prep.enhance import skill_stars
from data_prep.prompts import TASK_LABELS
from services.ai import scout_line
from services.cardinfo import bonus_stats
from services.insights import percentiles, position_usage, trade_prices
from services.teamcolor import effect_bonus, enhance_details
from data_prep.tactics import OVR_GRID
from views.common import (Ctx, add_to_compare, back_button, card_basics, dialog_open, enrich, friendly_error, get_player,
                          log, render_sources, run_with_status, tile_grid, with_saves)
from views.prices import compare_prices, price_section
from views.widgets import card_picker, grade_buttons

SAMPLES = ["프로 데뷔는 언제, 어느 팀에서 했어?", "국가대표 경력을 알려 줘", "가장 큰 우승 경력은?", "어떤 플레이 스타일이야?"]
OTHER_PAGE = 12   # 다른 시즌 카드 한 번에 (2줄 × 6장)


def _ask(question: str) -> None:
    st.session_state.pending_question = question


def _turn_seasons(player_id: str, page: int) -> None:
    pages = st.session_state.setdefault("other_page", {})
    st.session_state.other_dir = "next" if page > pages.get(player_id, 0) else "prev"
    pages[player_id] = page


# ── 카드 정보 (FC 온라인 데이터센터) — 능력치 · 포지션별 OVR · 특성 · 클럽 경력 · 팀컬러 · 급여 ──────────

def _card_section(ctx: Ctx, player: dict) -> None:
    info = ctx.cards.get([player["spid"]]).get(player["spid"])
    st.markdown('<div class="fc-section">📋 카드 정보<small>능력치 · 포지션별 오버롤 · 특성 · 클럽 경력 · 팀컬러 — '
                'FC 온라인 데이터센터</small></div>', unsafe_allow_html=True)
    if info:
        _card_panel(ctx, player, info)
    elif ctx.cards.enabled:
        ctx.cards.want([player["spid"]])
        _card_waiting(ctx, player)
    else:
        st.caption("데이터센터 기능이 꺼져 있어 카드 정보를 볼 수 없습니다 (settings.toml [market] enabled).")


@st.fragment(run_every=2)
def _card_waiting(ctx: Ctx, player: dict) -> None:
    """받는 동안 2초마다 확인 — 다 받으면 화면 전체를 한 번 다시 그린다 (머리의 카드 OVR 까지)."""
    if ctx.cards.get([player["spid"]]) and not dialog_open():
        st.rerun()
    st.info("⏳ 데이터센터에서 이 카드의 능력치 · 포지션별 오버롤 · 특성 · 클럽 경력 · 팀컬러를 받는 중… (몇 초)")


def _reset_bonus(spid: int) -> None:
    """추가 능력치 선택을 처음으로 (+1강 · 적응도 1 · 팀컬러 없음)."""
    for key in [k for k in st.session_state if str(k).startswith("pc_") and f"_{spid}" in str(k)]:
        del st.session_state[key]


def _bonus_controls(ctx: Ctx, player: dict, info: dict) -> dict:
    """데이터센터 선수 정보 화면의 추가 능력치 선택 — 강화(버튼) · 적응도 · 강화 / 소속 / 특성 팀컬러 (소속은 단계까지) · 초기화.
    돌려주는 값: combo(데이터센터 계산 조합) · effects(고른 팀컬러들의 효과 문구 — 모르면 None) · applied(화면 문구)."""
    spid = player["spid"]
    teamcolors = {tc["id"]: tc for tc in ctx.teamcolors.all()}
    enhance_ids = [i for i, tc in teamcolors.items() if tc["kind"] == "강화"]
    own = (info.get("teamcolors") or {}).get("소속", [])
    features = (info.get("teamcolors") or {}).get("관계", [])
    # 강화 팀컬러는 단계('N강 이상')를 알아야 목록에 들어가므로 없으면 지금 받는다 (처음 한 번 · 몇 개뿐)
    details = ctx.teamcolors.details([t["id"] for t in own + features]) | enhance_details(ctx.teamcolors)
    ctx.teamcolors.want([t["id"] for t in own + features])   # 단계별 효과를 미리 — 고르자마자 바로 계산되게
    grade = grade_buttons("강화", f"pc_grade_{spid}")
    a_col, e_col, t_col, l_col, r_col, x_col = st.columns([0.8, 2.1, 2.1, 0.9, 2.1, 1], vertical_alignment="bottom")
    adapt = a_col.selectbox("적응도", list(range(1, 6)), key=f"pc_adapt_{spid}", format_func=lambda a: f"+{a}",
                            help="같은 팀에서 뛴 만큼 오르는 적응도 — 1단계는 +0, 단계마다 모든 능력치 +1 (5단계 = +4)")
    enhance_options = [(0, 0)] + [(i, lv["level"]) for i in enhance_ids
                                  for lv in (details.get(i) or {}).get("levels", [])
                                  if (details.get(i) or {}).get("min_grade") and details[i]["min_grade"] <= grade]

    def level_effects(teamcolor_id: int, level: int | None) -> list[str] | None:
        levels = (details.get(teamcolor_id) or {}).get("levels")
        if not levels:   # 한 단계짜리는 목록의 효과로
            tc = teamcolors.get(teamcolor_id) or {}
            return tc.get("effects") if tc.get("max_level") == 1 else None
        return next((lv["effects"] for lv in levels if level is None or lv["level"] == level), None)

    def enhance_label(option: tuple[int, int]) -> str:
        if not option[0]:
            return "강화 팀컬러 없음"
        lv = next(lv for lv in details[option[0]]["levels"] if lv["level"] == option[1])
        return f"{teamcolors[option[0]]['name']} Lv.{option[1]} ({lv['count']}명 · {', '.join(lv['effects'])})"

    conditions = sorted((details[i]["min_grade"], teamcolors[i]["name"]) for i in enhance_ids
                        if (details.get(i) or {}).get("min_grade"))
    enhance = e_col.selectbox("강화 팀컬러", enhance_options, key=f"pc_enh_{spid}_{grade}", format_func=enhance_label,
                              help="강화 등급 조건을 채우는 팀컬러만 — " + " · ".join(f"{n} {g}강 이상" for g, n in conditions))
    pick = t_col.selectbox("소속 팀컬러", [None] + own, key=f"pc_tc_{spid}",
                           format_func=lambda t: "소속 팀컬러 없음" if t is None else t["name"])
    level = 0
    if pick:
        top = teamcolors.get(pick["id"], {}).get("max_level") or 1
        level = l_col.selectbox("단계", list(range(1, top + 1)), index=top - 1, key=f"pc_tclv_{spid}_{pick['id']}",
                                format_func=lambda lv: f"Lv.{lv}")
    else:
        l_col.selectbox("단계", ["-"], key=f"pc_tclv_{spid}_none", disabled=True)
    relation = r_col.selectbox("특성 팀컬러", [None] + features, key=f"pc_rel_{spid}",
                               format_func=lambda t: "특성 팀컬러 없음" if t is None else t["name"],
                               help="연도별 · 듀오 같은 팀컬러 (데이터센터 선수 정보의 '관계 팀컬러')")
    x_col.button("↺ 초기화", key=f"pc_reset_{spid}", on_click=_reset_bonus, args=(spid,), width="stretch",
                 help="+1강 · 적응도 1 · 팀컬러 없음으로 되돌린다")
    if conditions:   # 지금 등급으로 고를 수 있는 강화 팀컬러는 굵게 — 백금빛 물결이 왜 안 보이는지(11강 미만) 바로 알 수 있게
        st.caption("강화 팀컬러 조건: " + " · ".join(f"**{n} {g}강+**" if g <= grade else f"{n} {g}강+" for g, n in conditions)
                   + f" — 지금 +{grade}강은 굵은 것만 고를 수 있습니다. 데이터센터에서 보이는 은빛 Lv.2 +7 · 금빛 Lv.2 +8 · "
                   "백금빛 Lv.2 +9 는 팀컬러(+3 · +4 · +5)에 적응도 5(+4)를 더한 값입니다 — 적응도 +5 를 함께 고르면 같아집니다.")
    effects = ([level_effects(*enhance)] if enhance[0] else []) + ([level_effects(pick["id"], level)] if pick else []) \
        + ([level_effects(relation["id"], None)] if relation else [])
    extras = ([f"적응도 +{adapt}"] if adapt > 1 else []) + ([enhance_label(enhance).split(" (")[0]] if enhance[0] else []) \
        + ([f"{pick['name']} Lv.{level}"] if pick else []) + ([relation["name"]] if relation else [])
    combo = (grade, adapt, (pick["id"], level) if pick else (0, 0), enhance, relation["id"] if relation else 0)
    return {"grade": grade, "combo": combo, "effects": effects, "extras": extras}


def _stars_html(base: int | None, grade: int) -> str:
    """개인기 별 — 카드 기본 별 + 강화로 더해진 별(초록, 5 · 8 · 11강에서 하나씩 · 최대 6개)."""
    if not base:
        return '<b class="txt">-</b>'
    have, added = skill_stars(base, grade)
    plus = f'<span class="star-up" title="강화로 더해진 개인기 (5 · 8 · 11강)">{"★" * added}</span>' if added else ""
    note = f" <small>{have}+{added} · 5·8·11강마다 +1</small>" if added else " <small>5·8·11강마다 +1 (최대 6)</small>"
    return f'<b class="txt">{"★" * have}{plus}</b>{note}'


@st.fragment
def _card_panel(ctx: Ctx, player: dict, info: dict) -> None:
    pick = _bonus_controls(ctx, player, info)
    spid, grade = player["spid"], pick["grade"]
    plain = not pick["extras"]
    # 강화 · 적응도 · 팀컬러는 바로 계산한다 (데이터센터와 같은 규칙). 특정 능력치만 오르는 팀컬러는 포지션별 OVR 이 포지션마다
    # 달라, 정확한 값은 데이터센터 계산을 뒤에서 받아 바꿔 끼운다 — 그동안은 ≈ 표시.
    adapt = pick["combo"][1]
    shown = bonus_stats(info, grade, adapt, [e for e in pick["effects"] if e])
    exact = shown["exact"] and all(pick["effects"])
    if not plain and not exact:
        boosted = ctx.cards.exact(spid, pick["combo"])
        if boosted:
            shown, exact = {**boosted, "exact": True}, True
    # 적응도는 숫자에 바로 들어간다 — 초록 +N 의 기준은 강화 · 적응도까지, +N 은 그 위에 팀컬러(강화 · 소속(특정 능력치만
    # 오르는 것도 그 능력치에) · 특성)로 더 오른 만큼. 데이터센터는 강화를 바꾸면 적응도 5 와 그 등급의 가장 높은 강화 팀컬러를
    # 함께 골라(n1Change=1) 은빛 Lv.2 +7 · 금빛 Lv.2 +8 · 백금빛 Lv.2 +9 로 보이는데, 이것은 팀컬러 +3/+4/+5 에 적응도 +4 를
    # 더한 값이다 (2026-10-22 데이터센터 계산으로 확인).
    base = bonus_stats(info, grade, adapt, [])
    focus: dict[str, int] = {}   # 팀컬러가 '전체 능력치' 말고 따로 올려 주는 능력치 (예: 바르셀로나 Lv.4 짧은 패스 +3 · 볼 컨트롤 +3)
    for effect in pick["effects"]:
        for name, value in effect_bonus(effect or [])[1].items():
            focus[name] = focus.get(name, 0) + value
    note = f"+{grade}강" + (f" · {' · '.join(pick['extras'])}" if pick["extras"] else "")
    mark = "" if exact else " ≈"
    stats = ctx.db.card_stats([spid]).get(spid) or {}
    pay_note = ""
    if stats.get("pay_change"):
        change = stats["pay_change"]
        pay_note = f" <small>전 {change['before']} · {change['at'][:10]} 로스터 패치</small>"
    foot = info.get("foot_lr") or {}
    foot_text = f"L{foot['L']} – R{foot['R']}" if foot else escape(info.get("foot", "") or "-")
    main_foot = {"L": "왼발", "R": "오른발"}.get(foot.get("main"), "")
    grid = shown.get("position_ovr") or {}
    best = max(grid.values(), default=shown.get("ovr") or 0)
    up = (shown.get("ovr") or 0) - base["ovr"]
    cells = [
        ("OVR", f'<b>{shown.get("ovr") or "-"}{mark}</b> <small>{escape(info.get("position", ""))}'
                f'{f" · 추가 +{up}" if up > 0 else ""}</small>'),
        ("최고 포지션 OVR", f"<b>{best}{mark}</b>"),
        ("급여", f'<b>{info.get("pay") or "-"}</b>{pay_note}'),
        ("주발", f'<b class="txt">{foot_text}</b> <small>{main_foot}</small>'),
        ("개인기", _stars_html(info.get("skill_moves"), grade)),
        ("키 · 몸무게", f'<b class="txt">{escape(info.get("height", ""))} · {escape(info.get("weight", ""))}</b> '
                      f'<small>{escape(info.get("body", ""))}</small>'),
        ("국적 · 명성", f'<b class="txt">{escape(info.get("nation", "") or "-")}</b> <small>{escape(info.get("reputation", ""))}</small>'),
    ]
    st.markdown('<div class="fc-sum">' + "".join(f"<div><span>{k}</span>{v}</div>" for k, v in cells) + "</div>",
                unsafe_allow_html=True)
    if not exact and not plain:
        # 받는 중일 때만 지켜본다 — 데이터센터가 꺼졌거나 받기에 실패하면 기다릴 것이 없는데, 그때도 지켜보면
        # '안 왔다 → 다시 그리기'를 끝없이 되풀이했다 (화면이 계속 깜빡이고 멈추지 않음)
        if ctx.cards.exact_waiting(spid, pick["combo"]):
            st.caption("≈ 특정 능력치만 올리는 팀컬러가 포지션별 OVR 에 미치는 정도를 데이터센터에서 받는 중 — 받는 대로 정확한 값으로 바뀝니다.")
            _exact_watch(ctx, spid, pick["combo"])
        else:
            st.caption("≈ 특정 능력치만 올리는 팀컬러가 포지션별 OVR 에 미치는 정도는 추정값입니다 (데이터센터 계산을 받지 못함). "
                       "능력치 숫자는 정확합니다.")
    icons = {tc["id"]: tc.get("icon", "") for tc in ctx.teamcolors.all()}
    colors = [(group, t["name"], icons.get(t["id"], "")) for group in ("소속", "관계")
              for t in (info.get("teamcolors") or {}).get(group, [])]
    left, right = st.columns(2, gap="medium")   # 아래 랭커 기록 칸과 같은 나눔 — 가장자리가 맞게
    left.markdown(
        '<div class="fc-stack">'
        f'<div class="fc-panel"><div class="title">포지션별 오버롤{mark}<small>{escape(note)} · 스쿼드에서 그 자리에 놓으면 이 값'
        f'</small></div>{ui_parts.ovr_grid(grid, OVR_GRID, 0, base["position_ovr"])}</div>'
        f'<div class="fc-panel"><div class="title">특성<small>신규 특성은 노란 테두리</small></div>'
        f'{ui_parts.trait_chips(info.get("traits") or [])}</div>'
        f'<div class="fc-panel"><div class="title">팀컬러<small>이 카드가 들어가는 팀컬러 — 클럽은 클럽 경력 기준</small></div>'
        f'{ui_parts.teamcolor_list(colors)}</div>'
        f'<div class="fc-panel"><div class="title">클럽 경력</div>{ui_parts.club_history(info.get("clubs") or [])}</div>'
        "</div>", unsafe_allow_html=True)
    right.markdown(
        f'<div class="fc-stack"><div class="fc-panel"><div class="title">능력치<small>{escape(note)} · 적응도는 숫자에 바로 · '
        f'초록 +N 은 팀컬러(강화 · 소속 · 특성)로 더 오른 만큼'
        f'{" · 초록 줄은 팀컬러가 따로 올려 주는 능력치" if focus else ""}</small></div>'
        f'{ui_parts.ability_table(shown.get("stats") or {}, shown.get("summary") or {}, 0, base["stats"], focus)}'
        "</div></div>", unsafe_allow_html=True)


@st.fragment(run_every=1.5)
def _exact_watch(ctx: Ctx, spid: int, combo: tuple) -> None:
    """데이터센터 계산이 오면 화면을 한 번 다시 그린다 (모달이 열려 있으면 기다린다)."""
    if not ctx.cards.exact_waiting(spid, combo) and not dialog_open():
        st.rerun()


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


def _open_compare(player_id: str) -> None:
    st.session_state.cmp_dialog = player_id


def _close_compare() -> None:
    st.session_state.cmp_dialog = None


@st.dialog("⚖️ 바로 비교", width="large", on_dismiss=_close_compare)
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
        _close_compare()
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
    basics = card_basics(ctx, [player["spid"]]).get(player["spid"], {})
    card_col.markdown(ui_parts.player_card(player, basics.get("ovr"), basics.get("position")), unsafe_allow_html=True)
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
        intro_ready = bool(player.get("intro"))   # 있으면 바로 — 맨 마지막에 채우면 다시 그릴 때마다 '더 보기'가 접힌다
        if intro_ready:
            intro_slot.markdown(ui_parts.intro_card(player), unsafe_allow_html=True)
        favorite = player["player_id"] in ctx.favorites.ids()
        b1, b2, b3 = st.columns(3)
        b1.button("★ 즐겨찾기 해제" if favorite else "☆ 즐겨찾기", key="fav_toggle", width="stretch",
                  on_click=ctx.favorites.toggle, args=(player["player_id"],))
        b2.button("⚖️ 비교하기", key="to_compare", width="stretch", type="primary", on_click=_open_compare,
                  args=(player["player_id"],))
        if st.session_state.get("cmp_dialog") == player["player_id"]:   # 닫을 때까지 — 카드 상세가 도착해 다시 그려져도
            compare_dialog(ctx, player, others)
        if player.get("wiki_url"):
            b3.link_button("📖 위키백과 원문", player["wiki_url"], width="stretch")
        else:
            b3.button("📖 위키 문서 없음", disabled=True, width="stretch", key="no_wiki")

    # ── 탭: Q&A · AI 리포트 · 근거 문서 (비교는 위의 '⚖️ 비교하기' 모달) ─────
    # 자리와 입력만 여기에. 답변 · 리포트 생성과 근거 문서 목록은 맨 마지막에 채운다.
    with st.container(key="qa_zone"):   # 눈에 띄게 — 초록 테두리 상자 (ui_parts.css 의 .st-key-qa_zone)
        st.markdown(f'<div class="fc-qa-head">🤖 AI에게 {escape(player["name"])} 물어보기<span class="tag">근거 기반 RAG</span>'
                    '<small>위키백과 · 넥슨 데이터에 있는 것만, 문장마다 근거 번호와 함께 답합니다</small></div>',
                    unsafe_allow_html=True)
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
            answer_slot = st.chat_message("assistant")

    with intro_tab:
        report_key = f"intro:{player['player_id']}"
        saved = st.session_state.results.get(report_key)
        st.caption("위키백과 선수 문서와 넥슨 랭커 기록만으로 소개를 만듭니다. 문서에 없는 내용은 쓰지 않습니다.")
        make_report = st.button("다시 만들기" if saved else "✨ AI 선수 리포트 만들기", key="make_intro",
                                type="secondary" if saved else "primary")
        report_slot = st.container()

    _card_section(ctx, player)

    # ── 스탯 · 포지션 사용 ─────────────────────────────────────────
    meter_col, usage_col = st.columns(2, gap="medium")
    meter_slot, usage_slot = meter_col.empty(), usage_col.empty()
    if not player.get("lite"):
        meter_slot.markdown(_meters(ctx, player), unsafe_allow_html=True)
    else:
        meter_slot.info("랭커 기록을 불러오는 중...")
    usage_slot.caption("랭커 포지션 사용을 불러오는 중...")
    price_slot = st.empty()

    # ── 시즌별 다른 카드 — 12장씩, '더보기'로 옆으로 넘긴다 ───────────────────
    if others:
        pages = -(-len(others) // OTHER_PAGE)
        page = min(st.session_state.setdefault("other_page", {}).get(player["player_id"], 0), pages - 1)
        st.markdown(f'<div class="fc-section">🗂️ {escape(player["name"])}의 다른 시즌 카드<small>{len(others)}장 · 최신 시즌부터'
                    f'{f" · {page + 1}/{pages}" if pages > 1 else ""}</small></div>', unsafe_allow_html=True)
        shown = [get_player(ctx, str(s)) for s in others[page * OTHER_PAGE : (page + 1) * OTHER_PAGE]]
        ctx.prefetcher.schedule([p["spid"] for p in shown if p.get("lite")])   # 누르기 전에 미리 분석
        # 넘길 때마다 새 칸이라 CSS 애니메이션이 다시 돈다 (다음은 오른쪽에서, 이전은 왼쪽에서 — ui_parts.css 의 st-key-slide_*)
        with st.container(key=f"slide_{st.session_state.get('other_dir', 'none')}_{page}"):
            tile_grid(ctx, shown, key="other", cols=6)
        if pages > 1:
            prev_col, dots_col, next_col = st.columns([1, 2, 1], vertical_alignment="center")
            prev_col.button("◀ 이전", key="other_prev", disabled=page == 0, width="stretch",
                            on_click=_turn_seasons, args=(player["player_id"], page - 1))
            dots_col.markdown('<div class="fc-dots">' + "".join("●" if i == page else "○" for i in range(pages)) + "</div>",
                              unsafe_allow_html=True)
            left = len(others) - (page + 1) * OTHER_PAGE
            next_col.button(f"더보기 ▶ ({left}장)" if left > 0 else "더보기 ▶", key="other_next", disabled=page >= pages - 1,
                            width="stretch", on_click=_turn_seasons, args=(player["player_id"], page + 1))

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

    if not intro_ready:   # 분석 안 된 카드는 보강한 뒤에야 소개가 생긴다
        if player.get("intro"):
            intro_slot.markdown(ui_parts.intro_card(player), unsafe_allow_html=True)
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

    # ── 그다음: 질문 답변 · AI 리포트 생성 (가장 느리다 — 화면을 다 그린 뒤 위의 탭 자리에) ──
    if question:
        with answer_slot:
            reply = run_with_status(ctx, "qa", [player], question)
            if reply:
                st.markdown(reply["answer"])
                render_sources(reply["answer"], reply["evidence"])
        if reply:
            history.append({"role": "assistant", "content": reply["answer"], "evidence": reply["evidence"]})

    with report_slot:
        if make_report:
            reply = run_with_status(ctx, "intro", [player], "")
            if reply:
                st.session_state.results[report_key] = saved = reply
        if saved:
            st.markdown(saved["answer"])
            render_sources(saved["answer"], saved["evidence"])

    with docs_tab:   # 방금 질문 · 리포트로 모은 문서까지
        docs = ctx.collector.stored_docs(player["player_id"])
        if not docs:
            st.caption("아직 수집한 문서가 없습니다. 질문하거나 리포트를 만들면 위키백과에서 모읍니다.")
        for d in docs:
            tag = "선수 문서" if d.get("is_self") else f"검색어: {d.get('search_query', '')}"
            st.markdown(f"- [{d['title']}]({d['url']}) · {tag} · {len(d['text']):,}자 · 수집 {d.get('collected_at', '')}")
