"""UI 영역의 화면 부품 — 스타일, 선수 카드 · 타일, 스탯 막대, 포지션 사용, 비교 차트, 전적. (F2009 · F2010)

모든 사용자 데이터(선수 이름 · 소개 · 닉네임)는 html.escape 로 감싸 HTML 에 넣는다.
색: 화면은 .streamlit/config.toml 의 다크 테마. 차트 계열색은 검증한 범주형 두 칸(파랑 · 주황),
상태(승 · 무 · 패)는 상태색 + 글자로 — 색만으로 뜻을 전하지 않는다.
"""

from __future__ import annotations

from html import escape

from data_prep.players import KEY_STATS, STATS, key_stat_line, save_rate, stat_values

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "s1": "#2a78d6", "s2": "#eb6834", "border": "rgba(11,11,11,0.10)", "track": "#efeee9",
    },
    "dark": {
        "surface": "#141c2e", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#25304a", "s1": "#3987e5", "s2": "#d95926", "border": "rgba(255,255,255,0.08)", "track": "#22304a",
    },
}
STATUS = {"승": "#0ca30c", "무": "#898781", "패": "#d03b3b"}
ACCENT = "#16e08a"


def css(theme: str) -> str:
    t = THEMES.get(theme, THEMES["dark"])
    return f"""
<style>
.block-container {{ padding-top: 1.2rem; max-width: 1280px; }}
h1, h2, h3 {{ letter-spacing: -0.02em; }}
a {{ color: {ACCENT}; }}

/* 상단 바 — 로고는 버튼(누르면 홈) */
.st-key-nav_brand button p {{ font-size: 21px; font-weight: 900; letter-spacing: .02em; color: {ACCENT}; white-space: nowrap; }}
.st-key-nav_back button p {{ font-size: 18px; font-weight: 800; }}

/* 카드 전체를 누르는 타일 — 타일 위에 투명 버튼을 겹친다 (키보드로 가면 테두리가 보인다) */
div[class*="st-key-tile_"] {{ position: relative; gap: 4px; }}
div[class*="st-key-tile_"] [data-testid="stElementContainer"]:has(.stButton) {{ position: absolute; inset: 0; z-index: 3; margin: 0; }}
div[class*="st-key-tile_"] .stButton, div[class*="st-key-tile_"] .stButton button {{ width: 100%; height: 100%; }}
div[class*="st-key-tile_"] .stButton button {{ opacity: 0; cursor: pointer; }}
div[class*="st-key-tile_"] .stButton button:focus-visible {{ opacity: 1; background: transparent; color: transparent;
  border: 2px solid {ACCENT}; }}
div[class*="st-key-tile_"]:hover .fc-tile {{ transform: translateY(-2px); border-color: rgba(22,224,138,.55); }}

/* 상단 메뉴 — 넓은 화면은 한 줄 고정, 900px 이하에서만 줄바꿈 (모바일에서도 세로로 길게 쌓이지 않게) */
.st-key-navbar [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap; gap: .35rem; }}
.st-key-navbar [data-testid="stColumn"] {{ min-width: 0; }}
.st-key-navbar button p {{ white-space: nowrap; }}
@media (max-width: 900px) {{
  .st-key-navbar [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; row-gap: .35rem; }}
  .st-key-navbar [data-testid="stColumn"] {{ width: auto !important; min-width: 92px; flex: 1 1 92px !important; }}
}}

/* 반응형 — 화면이 좁아지면 줄바꿈 (st-key-wrap_* : 필터 줄, st-key-grid_* : 카드 묶음) */
div[class*="st-key-wrap_"] [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; row-gap: .5rem; }}
div[class*="st-key-wrap_"] [data-testid="stColumn"] {{ min-width: min(100%, 120px); }}
div[class*="st-key-grid_"] [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
div[class*="st-key-grid_"] [data-testid="stColumn"] {{ min-width: 150px; flex: 1 1 150px; }}
@media (max-width: 1100px) {{
  .fc-hero h1 {{ font-size: 32px; }}
}}
@media (max-width: 768px) {{
  .block-container {{ padding-left: .9rem; padding-right: .9rem; }}
  .fc-hero {{ padding: 20px 18px; }}
  .fc-hero h1 {{ font-size: 24px; }}
  .fc-hero .stats {{ gap: 16px; }}
  .fc-hero .stats b {{ font-size: 18px; }}
  .fc-card .ph {{ height: 180px; }}
  .fc-card .ph img, .fc-card .ph object {{ max-height: 180px; }}
  .fc-row {{ grid-template-columns: 72px 1fr 80px; }}
  .fc-compare .crow {{ grid-template-columns: 72px 1fr; }}
  .fc-pitch .slot {{ width: 66px; }}
  .fc-pitch .slot img {{ width: 36px; height: 36px; }}
  .fc-pitch .pn {{ font-size: 10px; }}
  .fc-pitch .ps {{ font-size: 9px; }}
  .fc-line {{ gap: 8px; }}
  .fc-line .st {{ font-size: 11.5px; }}
  .fc-section {{ font-size: 17px; }}
}}

/* 순위 · 목록 행 */
.fc-line {{ display: flex; align-items: center; gap: 12px; min-height: 52px; }}
.fc-line img {{ width: 44px; height: 44px; border-radius: 50%; object-fit: cover; background: #0b1426; }}
.fc-line .rk {{ width: 30px; text-align: center; font-weight: 900; font-size: 16px; color: {ACCENT}; }}
.fc-line .nm {{ font-weight: 800; }}
.fc-line .sb {{ font-size: 12px; color: {t['muted']}; }}
.fc-line .st {{ font-size: 12.5px; color: {t['ink2']}; }}
.fc-line .st b {{ color: {t['ink']}; }}

/* 히어로 */
.fc-hero {{ border-radius: 22px; padding: 30px 32px 26px; margin: 4px 0 14px;
  background: radial-gradient(90% 140% at 0% 0%, rgba(22,224,138,.22) 0%, rgba(22,224,138,0) 55%),
              radial-gradient(80% 120% at 100% 0%, rgba(57,135,229,.30) 0%, rgba(57,135,229,0) 60%), #101830;
  border: 1px solid rgba(255,255,255,.06); }}
.fc-hero h1 {{ font-size: 40px; margin: 0 0 6px; color: #fff; }}
.fc-hero h1 b {{ color: {ACCENT}; }}
.fc-hero p {{ margin: 0; color: #c3cbe0; font-size: 15px; }}
.fc-hero .stats {{ display: flex; gap: 26px; margin-top: 16px; flex-wrap: wrap; }}
.fc-hero .stats div {{ color: #9fb0d0; font-size: 12px; }}
.fc-hero .stats b {{ display: block; color: #fff; font-size: 22px; }}

.fc-section {{ font-size: 19px; font-weight: 800; margin: 20px 0 8px; }}
.fc-section small {{ font-size: 12px; font-weight: 500; color: {t['muted']}; margin-left: 8px; }}

/* 타일 (검색 결과 · 추천) */
.fc-tile {{ border-radius: 16px; padding: 10px 10px 12px; text-align: center; position: relative; overflow: hidden;
  background: linear-gradient(180deg, #1b2a4a 0%, #121b30 100%); border: 1px solid rgba(255,255,255,.06);
  transition: transform .12s ease, border-color .12s ease; min-height: 214px; }}
.fc-tile:hover {{ transform: translateY(-2px); border-color: rgba(22,224,138,.45); }}
.fc-tile.icon {{ background: linear-gradient(180deg, #4a3816 0%, #241a09 100%); }}
.fc-tile .season {{ position: absolute; top: 8px; left: 8px; display: flex; align-items: center; gap: 4px;
  font-size: 11px; font-weight: 700; color: #dfe6f3; }}
.fc-tile .season img {{ height: 16px; }}
.fc-tile .rank {{ position: absolute; top: 8px; right: 8px; font-size: 11px; font-weight: 800; color: #0b1426;
  background: {ACCENT}; border-radius: 999px; padding: 1px 7px; }}
.fc-tile .ph {{ height: 120px; margin: 18px auto 4px; display: flex; align-items: flex-end; justify-content: center; }}
.fc-tile .ph img, .fc-tile .ph object {{ max-height: 120px; max-width: 100%; }}
.fc-tile .nm {{ font-weight: 800; font-size: 15px; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.fc-tile .sub {{ font-size: 11px; color: #a9b6cf; }}
.fc-tile .kpi {{ font-size: 12px; color: #dfe6f3; margin-top: 4px; }}
.fc-tile .kpi b {{ color: #fff; }}
.fc-tile .badge {{ display: inline-block; margin-top: 8px; font-size: 11px; font-weight: 800; color: #0b1426;
  background: {ACCENT}; border-radius: 999px; padding: 2px 8px; }}
.fc-tile .why {{ margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,.10); text-align: left;
  font-size: 12px; line-height: 1.5; color: #c9d3e6; }}
div[class*="st-key-tile_"] {{ margin-bottom: 10px; }}
.fc-tile.fc-person {{ min-height: 0; padding: 8px 8px 10px; }}
.fc-tile.fc-person .ph img {{ max-height: 72px; border-radius: 50%; }}

/* 큰 선수 카드 */
.fc-card {{ position: relative; border-radius: 22px; padding: 16px 16px 18px; overflow: hidden; text-align: center;
  background: radial-gradient(120% 90% at 50% 0%, #2b4a7d 0%, #142441 55%, #0b1426 100%);
  color: #f4f6fb; box-shadow: 0 14px 34px rgba(0,0,0,.35); }}
.fc-card.icon {{ background: radial-gradient(120% 90% at 50% 0%, #8a6a2c 0%, #3d2c10 55%, #1d1406 100%); }}
.fc-card .season {{ position: absolute; top: 14px; left: 14px; display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 700; color: #dfe6f3; }}
.fc-card .season img {{ height: 22px; }}
.fc-card .pos {{ position: absolute; top: 14px; right: 14px; font-size: 13px; font-weight: 800; color: #0b1426;
  background: {ACCENT}; padding: 3px 10px; border-radius: 999px; }}
.fc-card .ph {{ height: 230px; margin: 26px auto 6px; display: flex; align-items: flex-end; justify-content: center; }}
.fc-card .ph img, .fc-card .ph object {{ max-height: 230px; max-width: 100%; filter: drop-shadow(0 10px 16px rgba(0,0,0,.5)); }}
.fc-card .name {{ font-size: 24px; font-weight: 900; margin: 4px 0 2px; }}
.fc-card .sub {{ font-size: 12px; color: #b9c3d6; }}

/* 칩 */
.fc-chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0 10px; }}
.fc-chip {{ font-size: 12px; padding: 3px 10px; border-radius: 999px; background: {t['track']}; color: {t['ink2']}; }}
.fc-chip b {{ color: {t['ink']}; }}

/* AI 한줄 스카우팅 */
.fc-ai {{ border-radius: 14px; padding: 12px 14px; margin: 8px 0 12px; font-size: 14.5px; line-height: 1.6;
  background: linear-gradient(90deg, rgba(22,224,138,.12), rgba(57,135,229,.10)); border: 1px solid rgba(22,224,138,.25); }}
.fc-ai .tag {{ font-size: 11px; font-weight: 800; color: {ACCENT}; letter-spacing: .04em; display: block; margin-bottom: 3px; }}

/* 스탯 막대 · 포지션 사용 · 비교 차트 (같은 판) */
.fc-panel {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 14px; padding: 14px 16px 10px; }}
.fc-panel .title {{ font-size: 13px; font-weight: 800; color: {t['ink']}; margin-bottom: 6px; }}
.fc-panel .title small {{ font-weight: 500; color: {t['muted']}; margin-left: 6px; }}
.fc-row {{ display: grid; grid-template-columns: 96px 1fr 92px; gap: 10px; align-items: center; padding: 5px 0;
  border-top: 1px solid {t['grid']}; }}
.fc-row:first-of-type {{ border-top: none; }}
.fc-row .lb {{ font-size: 13px; color: {t['ink2']}; }}
.fc-row .track {{ height: 8px; background: {t['track']}; border-radius: 4px; overflow: hidden; }}
.fc-row .bar {{ height: 8px; border-radius: 0 4px 4px 0; background: {t['s1']}; }}
.fc-row .vl {{ font-size: 12px; color: {t['ink']}; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.fc-row .vl small {{ color: {t['muted']}; }}
.fc-note {{ font-size: 11px; color: {t['muted']}; margin-top: 6px; }}

.fc-compare .legend {{ display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; color: {t['ink']}; }}
.fc-compare .legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }}
.fc-compare .crow {{ display: grid; grid-template-columns: 96px 1fr; gap: 12px; align-items: center; padding: 6px 0; border-top: 1px solid {t['grid']}; }}
.fc-compare .stat {{ font-size: 13px; color: {t['ink']}; font-weight: 600; }}
.fc-compare .stat small {{ display: block; font-weight: 400; color: {t['muted']}; font-size: 11px; }}
.fc-compare .line {{ display: flex; align-items: center; gap: 8px; height: 14px; margin: 2px 0; }}
.fc-compare .cbar {{ height: 10px; border-radius: 0 4px 4px 0; min-width: 2px; }}
.fc-compare .val {{ font-size: 12px; color: {t['ink2']}; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.fc-compare .val b {{ color: {t['ink']}; }}
.fc-compare .none {{ font-size: 12px; color: {t['muted']}; }}

/* 전적 */
.fc-form {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }}
.fc-form span {{ min-width: 30px; text-align: center; font-size: 12px; font-weight: 800; color: #fff; border-radius: 8px; padding: 4px 6px; }}
.fc-up {{ color: #0ca30c; font-weight: 700; }}
.fc-down {{ color: #e66767; font-weight: 700; }}
</style>
"""


# ── 이미지 ─────────────────────────────────────────────────────

def photo(player: dict) -> str:
    """액션 이미지. 바로 띄운 카드(확인 안 한 이미지)는 없을 때 얼굴 이미지가 대신 보이도록 <object> 로 감싼다."""
    image, face, name = player.get("image") or "", player.get("face") or "", escape(player["name"])
    if not image and not face:
        return '<div style="font-size:64px;line-height:1">⚽</div>'
    if player.get("lite") and face:
        return (f'<object data="{escape(image)}" type="image/png" aria-label="{name}">'
                f'<img src="{escape(face)}" alt="{name}"></object>')
    return f'<img src="{escape(image or face)}" alt="{name}" loading="lazy">'


def _season(player: dict) -> str:
    icon = f'<img src="{escape(player["season_img"])}" alt="">' if player.get("season_img") else ""
    return f'<div class="season">{icon}{escape(player["season_short"])}</div>'


# ── 타일 · 카드 ─────────────────────────────────────────────────

def tile(player: dict, rank: int | None = None, kpi: str | None = None, badge: str = "", note: str = "") -> str:
    """카드 타일. badge(예: 랭킹 순위)와 note(예: AI 추천 이유)는 카드 안 아래쪽에 — 카드 밖으로 넘치거나 겹치지 않게."""
    icon = " icon" if "ICON" in player.get("season", "") else ""
    if kpi is None:   # 포지션에 맞는 대표 스탯 (공격 골 · 도움 / 수비 태클 · 블록 / 골키퍼 선방률)
        kpi = key_stat_line(player) or ("랭커 기록 불러오기 전" if player.get("lite") else "랭커 기록 없음")
    sub = player.get("position_group") or ("카드 정보" if player.get("lite") else "포지션 기록 없음")
    rank_html = f'<div class="rank">#{rank}</div>' if rank else ""
    extra = (f'<div class="badge">{escape(badge)}</div>' if badge else "") + (f'<div class="why">{escape(note)}</div>' if note else "")
    return (
        f'<div class="fc-tile{icon}">{_season(player)}{rank_html}'
        f'<div class="ph">{photo(player)}</div>'
        f'<div class="nm">{escape(player["name"])}</div>'
        f'<div class="sub">{escape(sub)}</div><div class="kpi">{kpi}</div>{extra}</div>'
    )


def person_tile(name: str, pid: int, card_count: int) -> str:
    """선수 고르기용 작은 카드 — 얼굴 · 이름 · 카드 수."""
    face = escape(f"https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/players/p{pid}.png")
    return (f'<div class="fc-tile fc-person"><div class="ph" style="height:72px;margin-top:4px">'
            f'<img src="{face}" alt="{escape(name)}" loading="lazy"></div>'
            f'<div class="nm">{escape(name)}</div><div class="sub">카드 {card_count}장</div></div>')


def player_card(player: dict) -> str:
    icon = " icon" if "ICON" in player.get("season", "") else ""
    pos = player["positions"][0] if player.get("positions") else ""
    pos_html = f'<div class="pos">{escape(pos)}</div>' if pos else ""
    return (
        f'<div class="fc-card{icon}">{_season(player)}{pos_html}'
        f'<div class="ph">{photo(player)}</div>'
        f'<div class="name">{escape(player["name"])}</div>'
        f'<div class="sub">{escape(player.get("season", ""))}</div></div>'
    )


def chips(items: list[tuple[str, str]]) -> str:
    return '<div class="fc-chips">' + "".join(
        f'<span class="fc-chip">{escape(k)} <b>{escape(v)}</b></span>' for k, v in items) + "</div>"


def ai_box(text_html: str, tag: str = "AI 스카우팅") -> str:
    return f'<div class="fc-ai"><span class="tag">🤖 {escape(tag)}</span>{text_html}</div>'


# ── 스탯 막대 (포지션 내 백분위) ──────────────────────────────────

def meters(player: dict, pct: dict[str, float]) -> str:
    values = stat_values(player.get("ranker"))
    if not values:
        return '<div class="fc-panel"><div class="title">랭커 기록</div><div class="fc-note">랭커 경기 기록이 없는 카드입니다.</div></div>'
    rows = []
    first = [k for k in KEY_STATS.get(player.get("position_group") or "", []) if k in values]   # 포지션 대표 스탯을 위로
    ordered = sorted(STATS, key=lambda s: (s[0] not in first, first.index(s[0]) if s[0] in first else 0))
    for key, name, unit in ordered:
        shown = f"{values[key]:.0f}%" if unit else f"{values[key]:.2f}"
        p = pct.get(key)
        width = p if p is not None else 0
        rank = f" <small>상위 {max(1, 100 - int(p))}%</small>" if p is not None else ""
        rows.append(f'<div class="fc-row" title="{escape(name)} {shown}{" · 같은 포지션 상위 " + str(max(1, 100 - int(p))) + "%" if p is not None else ""}">'
                    f'<div class="lb">{name}</div><div class="track"><div class="bar" style="width:{width:.0f}%"></div></div>'
                    f'<div class="vl">{shown}{rank}</div></div>')
    r = player["ranker"]
    group = escape(player.get("position_group") or "")
    note = (f'공식경기 {escape(r["position"])} · 최근 {r["match_count"]}경기 평균 · {escape(r["date"])} 집계. '
            f'막대 = 같은 포지션({group}) 카드 중 위치' if pct else f'공식경기 {escape(r["position"])} · 최근 {r["match_count"]}경기 평균')
    save_html = ""
    if player.get("position_group") == "골키퍼":
        rate, s = save_rate(player), player.get("gk_saves")
        if rate is not None:   # 막대(백분위)가 아니라 값만 — 비교할 골키퍼 표본이 적다
            save_html = (f'<div class="fc-row" title="선방률 {rate:.0f}%"><div class="lb">선방률</div>'
                         f'<div class="track"><div class="bar" style="width:{rate:.0f}%"></div></div>'
                         f'<div class="vl">{rate:.0f}%</div></div>')
            note += (f". 선방률 = 1 − 실점 ÷ 상대 유효슈팅 — 최근 30일 수집 경기 {s['games']}경기 "
                     f"(상대 유효슈팅 {s['on_target']} · 실점 {s['goals']}), 막대는 0~100%")
        else:
            note += (". 선방 기록은 넥슨 랭커 API 에 없어 경기 기록(🎮 내 기록)으로 모읍니다 — "
                     + ("아직 이 카드의 표본이 부족합니다" if s else "아직 이 카드의 경기 기록이 없습니다"))
    return (f'<div class="fc-panel"><div class="title">랭커 경기당 기록<small>막대가 길수록 같은 포지션에서 상위</small></div>'
            f'{save_html}{"".join(rows)}<div class="fc-note">{note}</div></div>')


def usage(rows: list[dict]) -> str:
    """랭커가 이 카드를 쓰는 포지션 — 한 계열(파랑) 가로 막대, 값은 글자로."""
    if not rows:
        return '<div class="fc-panel"><div class="title">랭커 포지션 사용</div><div class="fc-note">최근 랭커 경기 기록이 없습니다.</div></div>'
    top = rows[:7]
    most = max(r["match_count"] for r in top) or 1
    body = "".join(
        f'<div class="fc-row" title="{escape(r["position"])} · {r["match_count"]}경기 · 골 {r["goal"]:.2f}">'
        f'<div class="lb">{escape(r["position"])}</div><div class="track"><div class="bar" style="width:{100 * r["match_count"] / most:.0f}%"></div></div>'
        f'<div class="vl">{r["match_count"]}경기 <small>골 {r["goal"]:.1f}</small></div></div>'
        for r in top
    )
    return (f'<div class="fc-panel"><div class="title">랭커는 이 카드를 어디에 쓰나<small>최근 집계 · 포지션별 최대 20경기</small></div>'
            f'{body}<div class="fc-note">{escape(str(max(r["date"] for r in rows)))} 기준 공식경기 랭커 집계</div></div>')


# ── 두 선수 비교 차트 ──────────────────────────────────────────────

def stat_maxima(players: list[dict]) -> dict[str, float]:
    """막대 길이의 기준 — 전체 선수 중 최고값. 성공률은 100%."""
    maxima = {key: 0.0 for key, _, _ in STATS}
    for p in players:
        for key, value in stat_values(p.get("ranker")).items():
            maxima[key] = max(maxima[key], value)
    maxima.update(pass_rate=100.0, dribble_rate=100.0)
    return maxima


def compare_chart(a: dict, b: dict, maxima: dict[str, float], theme: str) -> str:
    """스탯마다 한 줄씩(작은 막대 묶음). 스탯마다 단위 · 크기가 달라 한 축에 겹치지 않는다. 앞선 쪽 값은 굵게."""
    t = THEMES.get(theme, THEMES["dark"])
    va, vb = stat_values(a.get("ranker")), stat_values(b.get("ranker"))
    rows = []
    for key, name, unit in STATS:
        lines = []
        for player, values, other, color in ((a, va, vb, t["s1"]), (b, vb, va, t["s2"])):
            if not values:
                lines.append('<div class="line"><span class="none">랭커 기록 없음</span></div>')
                continue
            value = values[key]
            width = 0 if not maxima[key] else max(1.5, 100 * value / maxima[key])
            shown = f"{value:.0f}%" if unit else f"{value:.2f}"
            lead = other and value > other[key]
            lines.append(
                f'<div class="line" title="{escape(player["name"])} · {name} {shown}">'
                f'<div class="cbar" style="width:calc({width:.1f}% - 70px);background:{color}"></div>'
                f'<span class="val">{"<b>" + shown + " ▲</b>" if lead else shown}</span></div>'
            )
        caption = "성공률" if unit else "경기당"
        rows.append(f'<div class="crow"><div class="stat">{name}<small>{caption}</small></div><div>{"".join(lines)}</div></div>')
    legend = (
        f'<div class="legend"><span><i style="background:{t["s1"]}"></i>{escape(a["name"])} · {escape(a["season_short"])}</span>'
        f'<span><i style="background:{t["s2"]}"></i>{escape(b["name"])} · {escape(b["season_short"])}</span></div>'
    )
    note = '<div class="fc-note">랭커 공식경기 평균 (최근 최대 20경기). 막대 길이는 전체 선수 중 최고값 대비. ▲ = 앞선 쪽.</div>'
    return f'<div class="fc-panel fc-compare">{legend}{"".join(rows)}{note}</div>'


# ── 전술: 경기장 ────────────────────────────────────────────────

def pitch(slots: list[str], squad: dict[str, dict | None], xy: dict[str, tuple[int, int]],
          grades: dict[str, int] | None = None) -> str:
    """포메이션 자리에 선수 얼굴 · 이름을 놓은 경기장. 빈 자리는 점선 원. grades 가 있으면 강화 등급(+n)도."""
    marks = []
    for slot in slots:
        x, y = xy.get(slot, (50, 50))
        p = squad.get(slot)
        if p:
            face = escape(p.get("face") or p.get("image") or "")
            grade = f" +{grades[slot]}" if grades and grades.get(slot) else ""
            body = (f'<img src="{face}" alt="{escape(p["name"])}">'
                    f'<div class="pn">{escape(p["name"])}</div><div class="ps">{escape(slot)} · {escape(p["season_short"])}{grade}</div>')
        else:
            body = f'<div class="empty">{escape(slot)}</div><div class="ps">비어 있음</div>'
        marks.append(f'<div class="slot" style="left:{x}%;top:{y}%">{body}</div>')
    return f'<div class="fc-pitch"><div class="box top"></div><div class="box bottom"></div><div class="mid"></div><div class="circle"></div>{"".join(marks)}</div>'


PITCH_CSS = """
<style>
.fc-pitch { position: relative; width: 100%; aspect-ratio: 0.78; border-radius: 18px; overflow: hidden;
  background: repeating-linear-gradient(180deg, #0f5132 0 9%, #0d4a2d 9% 18%); border: 2px solid rgba(255,255,255,.18); }
.fc-pitch .mid { position: absolute; left: 0; right: 0; top: 50%; border-top: 2px solid rgba(255,255,255,.25); }
.fc-pitch .circle { position: absolute; left: 50%; top: 50%; width: 22%; aspect-ratio: 1; transform: translate(-50%, -50%);
  border: 2px solid rgba(255,255,255,.25); border-radius: 50%; }
.fc-pitch .box { position: absolute; left: 25%; width: 50%; height: 14%; border: 2px solid rgba(255,255,255,.25); }
.fc-pitch .box.top { top: -2px; border-top: none; }
.fc-pitch .box.bottom { bottom: -2px; border-bottom: none; }
.fc-pitch .slot { position: absolute; transform: translate(-50%, -50%); text-align: center; width: 92px; }
.fc-pitch .slot img { width: 52px; height: 52px; border-radius: 50%; object-fit: cover; background: #0b1426;
  border: 2px solid #16e08a; box-shadow: 0 4px 10px rgba(0,0,0,.4); }
.fc-pitch .slot .empty { width: 48px; height: 48px; margin: 0 auto; border-radius: 50%; border: 2px dashed rgba(255,255,255,.45);
  color: #dfe6f3; font-size: 11px; font-weight: 800; line-height: 44px; }
.fc-pitch .pn { font-size: 12px; font-weight: 800; color: #fff; text-shadow: 0 1px 3px rgba(0,0,0,.8);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fc-pitch .ps { font-size: 10px; color: #cfe9dc; text-shadow: 0 1px 2px rgba(0,0,0,.8); }
</style>
"""


# ── 전적 ───────────────────────────────────────────────────────

def form_strip(matches: list[dict]) -> str:
    return '<div class="fc-form">' + "".join(
        f'<span style="background:{STATUS.get(m["result"], "#555")}" title="{escape(m["date"])} {escape(m["score"])} vs {escape(m["opponent"])}">'
        f'{escape(m["result"])}</span>' for m in matches) + "</div>"


def delta(mine: float, ranker: float | None, unit: str = "") -> str:
    """내 값과 랭커 평균의 차이 — 화살표 + 숫자로 (색만으로 전하지 않는다)."""
    if ranker is None:
        return "—"
    diff = mine - ranker
    fmt = f"{abs(diff):.0f}{unit}" if unit else f"{abs(diff):.2f}"
    if abs(diff) < (1 if unit else 0.05):
        return f"= {fmt}"
    return f'<span class="fc-up">▲ {fmt}</span>' if diff > 0 else f'<span class="fc-down">▼ {fmt}</span>'
