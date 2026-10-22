"""🧠 스쿼드 · 전술 — 스쿼드 메이커 + AI 추천 스쿼드 + AI 전술 추천. (F2010)

스쿼드 메이커 (공식 https://fconline.nexon.com/squadmaker 를 참고):
- 포메이션은 공식 33개(4백 · 3백 · 5백)와 같은 배치. 경기장에서 선수 얼굴을 끌어 다른 자리에 놓으면 그 자리의 포지션으로 바뀌고,
  포지션이 바뀌면 포메이션도 다시 찾는다 (맞는 포메이션이 없으면 '사용자 지정'). 선수가 있는 자리에 놓으면 둘이 자리를 바꾼다.
- 자리마다 그 포지션에서의 OVR (카드의 포지션별 오버롤 16칸 + 강화) — 경기장 · 목록 · 평균 OVR · AI 추천 · AI 전술에 모두 같은 값.
  카드 상세를 아직 못 받은 카드는 선수 검색의 포지션 목록, 그것도 없으면 대표 OVR 로 두고 * 를 붙인다 (추정).
- 총 급여 / 310 (넘으면 '초과') · 최대 · 최소 급여 선수. 급여는 데이터센터에서 받은 최신 값 — 로스터 패치로 바뀌면
  바뀐 값과 전 값을 함께 보여 준다 (FcDb.card_pay_history).
- 팀컬러: 소속 · 강화 · 특성마다 적용되는 팀컬러(단계 · 효과 · 다음 단계까지 남은 인원) — services/teamcolor.py.
- 💾 저장 · 불러오기: 짠 스쿼드(포메이션 · 자리 · 강화)를 이름을 붙여 SQLite 에 둔다.
'🤖 AI 추천 스쿼드'는 모드(공식 · 친선 / 감독 모드) · 전술 · 꼭 넣을 선수 · 팀컬러 · 예산으로 11명을 고른다 (services/squad.py).
전술 항목 · 선택지 · 포메이션은 data_prep/tactics.py 에만 있다 (게임 업데이트로 바뀌면 거기만 고친다).
"""

from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

import ui_parts
from data_prep.datacenter import NEW_TRAIT_ICONS, TRAIT_ICON
from data_prep.enhance import GRADE_OVR
from data_prep.market import bp
from data_prep.nexon_api import ACTION_IMAGE
from data_prep.players import GROUP_OF, is_live_season, season_short, stat_values
from data_prep.tactics import (BASE_POSITION, FORMATION_GROUPS, FORMATIONS, LINE, PLAY_STYLES, TEAM_TACTICS, formation_of,
                               position_at, slot_xy)
from services.ai import recommend_tactics
from services.cardinfo import bonus_stats
from services.insights import latest_squad
from services.squad import MODE_NAMES, PAY_CAP, SquadAdvisor, explain
from services.teamcolor import GROUPS, effect_bonus, enhance_details, squad_teamcolors
from views.common import (Ctx, by_ovr, crests_of, dialog_open, enrich_many, friendly_error, get_player, go, log,
                          render_sources)
from views.pitch import dropped, pitch, pitch_slots, slot_ovrs
from views.widgets import card_picker, card_tiles, grade_buttons

ATTACK = {"스트라이커", "윙어", "공격형 미드필더"}
DEFENCE = {"센터백", "풀백", "수비형 미드필더"}
CUSTOM = "사용자 지정"
GROUP_OF_FORMATION = {name: group for group, names in FORMATION_GROUPS.items() for name in names}
TC_KINDS = ["없음", "클럽", "국가", "연도별", "시즌", "관계", "스페셜", "강화"]   # AI 추천 스쿼드의 팀컬러 종류


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


# ── 스쿼드 상태 ── tac_formation: 포메이션 이름 또는 '사용자 지정', tac_squad: {포지션: {pid, grade} 또는 None} ──

def _squad() -> dict[str, dict | None]:
    return st.session_state.tac_squad


def _codes() -> list[str]:
    """지금 자리들 — 포메이션 순서, 사용자 지정이면 골키퍼 → 수비 → 미드 → 공격, 같은 줄은 왼쪽부터."""
    formation = st.session_state.tac_formation
    if formation in FORMATIONS:
        return FORMATIONS[formation]
    return sorted(_squad(), key=lambda c: (LINE[c], slot_xy(c, None)[1]))


def list_order(codes: list[str], formation: str | None) -> list[str]:
    """자리별 선수 목록 순서 — FC 온라인 스쿼드 화면처럼 공격수부터: 앞줄(공격) → 미드 → 수비 → 골키퍼, 같은 줄은 왼쪽부터."""
    layout = formation if formation in FORMATIONS else None
    return sorted(codes, key=lambda c: (-LINE[c], slot_xy(c, layout)[1]))


def _set(formation: str | None, squad: dict[str, dict | None]) -> None:
    st.session_state.tac_formation = formation or CUSTOM
    st.session_state.tac_squad = squad
    st.session_state.tac_formation_pick = formation or CUSTOM


def _remap(codes: list[str]) -> dict[str, dict | None]:
    """새 포메이션 자리에 지금 선수들을 옮겨 앉힌다 — 같은 포지션은 그대로, 나머지는 같은 포지션 그룹 → 가까운 줄 · 가까운 쪽."""
    left = {c: v for c, v in _squad().items() if v}
    new = {c: left.pop(c, None) for c in codes}
    for code in codes:
        if new[code] is None and left:
            source = min(left, key=lambda c: (GROUP_OF.get(c) != GROUP_OF.get(code), abs(LINE[c] - LINE[code]),
                                              abs(slot_xy(c, None)[1] - slot_xy(code, None)[1])))
            new[code] = left.pop(source)
    return new


def _on_formation() -> None:
    picked = st.session_state.tac_formation_pick
    if picked in FORMATIONS and picked != st.session_state.tac_formation:
        _set(picked, _remap(FORMATIONS[picked]))
        st.session_state.tac_msg = None


def _on_drop() -> None:
    """경기장에서 끌어 놓았다 — 그 자리의 포지션으로 (선수가 있으면 둘이 자리를 바꾼다) → 포메이션을 다시 찾는다."""
    drop = dropped("tac_pitch")
    squad = _squad()
    if not drop or drop[0] not in squad or drop[0] == "GK":
        return
    source, top, left = drop
    target = position_at(top, left, set(squad) - {source})
    if target in ("GK", source):
        return
    before = st.session_state.tac_formation
    if target in squad:
        squad[source], squad[target] = squad[target], squad[source]
        message = f"🔁 {source} ↔ {target} — 두 선수의 자리를 바꿨습니다."
    else:
        squad[target] = squad.pop(source)
        message = f"↪️ {source} → {target} 로 옮겼습니다 (OVR 도 {target} 기준으로 바뀝니다)."
    formation = formation_of(list(squad), prefer=before)
    _set(formation, squad)
    if (formation or CUSTOM) != before:
        message += f" 포메이션이 {before} → **{formation or CUSTOM}** 로 바뀌었습니다."
    st.session_state.tac_msg = message


def _fill_power(ctx: Ctx) -> None:
    """자리마다 같은 포지션 그룹에서 랭커 기록이 가장 좋은 카드 (같은 선수는 한 번만)."""
    used: set[int] = set()
    squad = {}
    for code in _codes():
        pool = [p for p in ctx.players if p.get("position_group") == GROUP_OF.get(code) and p.get("ranker")
                and p["ranker"]["match_count"] >= 10 and p["pid"] not in used]
        best = max(pool, key=_score, default=None)
        squad[code] = {"pid": best["player_id"], "grade": None} if best else None
        if best:
            used.add(best["pid"])
    st.session_state.tac_squad = squad
    st.session_state.tac_msg = "⚡ 자리마다 랭커 기록이 가장 좋은 카드로 채웠습니다."


def fill_mine_and_go(ctx: Ctx) -> None:
    """🎮 내 기록의 '이 스쿼드로 전술 추천' — 내 스쿼드를 채우고 스쿼드 페이지로."""
    _ensure_state(ctx)
    _fill_mine(ctx)
    go("tactics")


def _fill_mine(ctx: Ctx) -> None:
    """연결한 계정의 최근 공식경기 선발 → 그 포지션 · 강화 그대로 (맞는 포메이션이 없으면 사용자 지정)."""
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
    mine = {p["position"]: {"pid": str(p["spid"]), "grade": p["grade"]} for p in squad["players"] if p["position"] in LINE}
    formation = formation_of(list(mine))
    _set(formation, mine)
    ctx.prefetcher.schedule([p["spid"] for p in squad["players"]])
    st.session_state.tac_msg = (f"🎮 {account['nickname']}의 최근 공식경기({squad['match_date']}, {squad['result']}) 선발을 "
                                f"불러왔습니다 → 포메이션 {formation or CUSTOM}")


def _clear() -> None:
    st.session_state.tac_squad = {code: None for code in _codes()}
    st.session_state.tac_msg = None


def _apply(code: str, pick_key: str) -> None:
    spid = st.session_state.get(f"{pick_key}_card")
    if spid:
        _squad()[code] = {"pid": str(spid), "grade": st.session_state.get(f"{pick_key}_grade") or 1}


NO_TRAIT, OWN_IMAGE = "없음", 0   # 고르기 칸의 '없음' · '기본 이미지' (None 이면 칸이 비어 보인다)


def _set_field(code: str, field: str, key: str) -> None:
    """자리 선수의 강화(grade) · 보유 신규 특성(trait) · 선수 이미지(img = 그 이미지를 쓰는 카드 spid)를 바꾼다."""
    if _squad().get(code):
        value = st.session_state[key]
        _squad()[code][field] = None if value in (NO_TRAIT, OWN_IMAGE) else value


def _reset_slot(code: str) -> None:
    """이 자리 선수의 강화 · 보유 신규 특성 · 선수 이미지를 처음으로 (선수는 그대로)."""
    if _squad().get(code):
        _squad()[code].update(grade=None, trait=None, img=None)


def _reset_all() -> None:
    """모든 선수의 강화 · 보유 신규 특성 · 선수 이미지를 처음으로 (선수 · 포메이션은 그대로)."""
    for code, entry in _squad().items():
        if entry:
            _reset_slot(code)
    st.session_state.tac_msg = "↺ 모든 선수의 강화 · 보유 신규 특성 · 선수 이미지를 처음으로 되돌렸습니다 (선수는 그대로)."


def _ensure_state(ctx: Ctx) -> None:
    if "tac_squad" not in st.session_state:   # 처음에는 빈 스쿼드 — 채우기는 '⚡ 파워 랭킹 추천' · '🎮 내 스쿼드' · 자리마다 '➕ 추가'로
        st.session_state.tac_formation = "4-2-3-1"
        st.session_state.tac_squad = {code: None for code in FORMATIONS["4-2-3-1"]}
    st.session_state.setdefault("tac_formation_pick", st.session_state.tac_formation)


# ── 저장 · 불러오기 ─────────────────────────────────────────────

def _save(ctx: Ctx) -> None:
    name = (st.session_state.get("tac_save_name") or "").strip() or f"스쿼드 {datetime.now():%m-%d %H:%M}"
    ctx.db.save_squad(name, {"formation": st.session_state.tac_formation, "squad": _squad(),
                             "style": st.session_state.get("tac_style")})
    st.session_state.tac_save_name = ""
    st.session_state.tac_msg = f"💾 '{name}' 이름으로 저장했습니다. '💾 저장 · 불러오기'에서 언제든 다시 불러올 수 있습니다."


def _load(ctx: Ctx, name: str) -> None:
    saved = next((s for s in ctx.db.squads() if s["name"] == name), None)
    if not saved:
        return
    data = saved["data"]
    formation = data.get("formation")
    _set(formation if formation in FORMATIONS else None, {code: entry for code, entry in data["squad"].items() if code in LINE})
    if data.get("style") in PLAY_STYLES:
        st.session_state.tac_style = data["style"]
    st.session_state.tac_msg = f"📂 '{name}' 스쿼드를 불러왔습니다 ({saved['saved_at'][:16].replace('T', ' ')} 저장)."


def _delete(ctx: Ctx, name: str) -> None:
    ctx.db.delete_squad(name)
    st.session_state.tac_msg = f"🗑️ '{name}' 스쿼드를 지웠습니다."


def _save_load(ctx: Ctx) -> None:
    saved = ctx.db.squads()
    st.text_input("이름", key="tac_save_name", placeholder="예) 토트넘 팀컬러 4-2-3-1", max_chars=40)
    st.button("💾 지금 스쿼드 저장", key="tac_save", type="primary", width="stretch", on_click=_save, args=(ctx,),
              help="같은 이름이면 덮어씁니다")
    st.markdown(f"**저장한 스쿼드** · {len(saved)}개")
    if not saved:
        st.caption("아직 저장한 스쿼드가 없습니다.")
    for i, item in enumerate(saved):
        filled = sum(1 for v in item["data"].get("squad", {}).values() if v)
        n_col, l_col, d_col = st.columns([3, 1.2, 0.7], vertical_alignment="center")
        n_col.markdown(f"**{escape(item['name'])}**  \n<small>{escape(item['data'].get('formation') or CUSTOM)} · {filled}명 · "
                       f"{item['saved_at'][:16].replace('T', ' ')}</small>", unsafe_allow_html=True)
        l_col.button("불러오기", key=f"tac_load_{i}", on_click=_load, args=(ctx, item["name"]), width="stretch")
        d_col.button("🗑", key=f"tac_del_{i}", on_click=_delete, args=(ctx, item["name"]), width="stretch",
                     help="이 스쿼드 지우기")


# ── 스쿼드 계산 — 자리 OVR · 급여 · 팀컬러 ──────────────────────────

def squad_view(ctx: Ctx, codes: list[str], players: dict[str, dict | None], grades: dict[str, int | None]) -> dict:
    """자리마다 {player, info, grade, ovr, exact, pay, pay_change, foot} + 평균 OVR · 총 급여 · 팀컬러 · 아직 받는 중인 것."""
    ovrs = slot_ovrs(ctx, codes, players, grades)   # 카드 상세가 없는 카드는 여기서 받기 시작한다
    spids = [int(p["player_id"]) for p in players.values() if p]
    infos = ctx.cards.get(spids)
    known = ctx.db.card_stats(spids)
    slots = {}
    for code in codes:
        p = players.get(code)
        if not p:
            continue
        spid = int(p["player_id"])
        info, row = infos.get(spid), known.get(spid) or {}
        pay = row.get("pay") if row.get("pay") is not None else (info or {}).get("pay")   # 선수 검색 값이 더 자주 새로 온다
        ovr, exact = ovrs.get(code, (None, False))
        entry = _squad().get(code) or {}
        # 선수 이미지 바꾸기는 LIVE 시즌 카드를 뺀 나머지 시즌만 (예전에 골라 둔 값이 있어도 LIVE 면 기본 이미지)
        swap = entry.get("img") and not is_live_season(p.get("season"))
        shown = {**p, "image": ACTION_IMAGE.format(spid=entry["img"])} if swap else p
        trait = entry.get("trait")
        slots[code] = {"player": shown, "info": info, "grade": grades.get(code) or 1, "ovr": ovr, "exact": exact, "pay": pay,
                       "pay_change": row.get("pay_change"), "foot": (info or {}).get("foot_lr") or row.get("foot"),
                       "trait": {"name": trait, "icon": TRAIT_ICON.format(no=NEW_TRAIT_ICONS[trait])}
                       if trait in NEW_TRAIT_ICONS else None,
                       "price": (ctx.market.table(spid).get(grades.get(code) or 1) or {}).get("current")
                       if ctx.market.enabled else None}
    no_price = [int(s["player"]["player_id"]) for s in slots.values() if s["price"] is None]
    if no_price and ctx.market.enabled:
        ctx.market.want(no_price, grades=())   # 현재가만 (선수 검색 한 번에 그 선수 카드의 강화 1~13 현재가)
    value = sum(s["price"] for s in slots.values() if s["price"])
    for s in slots.values():
        s["share"] = s["price"] / value * 100 if s["price"] and value else None
    team = team_of(ctx, slots)
    tc_ids = team["need_detail"]
    exact_jobs = _teamcolor_ovrs(ctx, slots, team, settled(ctx, team))
    pays = [s["pay"] for s in slots.values() if s["pay"] is not None]
    values = [s["ovr"] for s in slots.values() if s["ovr"]]
    loading_prices = sum(1 for s in no_price if ctx.market.loading_current(s)) if ctx.market.enabled else 0
    return {"slots": slots, "team": team, "pay": sum(pays), "pay_known": len(pays), "filled": len(slots),
            "avg": sum(values) / len(values) if values else None, "all_exact": all(s["exact"] for s in slots.values()),
            "value": value, "priced": sum(1 for s in slots.values() if s["price"]), "spids": spids, "tc_ids": tc_ids,
            "waiting": ctx.cards.pending(spids) + ctx.teamcolors.pending(tc_ids) + loading_prices + len(exact_jobs),
            "price_spids": [s for s in no_price if ctx.market.enabled], "exact_jobs": exact_jobs}


def team_of(ctx: Ctx, slots: dict) -> dict:
    """자리마다 {player, info, grade} → 스쿼드의 팀컬러 (services/teamcolor.squad_teamcolors). 단계를 모르는 팀컬러는 받기 시작한다."""
    members = [{"name": s["player"]["name"], "grade": s["grade"], "code": code,
                "teamcolors": (s["info"] or {}).get("teamcolors") if s["info"] else None} for code, s in slots.items()]
    used = {t["id"] for m in members for g in ("소속", "관계") for t in (m["teamcolors"] or {}).get(g, [])}
    # 강화 팀컬러(동빛 ~ 백금빛 물결)는 단계('N강 이상')를 모르면 계산에서 빠진다 — 없으면 지금 받는다 (처음 한 번)
    team = squad_teamcolors(members, ctx.teamcolors.all(), ctx.teamcolors.details(sorted(used)) | enhance_details(ctx.teamcolors))
    ctx.teamcolors.want(team["need_detail"])
    return team


def settled(ctx: Ctx, team: dict) -> bool:
    """팀컬러 인원 · 단계가 정해졌나 — 모든 선수의 팀컬러를 알고 단계를 받을 팀컬러가 없을 때.
    그 전에는 카드 상세가 한 장 올 때마다 단계가 바뀌어, 그때마다 정확한 OVR 을 새로 묻느라 카드 상세가 밀렸다."""
    return team["unknown"] == 0 and not ctx.teamcolors.pending(team["need_detail"])


def applied_html(team: dict) -> str:
    """지금 적용되는 팀컬러를 한 줄씩 — 소속 · 강화 · 특성마다 이름 · 단계 · 인원 · 효과 (AI 추천 스쿼드 결과 위)."""
    lines = []
    for group in GROUPS:
        e = next((e for e in team["groups"].get(group, []) if e["level"]), None)
        if e:
            icon = ui_parts.image(e["icon"])
            img = f'<img src="{escape(icon)}" alt="">' if icon else ""
            lines.append(f'<div><span class="g">{group}</span>{img}<b>{escape(e["name"])}</b> <span class="lv">{e["level"]}단계</span>'
                         f' <small>{e["count"]}명</small> <span class="eff">{escape(", ".join(e["effects"]))}</span></div>')
        else:
            lines.append(f'<div class="none"><span class="g">{group}</span>적용되는 팀컬러 없음</div>')
    note = f'<div class="none">⏳ {team["unknown"]}명의 팀컬러 정보를 받는 중 — 받는 대로 바뀝니다</div>' if team["unknown"] else ""
    return f'<div class="fc-tcline"><div class="t">🎨 적용 팀컬러</div>{"".join(lines)}{note}</div>'


def _teamcolor_ovrs(ctx: Ctx, slots: dict, team: dict, ask: bool = True) -> list[tuple[int, tuple]]:
    """적용되는 팀컬러(소속 · 강화 · 특성마다 하나)의 효과를 그 팀컬러에 든 선수의 자리 OVR 에 더한다 (공식 스쿼드 메이커처럼).

    '전체 능력치 +N' 은 OVR 에 그대로 +N. 특정 능력치만 오르는 효과는 포지션마다 OVR 에 미치는 정도가 달라, 데이터센터 계산을
    뒤에서 받아 그 자리 포지션의 값으로 바꾼다 (받기 전에는 * 표시). ask=False(팀컬러 인원이 아직 바뀌는 중)면 받아 둔 것만
    쓰고 새로 묻지 않는다. 돌려주는 값: 아직 받는 중인 (카드, 조합)."""
    applied = {group: next((e for e in team["groups"].get(group, []) if e["level"]), None) for group in GROUPS}
    waiting = []
    for code, s in slots.items():
        joined = {group: e for group, e in applied.items() if e and code in e["codes"]}
        if not joined:
            continue
        bonus = [effect_bonus(e["effects"]) for e in joined.values()]
        every, specific = sum(b[0] for b in bonus), any(b[1] for b in bonus)
        s["tc_bonus"] = {"every": every, "names": [e["name"] for e in joined.values()],
                         "applied": [(e["name"], e["level"], e["effects"]) for e in joined.values()]}
        if s.get("info"):   # 요약 능력치(스피드 · 슛 …)에도 — 특정 능력치 효과(볼 컨트롤 +3 등)는 그 능력치가 든 묶음에 나눠 들어간다
            plain = bonus_stats(s["info"], s["grade"], 1, [])["summary"]
            boosted = bonus_stats(s["info"], s["grade"], 1, [e["effects"] for e in joined.values()])["summary"]
            s["tc_summary"] = {k: boosted[k] - plain[k] for k in boosted if boosted[k] != plain.get(k)}
        if s["ovr"] is None:
            continue
        combo = (s["grade"], 1, (joined["소속"]["id"], joined["소속"]["level"]) if "소속" in joined else (0, 0),
                 (joined["강화"]["id"], joined["강화"]["level"]) if "강화" in joined else (0, 0),
                 joined["특성"]["id"] if "특성" in joined else 0)
        spid = int(s["player"]["player_id"])
        grid = ((ctx.cards.exact(spid, combo, ask=ask) if specific else None) or {}).get("position_ovr") or {}
        if BASE_POSITION.get(code, code) in grid:
            s["ovr"], s["exact"] = grid[BASE_POSITION.get(code, code)], True
        else:
            s["ovr"] += every
            s["exact"] = s["exact"] and not specific
            if specific and ctx.cards.exact_waiting(spid, combo):
                waiting.append((spid, combo))
    return waiting


def share_text(share: float | None) -> str:
    """구단 가치 중 비율 — 42% · 3.7% · <0.1%."""
    if not share:
        return "-"
    if share < 0.1:
        return "<0.1%"
    return f"{share:.0f}%" if share >= 10 else f"{share:.1f}%"


def _applied(view: dict, group: str) -> dict | None:
    return next((e for e in view["team"]["groups"].get(group, []) if e["level"]), None)


def top_html(view: dict) -> str:
    """스쿼드 위 — 팀컬러(적용되는 소속 팀컬러) · 구단가치(시세 합) · 주요 선수(시세가 가장 높은, 모르면 OVR 이 가장 높은)."""
    tc = _applied(view, "소속") or _applied(view, "특성")
    slots = list(view["slots"].values())
    key = max((s for s in slots if s["price"]), key=lambda s: s["price"], default=None) or \
        max((s for s in slots if s["ovr"]), key=lambda s: s["ovr"], default=None)
    missing = view["filled"] - view["priced"]
    team = f"{escape(tc['name'])} <small>{tc['level']}단계</small>" if tc else "없음"
    value = f"{ui_parts.bp_short(view['value'])} BP" if view["value"] else "-"
    if missing:
        value += f" <small>{missing}명 시세 받는 중</small>"
    star = escape(key["player"]["name"]) if key else "-"
    return (f'<div class="fc-sqtop"><div><span>팀컬러</span><b>{team}</b></div>'
            f'<div><span>구단가치</span><b>{value}</b></div><div><span>주요 선수</span><b>{star}</b></div></div>')


def chips_html(view: dict, formation: str) -> str:
    """경기장 아래 — 포메이션 · 소속 팀컬러 · 특성 팀컬러 · (강화 팀컬러) · 급여 N/310."""
    parts = [f'<span class="fm">{escape(formation)}</span>']
    for group, css in (("소속", "tc"), ("특성", "ft"), ("강화", "en")):
        applied = _applied(view, group)
        if applied:
            parts.append(f'<span class="{css}" title="{group} 팀컬러 {applied["level"]}단계">{escape(applied["name"])}</span>')
        elif group != "강화":
            parts.append(f'<span class="none">{group} 팀컬러 없음</span>')
    over = view["pay"] > PAY_CAP
    parts.append(f'<span class="pay{" over" if over else ""}">{view["pay"]}/{PAY_CAP}</span>')
    return f'<div class="fc-sqchips">{"".join(parts)}</div>'


def summary_html(view: dict, lead: str = "", avg_label: str = "평균 OVR (자리 · 강화 반영)") -> str:
    """평균 OVR · 총 급여(게이지) · 최대 · 최소 급여 선수 · 팀컬러(소속 · 강화 · 특성 적용 인원) · 급여가 바뀐 선수.
    lead 는 맨 앞에 더 넣을 칸 (AI 추천: 총 시세 / 예산)."""
    slots = view["slots"]
    avg = f"{view['avg']:.1f}{'' if view['all_exact'] else '*'}" if view["avg"] else "-"
    pay, cap = view["pay"], PAY_CAP
    over = pay > cap
    pay_note = "초과" if over else (f"남은 {cap - pay}" if cap - pay < 50 else f"{round(100 * pay / cap)}%")
    if view["pay_known"] < view["filled"]:
        pay_note += f" · {view['filled'] - view['pay_known']}명 급여 받는 중"
    paid = [(s["pay"], code, s["player"]["name"]) for code, s in slots.items() if s["pay"] is not None]
    cells = [lead] if lead else []
    cells += [f'<div><span>{escape(avg_label)}</span><b>{avg}</b> <small>{view["filled"]}/11명</small></div>',
             f'<div><span>총 급여</span><b style="color:{"#ff6b6b" if over else "inherit"}">{pay}</b> '
             f'<small>/ {cap} · {pay_note}</small><div class="fc-gauge{" over" if over else ""}">'
             f'<i style="width:{min(100, round(100 * pay / cap))}%"></i></div></div>']
    if paid:
        hi, lo = max(paid), min(paid)
        cells.append(f'<div><span>최대 · 최소 급여 선수</span><small>{escape(hi[1])} {escape(hi[2])} <b style="font-size:13px">'
                     f'{hi[0]}</b><br>{escape(lo[1])} {escape(lo[2])} <b style="font-size:13px">{lo[0]}</b></small></div>')
    groups = view["team"]["groups"]
    counts = []
    for group in GROUPS:
        best = next((e for e in groups.get(group, []) if e["level"]), None)
        counts.append(f"{group} <b style='font-size:15px'>{best['count'] if best else 0}</b>")
    cells.append(f'<div><span>팀컬러 적용 인원</span><small>{" · ".join(counts)}</small></div>')
    changed = [s for s in slots.values() if s.get("pay_change")]
    if changed:
        cells.append('<div><span>로스터 패치로 급여가 바뀐 선수</span><small>' + ", ".join(
            f"{escape(s['player']['name'])} {s['pay_change']['before']}→{s['pay']}" for s in changed[:4]) + "</small></div>")
    return f'<div class="fc-sum">{"".join(cells)}</div>'


def teamcolor_html(view: dict) -> str:
    """소속 · 강화 · 특성마다 적용되는 팀컬러 (공식 스쿼드 메이커처럼 하나씩 — 효과가 가장 큰 것) + 다른 후보."""
    groups, unknown = view["team"]["groups"], view["team"]["unknown"]
    cells = []
    for group in GROUPS:
        entries = groups.get(group, [])
        applied = next((e for e in entries if e["level"]), None)
        head = f'<div class="head">{group} 팀컬러<span class="cnt">{applied["count"] if applied else 0}</span></div>'
        if applied:
            icon = ui_parts.image(applied["icon"])
            img = f'<img src="{escape(icon)}" alt="">' if icon else ""
            nxt = (f" · 다음 {applied['next']['level']}단계까지 {applied['next']['count'] - applied['count']}명"
                   if applied.get("next") else " · 최고 단계")
            body = (f'<div class="best">{img}<div><b>{escape(applied["name"])}</b><span class="lv">{applied["level"]}단계</span>'
                    f'<small>{applied["count"]}명{nxt}</small></div></div>'
                    f'<div class="eff">{escape(", ".join(applied["effects"]))}</div>')
        else:
            near = next((e for e in entries if e.get("next")), None)
            hint = f" 가장 가까운 것: {escape(near['name'])} {near['count']}/{near['next']['count']}명" if near else ""
            body = f'<div class="none">적용된 팀컬러가 없습니다.{hint}</div>'
        others = [e for e in entries if e is not applied and e["count"] >= 2][:3]
        if others:
            body += '<div class="others">다른 후보: ' + ", ".join(
                f"{escape(e['name'])} {e['count']}명" + (f"({e['level']}단계)" if e["level"] else "") for e in others) + "</div>"
        cells.append(f'<div class="grp">{head}{body}</div>')
    note = f'<div class="fc-note">⏳ {unknown}명의 팀컬러 정보를 받는 중입니다.</div>' if unknown else ""
    return (f'<div class="fc-panel"><div class="title">🎨 팀컬러<small>소속(클럽 경력 · 국적 · 시즌) · 강화(강화 등급) · 특성(연도별 · 듀오 등) — '
            f'FC 온라인 데이터센터</small></div><div class="fc-tc">{"".join(cells)}</div>{note}</div>')


@st.fragment(run_every=2)
def _watch(ctx: Ctx, spids: list[int], tc_ids: list[int], price_spids: list[int], exact_jobs: list[tuple]) -> None:
    """카드 상세 · 팀컬러 단계 · 시세 · 팀컬러 OVR 을 받는 동안 2초마다 확인 — 다 받았거나 4건 넘게 새로 오면 한 번 다시 그린다."""
    left = (ctx.cards.pending(spids) + ctx.teamcolors.pending(tc_ids)
            + sum(1 for s in price_spids if ctx.market.loading_current(s))
            + sum(1 for spid, combo in exact_jobs if ctx.cards.exact_waiting(spid, combo)))
    if dialog_open():   # AI 추천 스쿼드 창을 쓰는 동안에는 화면을 다시 그리지 않는다
        return
    if left == 0 or st.session_state.get("tac_wait_shown", left) - left >= 4:
        st.rerun()
    st.caption(f"⏳ 데이터센터에서 카드 상세(포지션별 OVR · 특성 · 팀컬러) · 시세를 받는 중 — {left}건 남음. 받는 대로 바뀝니다.")


# ── AI 추천 스쿼드 ───────────────────────────────────────────────

def _teamcolor_pick(ctx: Ctx) -> dict | None:
    """팀컬러 고르기 — 종류 → 이름(쳐서 찾기) → 목표 단계. 강화 팀컬러는 'N강 이상' 인원으로 맞춘다."""
    kind = st.segmented_control("팀컬러", TC_KINDS, default="없음", key="ai_tc_kind",
                                help="클럽 팀컬러는 선수의 클럽 경력 기준 · 강화 팀컬러는 강화 등급 기준입니다")
    if not kind or kind == "없음":
        return None
    try:
        teamcolors = ctx.teamcolors.all(wait=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("팀컬러 목록 실패")
        st.caption(friendly_error(exc))
        return None
    options = sorted((tc for tc in teamcolors if tc["kind"] == kind), key=lambda tc: tc["name"])
    if not options:
        st.caption("이 종류의 팀컬러를 아직 받지 못했습니다. 잠시 뒤 다시 열어 주세요.")
        return None
    new = {tc["id"] for tc in options if tc.get("first_seen")}
    pick = st.selectbox(f"{kind} 팀컬러 (이름을 쳐서 찾기 · {len(options)}개)", options, index=None, key=f"ai_tc_{kind}",
                        format_func=lambda tc: f"{'🆕 ' if tc['id'] in new else ''}{tc['name']} · 최대 {tc['max_level']}단계"
                                               f" {tc['max_count']}명", placeholder="예) 토트넘 홋스퍼 · 대한민국 · 금빛 물결")
    if not pick:
        return None
    try:
        detail = ctx.teamcolors.detail_now(pick["id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("팀컬러 상세 실패")
        st.caption(friendly_error(exc))
        detail = None
    levels = (detail or {}).get("levels") or [{"level": pick["max_level"], "count": pick["max_count"],
                                               "effects": pick.get("effects", [])}]
    if detail and detail.get("description"):
        st.caption(detail["description"])
    level = st.radio("목표 단계", levels, index=len(levels) - 1, key=f"ai_tc_level_{pick['id']}", horizontal=True,
                     format_func=lambda lv: f"{lv['level']}단계 · {lv['count']}명 ({', '.join(lv['effects'])})")
    return {"id": pick["id"], "name": pick["name"], "kind": kind, "count": level["count"], "levels": levels,
            "min_grade": (detail or {}).get("min_grade") if kind == "강화" else None}


def _open_dialog() -> None:
    st.session_state.ai_dialog = True


def _close_dialog() -> None:
    st.session_state.ai_dialog = False


@st.dialog("🤖 AI 추천 스쿼드", width="large", on_dismiss=_close_dialog)
def squad_dialog(ctx: Ctx) -> None:
    """모드 · 포메이션 · 원하는 전술 · 꼭 넣을 선수 · 팀컬러 · 예산 → 11명 (코드가 예산 안에서 고르고 AI 가 설명)."""
    if ctx.datacenter is None:
        st.info("데이터센터 기능이 꺼져 있어 추천 스쿼드를 만들 수 없습니다.")
        return
    advisor = SquadAdvisor(ctx.datacenter, ctx.db)
    mode_label = st.segmented_control("모드", list(MODE_NAMES.values()), default=MODE_NAMES["official"], key="ai_mode",
                                      help="공식 · 친선은 랭커가 많이 쓰는(체감 좋은) 카드에, 감독 모드는 OVR 에 더 무게를 둡니다")
    mode = next(k for k, v in MODE_NAMES.items() if v == mode_label) if mode_label else "official"
    f_col, b_col = st.columns([1, 1])
    current = st.session_state.tac_formation if st.session_state.tac_formation in FORMATIONS else "4-2-3-1"
    formation = f_col.selectbox("포메이션", list(FORMATIONS), index=list(FORMATIONS).index(current), key="ai_formation",
                                format_func=lambda f: f"{GROUP_OF_FORMATION[f]} · {f}")
    budget = b_col.number_input("예산 (억 BP, 0.1~100,000)", min_value=0.1, max_value=100_000.0, value=50.0, step=10.0,
                                key="ai_budget", help="11명의 강화 등급별 현재가 합계 상한")
    style = st.pills("원하는 스타일", PLAY_STYLES, default=PLAY_STYLES[0], key="ai_style") or PLAY_STYLES[0]
    wish = st.text_input("원하는 전술 (선택)", key="ai_wish", placeholder="예) 빠른 윙어로 측면 돌파, 높은 압박")
    low = grade_buttons("가장 낮은 강화", "ai_grade_lo")
    st.session_state.setdefault("ai_grade_hi", max(5, low))
    high = grade_buttons("가장 높은 강화", "ai_grade_hi", options=range(low, 14))
    grades = (low, high)
    teamcolor = _teamcolor_pick(ctx)

    must = st.session_state.setdefault("ai_must", [])
    with st.expander(f"⭐ 꼭 넣을 선수 ({len(must)}/3)", expanded=bool(must)):
        for i, m in enumerate(list(must)):
            n_col, x_col = st.columns([4, 0.8], vertical_alignment="center")
            n_col.markdown(f"**{escape(m['name'])}** · {escape(m['season'])}")
            if x_col.button("빼기", key=f"ai_must_x_{m['spid']}", width="stretch"):
                must.pop(i)
                st.rerun(scope="fragment")
            st.session_state.setdefault(f"ai_must_g_{m['spid']}", m["grade"])
            m["grade"] = grade_buttons("", f"ai_must_g_{m['spid']}")
        if len(must) < 3:
            picked = card_picker(ctx, key="ai_must_pick")
            if picked and st.button("이 선수 꼭 넣기", key="ai_must_add", type="primary"):
                card = get_player(ctx, picked)
                if all(m["spid"] != card["spid"] for m in must):
                    must.append({"spid": card["spid"], "name": card["name"], "season": card["season_short"], "grade": grades[0]})
                st.rerun(scope="fragment")

    if st.button("🤖 이 조건으로 스쿼드 추천받기", key="ai_squad_go", type="primary", width="stretch"):
        request = {"formation": formation, "mode": mode, "grades": grades, "budget": int(budget * 10**8), "style": style,
                   "wish": wish.strip(), "teamcolor": teamcolor,
                   "must": [{"spid": m["spid"], "name": m["name"], "grade": m["grade"]} for m in must]}
        try:
            with st.status("데이터센터에서 후보를 모으는 중…", expanded=False) as status:
                result = advisor.recommend(request, progress=status.write)
                status.write("고른 11명을 AI 가 설명하는 중… (LLM)")
                result["explain"] = explain(result, ctx.model)
                status.update(label="추천 스쿼드 완성", state="complete")
            st.session_state.ai_squad = result
        except Exception as exc:  # noqa: BLE001
            log.exception("AI 추천 스쿼드 실패")
            st.error(friendly_error(exc))

    result = st.session_state.get("ai_squad")
    if result:
        _squad_result(ctx, result)


@st.fragment(run_every=2)
def _ai_watch(ctx: Ctx, spids: list[int], tc_ids: list[int], exact_jobs: list[tuple]) -> None:
    """추천 카드의 상세(팀컬러) · 팀컬러 단계 · 팀컬러 OVR 을 받는 동안 2초마다 — 다 받았거나 4건 넘게 오면 한 번 다시 그린다
    (추천 창은 열린 채로 남는다)."""
    left = (ctx.cards.pending(spids) + ctx.teamcolors.pending(tc_ids)
            + sum(1 for spid, combo in exact_jobs if ctx.cards.exact_waiting(spid, combo)))
    if left == 0 or st.session_state.get("ai_wait_shown", left) - left >= 4:
        st.rerun()
    st.caption(f"⏳ 추천 카드의 팀컬러 · 포지션별 OVR 을 데이터센터에서 받는 중 — {left}건 남음. 받는 대로 바뀝니다.")


def _squad_result(ctx: Ctx, result: dict) -> None:
    """추천 11명 — 지금 적용되는 팀컬러(소속 · 강화 · 특성, 카드 상세의 팀컬러로 다시 센다)와 그 효과를 넣은 자리 OVR ·
    요약 능력치. 목록은 공격수부터."""
    picks = result["picks"]
    target = result.get("team")
    spids = [p["card"]["spid"] for p in picks.values()]
    ctx.cards.want(spids)   # 팀컬러를 세려면 카드 상세가 있어야 한다 (경기장에 넣으면 바로 보이게도)
    infos = ctx.cards.get(spids)
    cards = {code: get_player(ctx, str(p["card"]["spid"])) for code, p in picks.items()}
    slots = {code: {"player": cards[code], "info": infos.get(p["card"]["spid"]), "grade": p["grade"],
                    "ovr": p.get("ovr") or (p["card"].get("ovr") or 0) + GRADE_OVR[p["grade"]], "exact": p.get("exact", True)}
             for code, p in picks.items()}
    team = team_of(ctx, slots)
    exact_jobs = _teamcolor_ovrs(ctx, slots, team, settled(ctx, team))
    ovrs = [s["ovr"] for s in slots.values() if s["ovr"]]
    for code, p in picks.items():   # 급여는 추천에 쓴 선수 검색 값 (스쿼드 메이커와 같은 요약 칸에 넣는다)
        slots[code]["pay"] = p["card"].get("pay")
    pays = [s["pay"] for s in slots.values() if s["pay"] is not None]
    view = {"slots": slots, "team": team, "pay": sum(pays), "pay_known": len(pays), "filled": len(slots),
            "avg": sum(ovrs) / len(ovrs) if ovrs else None, "all_exact": all(s["exact"] for s in slots.values())}
    team_text = ""
    if target:
        reached = f"{target['level']}단계" if target["level"] else "단계 미달"
        team_text = f"목표 팀컬러 {target['name']} {target['members']}/{target['target']}명 {reached}"
    st.markdown(
        f'<div class="fc-section">📋 추천 스쿼드 — {escape(MODE_NAMES[result["mode"]])} · {escape(result["formation"])}'
        f'<small>{escape(team_text)}</small></div>', unsafe_allow_html=True)
    total, budget = result["total"], result["budget"]
    spent = min(100, round(100 * total / budget)) if budget else 0
    lead = (f'<div><span>총 시세</span><b>{escape(bp(total))}</b> <small>/ 예산 {escape(bp(budget))}</small>'
            f'<div class="fc-gauge"><i style="width:{spent}%"></i></div></div>')
    st.markdown(summary_html(view, lead=lead, avg_label="평균 OVR (자리 · 강화 · 팀컬러 반영)"), unsafe_allow_html=True)
    st.markdown(applied_html(team), unsafe_allow_html=True)
    for note in result["notes"]:
        st.caption(f"⚠️ {note}")
    waiting = ctx.cards.pending(spids) + ctx.teamcolors.pending(team["need_detail"]) + len(exact_jobs)
    if waiting:
        st.session_state.ai_wait_shown = waiting
        _ai_watch(ctx, spids, team["need_detail"], exact_jobs)
    grades = {code: p["grade"] for code, p in picks.items()}
    slot_values = {code: (s["ovr"], s["exact"]) for code, s in slots.items()}
    total = result["total"] or 1
    extras = {code: {"pay": p["card"].get("pay"), "flag": (infos.get(p["card"]["spid"]) or {}).get("nation_flag", ""),
                     "price": f"{ui_parts.bp_short(p['price'])} · <b>{share_text(p['price'] / total * 100)}</b>"}
              for code, p in picks.items()}
    p_col, l_col = st.columns([1.1, 1], gap="medium")
    with p_col:
        pitch("ai_pitch", pitch_slots(result["slots"], cards, result["formation"], grades, slot_values, extras))
        st.markdown(chips_html(view, result["formation"]), unsafe_allow_html=True)
    with l_col:
        for code in list_order(list(result["slots"]), result["formation"]):
            p = picks.get(code)
            if not p:
                st.markdown(f"`{code}` _후보 없음_")
                continue
            card, s = p["card"], slots[code]
            usage = f"랭커 {card['share']:.1f}% · " if card.get("share") else ""
            tc = f" · {teamcolor_note(s)}" if s.get("tc_bonus") else ""
            st.markdown(ui_parts.card_row(cards[code], s["info"], grade=p["grade"], ovr=s["ovr"], position=code,
                                          pay=card.get("pay"), exact=s["exact"], ovr_label=f"{code} OVR",
                                          foot=card.get("foot"), crests=crests_of(ctx, s["info"]), wait_text="",
                                          note=f"{usage}{bp(p['price'])}{tc}", compact=True,
                                          tc_summary=s.get("tc_summary")), unsafe_allow_html=True)
    explained = result.get("explain")
    if explained:
        st.markdown(explained["answer"])
        render_sources(explained["answer"], explained["evidence"])
    if st.button("✅ 이 스쿼드를 경기장에 넣기", key="ai_squad_use", type="primary", width="stretch"):
        _set(result["formation"], {code: ({"pid": str(p["card"]["spid"]), "grade": p["grade"]} if (p := picks.get(code)) else None)
                                   for code in result["slots"]})
        st.session_state.tac_msg = f"🤖 AI 추천 스쿼드({MODE_NAMES[result['mode']]} · {result['formation']})를 넣었습니다."
        _close_dialog()
        st.rerun()


# ── 화면 ────────────────────────────────────────────────────────

def _slot_editor(ctx: Ctx, code: str, slot: dict) -> None:
    """자리 선수 꾸미기 — 강화 등급 · 보유 신규 특성 · 선수 이미지(같은 선수의 다른 시즌 카드 액션샷, LIVE 시즌 카드는 못 바꾼다)."""
    entry = _squad()[code]
    player = slot["player"]
    fields = {"grade": slot["grade"], "trait": entry.get("trait") or NO_TRAIT}
    for field, value in fields.items():   # 다른 곳(AI 추천 · 불러오기)에서 바뀐 값에 맞춘다
        key = f"tac_{field}_{code}"
        if st.session_state.get(key) != value:
            st.session_state[key] = value
    grade_buttons(f"{player['name']} 강화 등급", f"tac_grade_{code}", on_change=_set_field,
                  args=(code, "grade", f"tac_grade_{code}"))
    t_col, r_col = st.columns([2, 1], vertical_alignment="bottom")
    t_col.selectbox("보유 신규 특성", [NO_TRAIT] + list(NEW_TRAIT_ICONS), key=f"tac_trait_{code}", on_change=_set_field,
                    args=(code, "trait", f"tac_trait_{code}"), help="카드에 얹은 신규 특성 (경기장 카드 오른쪽 위에 보입니다)")
    r_col.button("↺ 이 선수 초기화", key=f"tac_reset_{code}", on_click=_reset_slot, args=(code,), width="stretch",
                 help="+1강 · 신규 특성 없음 · 기본 이미지로 (선수는 그대로)")
    _image_picker(ctx, code, player, entry)


def _set_img(code: str, spid: int) -> None:
    if _squad().get(code):
        _squad()[code]["img"] = None if spid == OWN_IMAGE else spid


def _image_picker(ctx: Ctx, code: str, player: dict, entry: dict) -> None:
    """선수 이미지 고르기 — 같은 선수의 다른 시즌 카드 액션샷을 이 카드 배경 위에 그려 보여 주고 눌러서 고른다 (골키퍼도 같다).
    LIVE 시즌 카드는 바꿀 수 없다. 없는 것으로 확인된 액션샷(넥슨 CDN 404)은 뺀다."""
    own = int(player["player_id"])
    st.markdown('<div class="fc-gradelabel">선수 이미지 — 다른 시즌 카드 액션샷</div>', unsafe_allow_html=True)
    if is_live_season(player.get("season")):
        st.caption("LIVE 시즌 카드는 선수 이미지를 바꿀 수 없습니다.")
        return
    others = [s for s in by_ovr(ctx, ctx.catalog.cards_of(own % 1_000_000))   # OVR 높은 순
              if s != own and ui_parts.image(ACTION_IMAGE.format(spid=s))]
    if not others:
        st.caption("이 선수는 다른 시즌 카드(액션샷)가 없어 바꿀 이미지가 없습니다.")
        return
    current = entry.get("img") if entry.get("img") in others else OWN_IMAGE
    items = []
    for choice in [OWN_IMAGE] + others:   # 이 카드 배경 위에 그 시즌 액션샷 — 바꾸면 이렇게 보인다
        spid = own if choice == OWN_IMAGE else choice
        items.append((choice, {**player, "image": ACTION_IMAGE.format(spid=spid)},
                      "기본" if choice == OWN_IMAGE else season_short(ctx.catalog.season_name(spid)),
                      ctx.catalog.season_name(spid) + (" (이 카드)" if choice == OWN_IMAGE else ""), None))
    card_tiles(f"tac_img_{code}", items, current, lambda choice: _set_img(code, choice))


def _remove(code: str) -> None:
    """이 자리 선수를 뺀다 — 자리(포지션)는 그대로 두고 비운다."""
    if _squad().get(code):
        _squad()[code] = None
        st.session_state.tac_msg = f"✕ {code} 자리의 선수를 뺐습니다 — 그 줄의 '➕ 추가'로 다시 넣을 수 있습니다."


def teamcolor_note(slot: dict) -> str:
    """이 선수에게 적용되는 팀컬러와 효과 그대로 — '🎨 첼시 4단계: 전체 능력치 +4, 볼 컨트롤 +3, 크로스 +3'."""
    applied = (slot.get("tc_bonus") or {}).get("applied") or []
    return " / ".join(f"🎨 {name} {level}단계: {', '.join(effects)}" for name, level, effects in applied)


def _slot_list(ctx: Ctx, codes: list[str], view: dict) -> None:
    """자리별 선수 줄 (공격수부터) — 카드 그림 · 포지션 · 시즌 · 이름 · 급여 · 강화 · 주발 · 국적/팀컬러 · 특성 · 그 자리 OVR
    + 오른쪽에 '✏️ 편집'(빈 자리는 '➕ 추가')과 '✕ 빼기'를 위아래로 (좁은 칸에서도 버튼이 찌그러지지 않게)."""
    for code in codes:
        slot = view["slots"].get(code)
        row_col, pop_col = st.columns([5, 1.25], vertical_alignment="center")
        if slot:
            notes = [teamcolor_note(slot)] if slot.get("tc_bonus") else []
            if slot.get("pay_change"):
                notes.append(f"급여 {slot['pay_change']['before']} → {slot['pay']} (로스터 패치 {slot['pay_change']['at'][:10]})")
            note = " · ".join(notes)
            row_col.markdown(ui_parts.card_row(slot["player"], slot["info"], grade=slot["grade"], ovr=slot["ovr"], position=code,
                                               pay=slot["pay"], foot=slot["foot"], crests=crests_of(ctx, slot["info"]),
                                               exact=slot["exact"], note=note, ovr_label=f"{code} OVR", compact=True,
                                               tc_summary=slot.get("tc_summary")),
                             unsafe_allow_html=True)
        else:
            row_col.markdown(f'<div class="fc-prow compact" style="grid-template-columns:1fr"><div>{ui_parts.position_badge(code)} '
                             f'<span class="wait">비어 있음 — 오른쪽 \'➕ 추가\'로 선수를 넣으세요</span></div></div>',
                             unsafe_allow_html=True)
        with pop_col.popover("✏️ 편집" if slot else "➕ 추가", width="stretch"):
            pick_key = f"tac_pick_{code}"
            current = slot["player"]["player_id"] if slot else None
            if st.session_state.get(f"{pick_key}_seen") != current:   # 자리 선수가 바뀌었으면 선택기를 그 카드로 다시 채운다
                for suffix in ("_name", "_who", "_card"):
                    st.session_state.pop(f"{pick_key}{suffix}", None)
                st.session_state[f"{pick_key}_seen"] = current
            if slot:
                _slot_editor(ctx, code, slot)
                st.divider()
            card_picker(ctx, key=pick_key, default_id=current, label="바꿀" if slot else "넣을")
            g_col, b_col = st.columns([3.2, 1], vertical_alignment="bottom")   # 강화 버튼 줄 오른쪽에 넣기 버튼
            with g_col:
                grade_buttons("넣을 카드의 강화", f"{pick_key}_grade")
            b_col.button("이 카드로 바꾸기" if slot else f"{code} 자리에 넣기", key=f"tac_apply_{code}", type="primary",
                         on_click=_apply, args=(code, pick_key), width="stretch")
        pop_col.button("✕ 빼기", key=f"tac_remove_{code}", width="stretch", on_click=_remove, args=(code,),
                       disabled=not slot, help=f"{code} 자리의 선수를 뺍니다 (자리는 비워 둡니다)")


def render(ctx: Ctx) -> None:
    st.markdown('<div class="fc-section">🧠 스쿼드 메이커 · AI 전술 추천<small>선수 얼굴을 끌어 자리를 바꾸면 포메이션 · 자리 OVR 이 '
                '바뀝니다 — 총 급여 · 팀컬러를 함께 보고, 짠 스쿼드는 저장해 두었다가 불러옵니다</small></div>',
                unsafe_allow_html=True)
    _ensure_state(ctx)
    codes = _codes()
    custom = st.session_state.tac_formation not in FORMATIONS

    f_col, s_col = st.columns([1, 2], vertical_alignment="bottom")
    options = list(FORMATIONS) + ([CUSTOM] if custom else [])
    f_col.selectbox("포메이션 (공식 33개)", options, key="tac_formation_pick", on_change=_on_formation,
                    format_func=lambda f: f"{GROUP_OF_FORMATION[f]} · {f}" if f in GROUP_OF_FORMATION else f"✋ {f}")
    st.session_state.setdefault("tac_style", PLAY_STYLES[0])
    with s_col:
        style = st.pills("원하는 스타일", PLAY_STYLES, key="tac_style") or PLAY_STYLES[0]

    b1, b2, b3, b6, b4, b5 = st.container(key="wrap_tacbtns").columns([1.3, 1.1, 0.9, 1.1, 1.5, 1.5],
                                                                     vertical_alignment="center")
    b1.button("⚡ 파워 랭킹 추천", key="tac_power", on_click=_fill_power, args=(ctx,), width="stretch")
    account = ctx.db.account()
    b2.button("🎮 내 스쿼드", key="tac_mine", on_click=_fill_mine, args=(ctx,), width="stretch",
              help=f"연결한 계정({account['nickname']})의 최근 공식경기 선발 11명" if account else "🎮 내 기록에서 계정을 먼저 연결하세요")
    b3.button("🧹 비우기", key="tac_clear", on_click=_clear, width="stretch")
    b6.button("↺ 초기화", key="tac_reset_all", on_click=_reset_all, width="stretch",
              help="모든 선수의 강화 · 보유 신규 특성 · 선수 이미지를 처음으로 (선수 · 포메이션은 그대로)")
    b4.button("🤖 AI 추천 스쿼드", key="tac_ai_squad", type="primary", width="stretch", on_click=_open_dialog,
              help="모드 · 전술 · 꼭 넣을 선수 · 팀컬러 · 예산으로 11명을 추천")
    if st.session_state.get("ai_dialog"):   # 닫을 때까지 열어 둔다 — 화면이 다시 그려져도(카드 상세가 도착해도) 닫히지 않게
        squad_dialog(ctx)
    with b5.popover("💾 저장 · 불러오기", width="stretch"):
        _save_load(ctx)
    if st.session_state.get("tac_msg"):
        st.info(st.session_state.tac_msg)

    entries = _squad()
    players = {code: get_player(ctx, entries[code]["pid"]) if entries.get(code) else None for code in codes}
    grades = {code: (entries[code] or {}).get("grade") for code in codes}
    view = squad_view(ctx, codes, players, grades)
    formation = st.session_state.tac_formation
    st.markdown(top_html(view), unsafe_allow_html=True)
    st.markdown(summary_html(view), unsafe_allow_html=True)
    if view["waiting"]:
        st.session_state.tac_wait_shown = view["waiting"]
        _watch(ctx, view["spids"], view["tc_ids"], view["price_spids"], view["exact_jobs"])

    pitch_col, list_col = st.columns([1.1, 1], gap="large")
    with pitch_col:
        st.caption("카드를 끌어 다른 자리에 놓으면 포지션 · 자리 OVR 이 바뀝니다 · 카드 밑은 시세 · 구단가치 중 비율")
        ovrs = {code: (s["ovr"], s["exact"]) for code, s in view["slots"].items()}
        extras = {code: {"player": s["player"], "pay": s["pay"], "trait": s["trait"],
                         "flag": (s["info"] or {}).get("nation_flag", ""),
                         "price": (f"{ui_parts.bp_short(s['price'])} · <b>{share_text(s['share'])}</b>"
                                   if s["price"] else "시세 받는 중" if ctx.market.enabled else "")}
                  for code, s in view["slots"].items()}
        pitch("tac_pitch", pitch_slots(codes, players, formation if formation in FORMATIONS else None, grades, ovrs, extras),
              on_move=_on_drop)
        st.markdown(chips_html(view, formation), unsafe_allow_html=True)
        st.caption("카드의 숫자는 그 자리에서의 OVR(강화 포함)입니다. * 는 카드 상세를 받기 전의 추정값입니다.")
    with list_col:
        st.markdown(f"**자리별 선수** — {escape(formation)} · 공격수부터 · '➕ 추가' · '✏️ 편집'으로 넣기 · 교체 · 강화 · 이미지, "
                    "'✕ 빼기'로 그 자리 비우기")
        _slot_list(ctx, list_order(codes, formation), view)
    st.markdown(teamcolor_html(view), unsafe_allow_html=True)
    with st.expander("📋 FC 온라인 팀 전술 항목"):
        for name, options_text, desc in TEAM_TACTICS:
            st.markdown(f"**{name}** · {options_text}  \n{desc}")
        st.caption("항목 이름 · 선택지는 게임 업데이트로 바뀔 수 있습니다.")

    filled = [(code, p) for code, p in players.items() if p]
    label = formation if not custom else f"{CUSTOM} ({', '.join(codes)})"
    key = f"tactic:{label}:{style}:" + ",".join(f"{code}={p['player_id']}+{grades.get(code) or 1}" for code, p in filled)
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
                    filled = [(code, p) for (code, _), p in zip(filled, full)]
                status.write("전술 항목 설명 + 선수들의 자리 OVR · 기록 · 팀컬러를 근거로 추천을 만드는 중... (LLM)")
                saved = recommend_tactics(label, style, filled, ctx.model, squad_facts(view))
                st.session_state.results[key] = saved
                status.update(label="전술 추천 완료", state="complete")
        except Exception as exc:  # noqa: BLE001
            log.exception("전술 추천 실패")
            st.error(friendly_error(exc))
    if saved:
        st.markdown('<div class="fc-section">📝 추천 전술</div>', unsafe_allow_html=True)
        st.markdown(saved["answer"])
        render_sources(saved["answer"], saved["evidence"])


def squad_facts(view: dict) -> dict:
    """AI 전술 추천에 넘길 스쿼드 사실 — 자리마다 (그 자리 OVR, 강화, 정확한지) · 평균 OVR · 총 급여 · 적용 팀컬러."""
    applied = []
    for group in GROUPS:
        best = next((e for e in view["team"]["groups"].get(group, []) if e["level"]), None)
        if best:
            applied.append(f"{group} {best['name']} {best['level']}단계 ({best['count']}명 · {', '.join(best['effects'])})")
    return {"slots": {code: {"ovr": s["ovr"], "grade": s["grade"], "exact": s["exact"]} for code, s in view["slots"].items()},
            "avg": view["avg"], "pay": view["pay"], "pay_cap": PAY_CAP, "teamcolors": applied}
