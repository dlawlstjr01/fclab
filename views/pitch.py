"""스쿼드 경기장 — 자리마다 선수 카드(공식 스쿼드 메이커처럼). 카드를 끌어 다른 자리에 놓으면 그 자리의 포지션으로 바뀐다.

카드: 시즌 카드 그림 위에 그 자리 OVR · 포지션 · 급여, 보유 신규 특성, 강화, 국기 · 이름 — 카드 밑에 시세 · 구단 가치 중 비율
(ui_parts.pitch_card). 브라우저 쪽 부품(st.components.v2)이 끌기 · 놓기를 맡아, 끄는 동안 놓일 포지션 구역을 밝혀 주고
놓은 자리(경기장 위 %)만 앱에 알린다. 포지션은 앱이 정한다 — 공식 스쿼드 메이커와 같은 구역 · 같은 규칙
(data_prep.tactics.position_at). 런타임이 없을 때(스크립트로 불러 쓸 때)는 끌 수 없는 같은 그림을 HTML 로 그린다.
"""

from __future__ import annotations

from html import escape

import streamlit as st

import ui_parts
from data_prep.tactics import SQUAD_ADAPT, ZONE_PRIORITY, ZONES, ovr_at, slot_xy
from views.common import Ctx, browser_component

# 경기장 그림 위 % ↔ 공식 좌표 (세로 0 = 공격 라인 ~ 115 = 골키퍼, 가로 0~100).
# 카드가 경기장 밖으로 잘리지 않게 위아래 · 양옆을 카드 반만큼 비운다 (맨 아래 골키퍼 카드 · 시세 줄까지 들어오게).
X0, X_SCALE, Y0, Y_SCALE = 9.0, 0.82, 8.5, 0.72


def screen(top: float, left: float) -> tuple[float, float]:
    return round(X0 + left * X_SCALE, 2), round(Y0 + top * Y_SCALE, 2)


def field(x: float, y: float) -> tuple[float, float]:
    """그림 위 (가로 %, 세로 %) → 공식 좌표 (세로, 가로)."""
    return (y - Y0) / Y_SCALE, (x - X0) / X_SCALE


def _zones() -> list[dict]:
    out = []
    for code, (left, top, width, height) in ZONES.items():
        x0, y0 = screen(top, left)
        x1, y1 = screen(top + height, left + width)
        out.append({"code": code, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                    "rank": ZONE_PRIORITY.get(code, 9)})
    return out


PITCH_CSS = ui_parts.CARD_CSS + """
.fc-pitch2 { --card: 17%; position: relative; width: 100%; aspect-ratio: 0.66; border-radius: 18px; overflow: hidden;
  user-select: none; touch-action: none; background: repeating-linear-gradient(180deg, #0f5132 0 9%, #0d4a2d 9% 18%);
  border: 2px solid rgba(255,255,255,.18); font-family: inherit; }
.mark { position: absolute; border: 2px solid rgba(255,255,255,.22); pointer-events: none; }
.mid { left: 0; right: 0; top: 50%; border-width: 2px 0 0 0; }
.circle { left: 50%; top: 50%; width: 22%; aspect-ratio: 1; transform: translate(-50%, -50%); border-radius: 50%; }
.box { left: 25%; width: 50%; height: 13%; }
.box.top { top: -2px; border-top: none; }
.box.bottom { bottom: -2px; border-bottom: none; }
.zone { position: absolute; display: none; justify-content: center; padding-top: 2px; box-sizing: border-box;
  border: 1px dashed rgba(255,255,255,.2); border-radius: 6px; color: rgba(255,255,255,.5); font-size: 10px; font-weight: 800; }
.dragging .zone { display: flex; }
.zone.hot { background: rgba(22,224,138,.22); border: 2px solid #16e08a; color: #fff; z-index: 1; }
.slot { position: absolute; transform: translate(-50%, -50%); width: var(--card); text-align: center; cursor: grab; z-index: 2;
  transition: left .25s ease, top .25s ease; }
.slot img { pointer-events: none; -webkit-user-drag: none; }
.slot.locked { cursor: default; }
.slot.lifted { transition: none; z-index: 6; cursor: grabbing; filter: drop-shadow(0 8px 14px rgba(0,0,0,.6)); }
.slot .empty { width: 72%; aspect-ratio: 340 / 400; margin: 0 auto; border-radius: 10px; border: 2px dashed rgba(255,255,255,.5);
  display: flex; align-items: center; justify-content: center; box-sizing: border-box; color: #dfe6f3; font-size: 12px;
  font-weight: 900; }
.slot .ps { font-size: 10px; color: #cfe9dc; text-shadow: 0 1px 2px rgba(0,0,0,.85); white-space: nowrap; }
.slot:has(.empty) { cursor: pointer; }
.slot:hover .empty { border-color: #16e08a; color: #16e08a; background: rgba(22,224,138,.12); }
@media (max-width: 640px) { .fc-pitch2 { --card: 18%; } .pc .price { font-size: 9px; } }
"""

PITCH_JS = """
export default function(component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector(".fc-pitch2");
  if (!root) { root = document.createElement("div"); root.className = "fc-pitch2"; parentElement.appendChild(root); }
  const zones = data.zones.map((z) =>
    `<div class="zone" data-code="${z.code}" style="left:${z.x0}%;top:${z.y0}%;width:${z.x1 - z.x0}%;height:${z.y1 - z.y0}%">${z.code}</div>`).join("");
  // 카드 HTML 은 앱(파이썬)이 만든다 — 이름 같은 값은 거기서 이스케이프했다.
  // 위에서 아래 순서로 그린다 — 아래 카드가 위 카드의 시세 줄을 덮어, 자리를 붙여 놓아도 아래 카드의 OVR 이 가려지지 않는다
  const slots = [...data.slots].sort((a, b) => a.y - b.y).map((s) =>
    `<div class="slot${s.locked ? " locked" : ""}" data-code="${s.code}" data-x="${s.x}" data-y="${s.y}"` +
    ` style="left:${s.x}%;top:${s.y}%">${s.html}</div>`).join("");
  root.innerHTML = `<div class="mark box top"></div><div class="mark box bottom"></div><div class="mark mid"></div>` +
                   `<div class="mark circle"></div>${zones}${slots}`;
  // 끌지 않고 누르면 그 선수 상세로 (빈 자리는 빼고)
  // 끌지 않고 누르면 — 선수가 있으면 그 선수 상세, 빈 자리면 그 자리의 선수 배치 (앱이 정한다)
  const open = (slot) => { if (data.clickable) setTriggerValue("clicked", { code: slot.dataset.code, at: Date.now() }); };
  if (data.readonly) { root.querySelectorAll(".slot").forEach((s) => s.addEventListener("click", () => open(s))); return; }
  root.querySelectorAll(".slot.locked").forEach((s) => s.addEventListener("click", () => open(s)));
  const occupied = new Set(data.slots.map((s) => s.code));
  // 놓일 포지션 — 점을 품은 구역 중 중심이 가장 가까운 것, 없으면 가장 가까운 구역 (골키퍼 자리 · 센터백 없는 스위퍼는 빼고)
  const zoneAt = (x, y) => {
    const ok = data.zones.filter((z) => z.code !== "GK" && (z.code !== "SW" || occupied.has("CB")));
    const inside = ok.filter((z) => x >= z.x0 && x <= z.x1 && y >= z.y0 && y <= z.y1);
    const dist = (z) => Math.hypot(x - z.cx, y - z.cy);
    return (inside.length ? inside : ok).reduce((best, z) =>
      !best || dist(z) < dist(best) - 1e-6 || (Math.abs(dist(z) - dist(best)) < 1e-6 && z.rank < best.rank) ? z : best, null);
  };
  const point = (ev) => { const r = root.getBoundingClientRect(); return [(ev.clientX - r.left) / r.width * 100, (ev.clientY - r.top) / r.height * 100]; };
  root.querySelectorAll(".slot:not(.locked)").forEach((slot) => {
    slot.addEventListener("pointerdown", (down) => {
      if (down.button !== 0) return;
      down.preventDefault();
      slot.setPointerCapture(down.pointerId);
      const start = point(down);
      let moved = false, target = null;
      const move = (ev) => {
        const [x, y] = point(ev);
        if (!moved && Math.hypot(x - start[0], y - start[1]) < 1.5) return;
        moved = true;
        root.classList.add("dragging");
        slot.classList.add("lifted");
        slot.style.left = x + "%";
        slot.style.top = y + "%";
        target = zoneAt(x, y);
        root.querySelectorAll(".zone").forEach((z) => z.classList.toggle("hot", !!target && z.dataset.code === target.code));
      };
      const up = (ev) => {
        slot.removeEventListener("pointermove", move);
        slot.removeEventListener("pointerup", up);
        slot.removeEventListener("pointercancel", up);
        root.classList.remove("dragging");
        slot.classList.remove("lifted");
        const [x, y] = point(ev);
        if (moved && target && target.code !== slot.dataset.code) {
          setTriggerValue("moved", { from: slot.dataset.code, x, y, at: Date.now() });
        } else {
          slot.style.left = slot.dataset.x + "%";
          slot.style.top = slot.dataset.y + "%";
          if (!moved) open(slot);
        }
      };
      slot.addEventListener("pointermove", move);
      slot.addEventListener("pointerup", up);
      slot.addEventListener("pointercancel", up);
    });
  });
}
"""


def slot_ovrs(ctx: Ctx, codes: list[str], squad: dict[str, dict | None],
              grades: dict[str, int | None]) -> dict[str, tuple[int | None, bool]]:
    """자리마다 (그 자리에서의 OVR + 강화 + 적응도 5, 정확한 값인지) — 카드 상세의 포지션별 OVR 16칸 → 선수 검색의 포지션 목록 →
    대표 OVR(추정) 순. 스쿼드에 등록한 선수는 적응도 5 (모든 능력치 +4 — data_prep.tactics.SQUAD_ADAPT).
    카드 상세가 없는 카드는 받기 시작한다."""
    spids = [int(p["player_id"]) for p in squad.values() if p]
    ctx.cards.want(spids)
    infos = ctx.cards.get(spids)
    known = ctx.db.card_stats([s for s in spids if s not in infos])
    out = {}
    for code in codes:
        p = squad.get(code)
        if p:
            info, row = infos.get(int(p["player_id"])) or {}, known.get(int(p["player_id"])) or {}
            out[code] = ovr_at(code, grades.get(code), info.get("position_ovr"), row.get("positions"),
                               info.get("ovr") or row.get("ovr"), adapt=SQUAD_ADAPT)
    return out


def pitch_slots(codes: list[str], squad: dict[str, dict | None], formation: str | None,
                grades: dict[str, int | None] | None = None,
                ovrs: dict[str, tuple[int | None, bool]] | None = None, extras: dict[str, dict] | None = None,
                empty_hint: str = "비어 있음") -> list[dict]:
    """자리마다 그림 위치 · 카드 HTML. extras[자리] = {player(바꾼 이미지), pay, trait{name, icon}, flag, price} (있는 것만).
    empty_hint = 빈 자리 밑 글자 (눌러서 넣을 수 있는 경기장은 '+ 눌러서 넣기').
    name · sub('그 자리 OVR · 시즌 +강화', * 는 추정값)은 점검이 읽는다."""
    out = []
    for code in codes:
        p = squad.get(code)
        x, y = screen(*slot_xy(code, formation))
        grade = (grades or {}).get(code)
        ovr, exact = (ovrs or {}).get(code, (None, True))
        if p:
            extra = (extras or {}).get(code, {})
            html = ui_parts.pitch_card(extra.get("player") or p, code, ovr, exact, grade, extra.get("pay"), extra.get("trait"),
                                       extra.get("flag", ""), extra.get("price", ""), nation=extra.get("nation", ""))
        else:
            html = f'<div class="empty">{escape(code)}</div><div class="ps">{escape(empty_hint)}</div>'
        head = f"{ovr}{'' if exact else '*'} · " if ovr else ""
        out.append({"code": code, "x": x, "y": y, "locked": code == "GK", "html": html, "name": p["name"] if p else "",
                    "sub": f"{head}{p['season_short']}{f' +{grade}' if grade else ''}" if p else ""})
    return out


def pitch(key: str, slots: list[dict], on_move=None, on_open=None) -> None:
    """경기장. on_move 가 있으면 카드를 끌어 옮길 수 있다 (없으면 보기만 — AI 추천 결과). on_open 이 있으면 끌지 않고 누른
    카드의 선수 상세로 — on_open 안에서 clicked(key) 로 누른 자리를 읽는다."""
    component = browser_component("fclab_pitch", css=PITCH_CSS, js=PITCH_JS)
    if component is None:   # 런타임 없이 — 같은 그림을 HTML 로 (끌기 없음)
        marks = ('<div class="mark box top"></div><div class="mark box bottom"></div><div class="mark mid"></div>'
                 '<div class="mark circle"></div>')
        body = "".join(f'<div class="slot locked" style="left:{s["x"]}%;top:{s["y"]}%">{s["html"]}</div>'
                       for s in sorted(slots, key=lambda s: s["y"]))   # 위에서 아래로 (아래 카드가 위 카드의 시세 줄을 덮게)
        st.markdown(f"<style>{PITCH_CSS}</style><div class='fc-pitch2'>{marks}{body}</div>", unsafe_allow_html=True)
        return
    data = {"slots": slots, "zones": _zones(), "readonly": on_move is None, "clickable": on_open is not None}
    handlers = {"on_moved_change": on_move, "on_clicked_change": on_open}
    component(key=key, data=data, **{name: fn for name, fn in handlers.items() if fn is not None})


def clicked(key: str) -> str | None:
    """방금 누른 자리 (끌지 않고 누른 카드) — on_open 콜백 안에서 부른다."""
    return ((st.session_state.get(key) or {}).get("clicked") or {}).get("code")


def dropped(key: str) -> tuple[str, float, float] | None:
    """방금 놓은 선수 — (원래 포지션, 놓은 곳의 공식 좌표 세로, 가로). on_move 콜백 안에서 부른다."""
    moved = (st.session_state.get(key) or {}).get("moved") or {}
    if not moved.get("from"):
        return None
    top, left = field(float(moved["x"]), float(moved["y"]))
    return str(moved["from"]), top, left
