"""UI 영역의 화면 부품 — 스타일, 선수 카드 · 타일, 스탯 막대, 포지션 사용, 비교 차트, 전적. (F2009 · F2010)

모든 사용자 데이터(선수 이름 · 소개 · 닉네임)는 html.escape 로 감싸 HTML 에 넣는다.
색: 화면은 .streamlit/config.toml 의 다크 테마. 차트 계열색은 검증한 범주형 두 칸(파랑 · 주황),
상태(승 · 무 · 패)는 상태색 + 글자로 — 색만으로 뜻을 전하지 않는다.
"""

from __future__ import annotations

from html import escape

from data_prep.datacenter import ABILITY_GROUPS
from data_prep.enhance import GRADE_OVR
from data_prep.players import KEY_STATS, STATS, key_stat_line, save_rate, stat_values

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "s1": "#2a78d6", "s2": "#eb6834", "border": "rgba(11,11,11,0.10)", "track": "#efeee9",
        "raise": "#ffffff", "raise_hover": "#f3f5f4", "edge": "rgba(11,11,11,0.22)", "faint": "rgba(11,11,11,0.13)",
    },
    "dark": {
        "surface": "#141c2e", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#25304a", "s1": "#3987e5", "s2": "#d95926", "border": "rgba(255,255,255,0.08)", "track": "#22304a",
        # 버튼 면 · 테두리 — 바탕(#0a0f1c)보다 한 단계 밝게, 테두리는 또렷하게 (바탕과 비슷해 눈에 안 띈다는 의견)
        "raise": "#18223a", "raise_hover": "#1f2c49", "edge": "#3a4c74", "faint": "rgba(255,255,255,0.14)",
    },
}
CARD_CSS = """
/* 시즌 카드 그림 — 시즌별 카드 배경 + 선수 (공식 스쿼드 메이커처럼). 크기는 감싼 칸의 너비를 따른다 */
/* 시즌마다 카드 몸통 크기가 달라, 몸통 칸(--x0 · --y0 · --w · --h, 배경 그림에서 잰 비율)에 맞춰 선수 · OVR 을 놓는다 */
.fc-cv { --x0: .19; --y0: .1; --w: .62; --h: .79; position: relative; width: 100%; aspect-ratio: 340 / 400;
  background: center / contain no-repeat; container-type: inline-size; }
.fc-cv.nobg { background: radial-gradient(120% 90% at 50% 0%, #2b4a7d 0%, #142441 60%, #0b1426 100%); border-radius: 12%; }
.fc-cv .fig { position: absolute; left: calc(var(--x0) * 100% + var(--w) * 24%); width: calc(var(--w) * 78%);
  top: calc(var(--y0) * 100% + var(--h) * 6%); height: calc(var(--h) * 72%); display: flex; align-items: flex-end;
  justify-content: center; }
.fc-cv .fig img, .fc-cv .fig object { max-width: 100%; max-height: 100%; filter: drop-shadow(0 6px 10px rgba(0,0,0,.45)); }
.fc-cv .ov { position: absolute; left: calc(var(--x0) * 100% + var(--w) * 2%); width: calc(var(--w) * 30%);
  top: calc(var(--y0) * 100% + var(--h) * 9%); display: flex; flex-direction: column; align-items: center; gap: 1.2cqw;
  line-height: 1; z-index: 1; text-shadow: 0 0 3px rgba(0,0,0,.9), 0 1px 3px rgba(0,0,0,.7); }
.fc-cv .ov b { font-size: calc(var(--w) * 24cqw); font-weight: 900; color: #fff; letter-spacing: -.02em; }
.fc-cv .ov span { font-size: calc(var(--w) * 15cqw); font-weight: 900; }
/* 밝은 카드(ICON · ICON TM 등)는 OVR 을 어둡게 — 흰 배경에 흰 글자가 묻히지 않게 */
.fc-cv.light .ov { text-shadow: 0 0 3px rgba(255,255,255,.85); }
.fc-cv.light .ov b { color: #1d1608; }
.fc-cv.light .ov span.fw { color: #c8102e; } .fc-cv.light .ov span.mf { color: #0b7a45; }
.fc-cv.light .ov span.df { color: #1557b0; } .fc-cv.light .ov span.gk { color: #a35f00; }
.fc-cv .gr { position: absolute; right: calc((1 - var(--x0) - var(--w)) * 100% + var(--w) * 6%);
  top: calc(var(--y0) * 100% + var(--h) * 7%); z-index: 1; }

/* 포지션 · 급여 · 강화 표시 (공식 사이트 색) */
.fc-pos { display: inline-block; padding: 1px 7px; border-radius: 7px; font-size: 11px; font-weight: 900; letter-spacing: .02em;
  border: 1px solid currentColor; line-height: 1.5; }
.fc-pos.fw, .fc-cv .ov span.fw { color: #ff5d6c; } .fc-pos.mf, .fc-cv .ov span.mf { color: #2fd389; }
.fc-pos.df, .fc-cv .ov span.df { color: #4aa3ff; } .fc-pos.gk, .fc-cv .ov span.gk { color: #f5b83d; }
.fc-pay { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 26px; font-size: 11px;
  font-weight: 900; color: #fff; background: #12937a; clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%); }
.fc-grade { display: inline-block; padding: 0 6px; border-radius: 5px; font-size: 11px; font-weight: 900; line-height: 18px; }
/* 강화 색 — 공식 데이터센터와 같다 (1 회색 · 2~4 동 · 5~7 은 · 8~10 금 · 11~13 백금) */
.fc-grade.g1, .pc .gd.g1 { color: #c5c8c9; background: linear-gradient(140deg, #51545a 0%, #42464d 100%); border: 1px solid #393a3c; }
.fc-grade.g2, .pc .gd.g2 { color: #7e3f27; background: linear-gradient(140deg, #de946b 0%, #ad5f42 100%); border: 1px solid #864229; }
.fc-grade.g5, .pc .gd.g5 { color: #4e545e; background: linear-gradient(140deg, #d8d9dc 0%, #b8bdca 100%); border: 1px solid #a5a8ae; }
.fc-grade.g8, .pc .gd.g8 { color: #695100; background: linear-gradient(140deg, #f9dd62 0%, #dca908 100%); border: 1px solid #cda000; }
.fc-grade.g11, .pc .gd.g11 { color: #2d2b43; background: linear-gradient(140deg, #f4f6ff 0%, #c9d2f2 45%, #9fb0e6 100%); border: 1px solid #5274c0; }


/* 경기장 자리의 카드 — 왼쪽 위 한 칸에 OVR · 포지션 · 급여 · 강화를 차례로 쌓는다 (공식 카드의 playerCardInfoSide 처럼).
   강화를 따로 고정 위치에 두면 급여 육각형이 길어질 때 겹친다 — 같은 칸에 쌓아 겹칠 수 없게 하고, 크기는 이름(아래 77%)에
   닿지 않게 줄였다. 보유 신규 특성(오른쪽 위), 국기 · 이름(아래), 카드 밑에 시세 */
.pc { width: 100%; }
.pc .fc-cv .ov { top: calc(var(--y0) * 100% + var(--h) * 6%); gap: .8cqw; }
.pc .fc-cv .ov b { font-size: calc(var(--w) * 26cqw); }
.pc .fc-cv .ov span { font-size: calc(var(--w) * 15cqw); }
.pc .fc-cv .ov .fc-pay { width: calc(var(--w) * 22cqw); height: calc(var(--w) * 24cqw); font-size: calc(var(--w) * 13cqw);
  color: #e9eef6; background: rgba(40,46,58,.85); text-shadow: none; }
.pc .fc-cv.light .ov .fc-pay { color: #fff; background: #6b6f78; }
.pc .fc-cv .ov small { font-size: calc(var(--w) * 14cqw); font-weight: 800; color: #ffd166; }
.pc .tr { position: absolute; z-index: 1; right: calc((1 - var(--x0) - var(--w)) * 100% + var(--w) * 4%);
  top: calc(var(--y0) * 100% + var(--h) * 4%); width: calc(var(--w) * 28%); filter: drop-shadow(0 1px 2px rgba(0,0,0,.7)); }
.pc .gd { min-width: calc(var(--w) * 20cqw); padding: .3cqw 1cqw; border-radius: 3px; font-size: calc(var(--w) * 14cqw);
  font-weight: 900; font-style: normal; line-height: 1.2; text-align: center; text-shadow: none;
  box-shadow: 0 1px 3px rgba(0,0,0,.5); }
.pc .nm { position: absolute; z-index: 1; left: calc(var(--x0) * 100% + var(--w) * 4%); width: calc(var(--w) * 92%);
  top: calc(var(--y0) * 100% + var(--h) * 77%); font-size: calc(var(--w) * 16cqw); font-weight: 800; line-height: 1.15;
  text-align: center; color: #fff; text-shadow: 0 1px 2px #000, 0 0 3px #000; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.pc .fl { position: absolute; z-index: 1; left: calc(var(--x0) * 100%); width: calc(var(--w) * 100%);
  top: calc(var(--y0) * 100% + var(--h) * 87%); display: flex; justify-content: center; }
.pc .fl img { height: calc(var(--w) * 13cqw); box-shadow: 0 0 2px rgba(0,0,0,.6); }
.pc .fc-cv.light .nm { color: #1d1608; text-shadow: 0 0 3px rgba(255,255,255,.9); }
.pc .price { margin-top: 1px; font-size: 10.5px; font-weight: 700; color: #e6ecf5; text-shadow: 0 1px 2px #000; white-space: nowrap; }
.pc .price b { color: #ffd166; }
"""
STATUS = {"승": "#0ca30c", "무": "#898781", "패": "#d03b3b"}
ACCENT = "#16e08a"


def css(theme: str) -> str:
    t = THEMES.get(theme, THEMES["dark"])
    return f"""
<style>
.block-container {{ padding-top: 1.2rem; max-width: 1280px; }}
h1, h2, h3 {{ letter-spacing: -0.02em; }}
a {{ color: {ACCENT}; }}

/* 브라우저 방문 기록을 맞추는 보이지 않는 칸 (views/common.nav_bridge) */
.st-key-nav_bridge_box {{ display: none; }}

/* 상단 바 — 로고는 버튼(누르면 홈) */
.fc-logo {{ font-size: 21px; font-weight: 900; letter-spacing: .02em; color: {ACCENT}; white-space: nowrap; text-align: center;
  line-height: 40px; user-select: none; }}
.st-key-nav_brand_box {{ position: relative; gap: 0; }}
.st-key-nav_brand_box [data-testid="stElementContainer"]:has(.stButton) {{ position: absolute; inset: 0; z-index: 3; margin: 0; }}
.st-key-nav_brand_box .stButton, .st-key-nav_brand_box .stButton button {{ width: 100%; height: 100%; min-height: 0; }}
.st-key-nav_brand_box .stButton button {{ opacity: 0; cursor: pointer; }}
.st-key-nav_brand_box:has(button:focus-visible) .fc-logo {{ text-decoration: underline; text-underline-offset: 6px; }}
/* 메뉴 버튼 — 옅은 테두리로 바탕과 구분, 올리면 초록 테두리 (지금 화면은 초록 면) */
.st-key-navbar button[data-testid="stBaseButton-tertiary"] {{ border: 1px solid {t['faint']}; border-radius: 10px; }}
.st-key-navbar button[data-testid="stBaseButton-tertiary"]:hover {{ border-color: {ACCENT}; background: rgba(127,127,127,.10); }}
.st-key-nav_back button p {{ font-size: 18px; font-weight: 800; }}

/* 카드 전체를 누르는 타일 — 타일 위에 투명 버튼을 겹친다 (키보드로 가면 테두리가 보인다) */
div[class*="st-key-tile_"] {{ position: relative; gap: 4px; }}
div[class*="st-key-tile_"] [data-testid="stElementContainer"]:has(.stButton) {{ position: absolute; inset: 0; z-index: 3; margin: 0; }}
div[class*="st-key-tile_"] .stButton, div[class*="st-key-tile_"] .stButton button {{ width: 100%; height: 100%; }}
div[class*="st-key-tile_"] .stButton button {{ opacity: 0; cursor: pointer; }}
div[class*="st-key-tile_"] .stButton button:focus-visible {{ opacity: 1; background: transparent; color: transparent;
  border: 2px solid {ACCENT}; }}
div[class*="st-key-tile_"]:hover .fc-tile {{ transform: translateY(-2px); border-color: rgba(22,224,138,.55); }}

/* 버튼 · 칩 — 바탕과 구분되게 한 단계 밝은 면 + 또렷한 테두리, 올리면 초록 테두리 */
button[data-testid="stBaseButton-secondary"], button[data-testid="stPopoverButton"],
button[data-variant="pills"][aria-checked="false"] {{
  background: {t['raise']}; border: 1px solid {t['edge']}; color: {t['ink']}; }}
button[data-testid="stBaseButton-secondary"]:hover:not(:disabled), button[data-testid="stPopoverButton"]:hover,
button[data-variant="pills"][aria-checked="false"]:hover {{
  background: {t['raise_hover']}; border-color: {ACCENT}; color: {t['ink']}; }}
button[data-testid="stBaseButton-secondary"]:disabled {{ opacity: .45; }}
button[data-testid="stBaseButton-primary"] {{ box-shadow: 0 4px 14px rgba(22,224,138,.22); }}
button[data-variant="pills"][aria-checked="true"] {{ font-weight: 700; }}
/* 글자 버튼(상단 메뉴 · 추천 검색어) — 올리면 옅은 면, 추천 검색어는 칩 모양으로 */
button[data-testid="stBaseButton-tertiary"]:hover {{ background: rgba(127,127,127,.12); }}
.st-key-wrap_chips button[data-testid="stBaseButton-tertiary"] {{ background: {t['raise']}; border: 1px solid {t['edge']};
  border-radius: 999px; padding: 4px 12px; }}
.st-key-wrap_chips button[data-testid="stBaseButton-tertiary"]:hover {{ border-color: {ACCENT}; }}

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

/* 다른 시즌 카드 넘기기 — 다음은 오른쪽에서, 이전은 왼쪽에서 밀려 들어온다 (views/player.py 의 slide_* 칸) */
@keyframes fc-from-right {{ from {{ opacity: 0; transform: translateX(56px); }} to {{ opacity: 1; transform: none; }} }}
@keyframes fc-from-left {{ from {{ opacity: 0; transform: translateX(-56px); }} to {{ opacity: 1; transform: none; }} }}
div[class*="st-key-slide_next_"] {{ animation: fc-from-right .38s ease-out; }}
div[class*="st-key-slide_prev_"] {{ animation: fc-from-left .38s ease-out; }}
.fc-dots {{ text-align: center; letter-spacing: 6px; color: {ACCENT}; font-size: 12px; }}

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

{CARD_CSS}

/* 선수 줄 — 카드 그림 · 포지션 · 시즌 · 이름 · 급여 · 강화 / 시즌 이름 · 주발 / 국적 · 팀컬러 / 능력치 · 특성 · OVR */
.fc-prow {{ display: grid; grid-template-columns: 74px minmax(0, 1fr) auto; gap: 14px; align-items: center;
  padding: 10px 16px 10px 10px; border-radius: 16px; background: {t['surface']}; border: 1px solid {t['border']};
  margin-bottom: 8px; word-break: keep-all; overflow-wrap: anywhere; line-height: 1.45; }}
.fc-prow .l1 {{ display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }}
.fc-prow .l1 .ssn {{ height: 17px; }}
.fc-prow .l1 .nm {{ font-size: 15.5px; font-weight: 800; color: {t['ink']}; }}
.fc-prow .l2 {{ font-size: 12.5px; color: {t['ink2']}; margin-top: 3px; }}
.fc-prow .l2 i {{ font-style: normal; color: {t['muted']}; margin: 0 6px; }}
.fc-prow .foot b {{ color: {t['ink']}; }}
.fc-prow .l3 {{ display: flex; align-items: center; gap: 6px; margin-top: 5px; flex-wrap: wrap; min-height: 18px; }}
.fc-prow .l3 img {{ height: 18px; max-width: 26px; object-fit: contain; }}
.fc-prow .l4 {{ display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: 12px; color: {t['ink2']}; margin-top: 4px;
  font-variant-numeric: tabular-nums; }}
.fc-prow .l4 span {{ white-space: nowrap; }}
.fc-prow .l4 b {{ color: {t['ink']}; font-weight: 700; }}
.fc-prow .note {{ font-size: 12px; color: {t['ink2']}; margin-top: 4px; }}
.fc-prow .side {{ display: flex; align-items: center; gap: 12px; }}
.fc-prow .traits {{ display: flex; align-items: center; gap: 4px; }}
.fc-prow .traits img {{ width: 30px; height: 30px; padding: 3px; border-radius: 8px; background: {t['raise']};
  border: 1px solid {t['edge']}; box-sizing: border-box; }}
.fc-prow .traits img.new {{ border-color: #f2c94c; }}
.fc-prow .traits small {{ font-size: 11px; color: {t['muted']}; }}
.fc-prow .ovr {{ font-size: 30px; font-weight: 900; color: #ff6b6b; min-width: 54px; text-align: right;
  font-variant-numeric: tabular-nums; }}
.fc-prow .ovr small {{ display: block; font-size: 10.5px; font-weight: 700; color: {t['muted']}; text-align: right; }}
.fc-prow .wait {{ font-size: 11.5px; color: {t['muted']}; }}
@media (max-width: 760px) {{ .fc-prow {{ grid-template-columns: 58px minmax(0, 1fr); }}
  .fc-prow .side {{ grid-column: 1 / -1; justify-content: space-between; }} }}
/* 좁은 칸용 (스쿼드 메이커 자리별 선수) — 오른쪽은 OVR 만, 특성은 아래 줄. 이름 한 줄 · 나머지는 덩어리째 줄바꿈 */
.fc-prow.compact {{ grid-template-columns: 56px minmax(0, 1fr) auto; gap: 10px; padding: 9px 12px 9px 8px; }}
.fc-prow.compact .l1 {{ flex-wrap: nowrap; min-width: 0; }}
.fc-prow.compact .l1 .nm {{ min-width: 0; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.fc-prow .meta {{ display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; margin-top: 4px; font-size: 12.5px;
  color: {t['ink2']}; }}
.fc-prow .meta > * {{ white-space: nowrap; }}
.fc-prow .meta .ss {{ display: inline-flex; align-items: center; gap: 4px; font-weight: 700; color: {t['ink']}; }}
.fc-prow .meta .ssn {{ height: 15px; }}
.fc-prow .meta .fc-pay {{ width: 22px; height: 24px; font-size: 10.5px; }}
.fc-prow.compact .traits {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }}
.fc-prow.compact .traits img {{ width: 24px; height: 24px; padding: 2px; }}
.fc-prow.compact .ovr {{ font-size: 26px; min-width: 46px; }}
@media (max-width: 760px) {{ .fc-prow.compact {{ grid-template-columns: 48px minmax(0, 1fr) auto; }}
  .fc-prow.compact .side {{ grid-column: auto; }} }}
div[class*="st-key-prow_"] {{ position: relative; gap: 0; }}
div[class*="st-key-prow_"] [data-testid="stElementContainer"]:has(.stButton) {{ position: absolute; inset: 0 0 8px 0; z-index: 3;
  margin: 0; }}
div[class*="st-key-prow_"] .stButton, div[class*="st-key-prow_"] .stButton button {{ width: 100%; height: 100%; }}
div[class*="st-key-prow_"] .stButton button {{ opacity: 0; cursor: pointer; }}
div[class*="st-key-prow_"] .stButton button:focus-visible {{ opacity: 1; background: transparent; color: transparent;
  border: 2px solid {ACCENT}; }}
div[class*="st-key-prow_"]:hover .fc-prow {{ border-color: rgba(22,224,138,.55); }}
.fc-tile .cvw {{ width: 104px; margin: 24px auto 2px; }}
.fc-tile .meta {{ display: flex; align-items: center; justify-content: center; gap: 7px; margin: 3px 0 1px;
  font-size: 12px; color: {t['ink2']}; }}
.fc-tile .meta .fc-pay {{ width: 22px; height: 24px; font-size: 10.5px; }}
.fc-tile .meta .fc-pay.wait {{ background: {t['grid']}; }}
.fc-tile .meta .foot b {{ color: #fff; }}
.fc-card .cvbig {{ width: min(240px, 86%); margin: 30px auto 4px; }}

/* 선수 특성 · 팀컬러 · 포지션별 OVR · 능력치 */
.fc-traits .fc-trait-row {{ display: grid; grid-template-columns: 44px 104px minmax(0, 1fr); gap: 8px; align-items: center;
  padding: 5px 0; border-top: 1px solid {t['grid']}; font-size: 13px; color: {t['ink']}; }}
.fc-traits .fc-trait-row:first-of-type {{ border-top: none; }}
.fc-traits .code {{ font-size: 11px; font-weight: 900; color: {ACCENT}; }}
.fc-trait {{ display: inline-flex; align-items: center; gap: 4px; margin: 2px 6px 2px 0; padding: 2px 8px 2px 3px;
  border-radius: 999px; background: {t['track']}; color: {t['ink']}; font-size: 12px; }}
.fc-trait img {{ width: 20px; height: 20px; }}
.fc-trait b {{ margin-left: 3px; font-size: 9.5px; font-weight: 900; color: #1b1405; background: #f2c94c; border-radius: 4px;
  padding: 0 4px; }}
.fc-trait.new {{ box-shadow: inset 0 0 0 1px #f2c94c; }}
.fc-trait.muted {{ color: {t['muted']}; padding-left: 8px; }}
.fc-chipwrap {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
.fc-chipwrap .fc-trait {{ margin: 0; min-height: 26px; box-sizing: border-box; }}
.fc-trait i.noimg {{ display: inline-block; width: 20px; height: 20px; border-radius: 50%; background: {t['grid']}; }}
.fc-stack {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 18px; }}
.fc-stack .fc-panel {{ margin: 0; }}
.fc-ovrgrid small.up, .fc-abil small.up {{ margin-left: 3px; font-size: 10.5px; font-weight: 700; color: {ACCENT}; }}
.fc-ovrgrid small.up {{ display: block; line-height: 1; }}
.fc-abil .it span {{ white-space: nowrap; }}
.fc-tc {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
.fc-tc .grp {{ border-radius: 12px; padding: 10px 12px; background: {t['track']}; }}
.fc-tc .head {{ display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 800; color: {t['ink2']}; }}
.fc-tc .head .cnt {{ margin-left: auto; font-size: 20px; font-weight: 900; color: {t['ink']}; }}
.fc-tc .best {{ display: flex; align-items: center; gap: 8px; margin-top: 8px; }}
.fc-tc .best img {{ width: 34px; height: 34px; object-fit: contain; }}
.fc-tc .best b {{ font-size: 13.5px; color: {t['ink']}; }}
.fc-tc .best small, .fc-tc .eff {{ display: block; font-size: 11.5px; color: {t['ink2']}; }}
.fc-tc .eff {{ margin-top: 4px; }}
.fc-tc .lv {{ display: inline-block; margin-left: 4px; padding: 0 6px; border-radius: 6px; font-size: 10.5px; font-weight: 900;
  color: #0b1426; background: {ACCENT}; }}
.fc-tc .none {{ margin-top: 8px; font-size: 12px; color: {t['muted']}; }}
.fc-tc .others {{ margin-top: 6px; font-size: 11px; color: {t['muted']}; }}
@media (max-width: 760px) {{ .fc-tc {{ grid-template-columns: 1fr; }} }}
.fc-ovrgrid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }}
.fc-ovrgrid div {{ border-radius: 10px; padding: 6px 4px; text-align: center; background: {t['track']}; }}
.fc-ovrgrid div span {{ display: block; font-size: 11px; font-weight: 800; }}
.fc-ovrgrid div b {{ font-size: 17px; color: {t['ink']}; font-variant-numeric: tabular-nums; }}
.fc-ovrgrid div.best {{ box-shadow: inset 0 0 0 2px {ACCENT}; }}
.fc-ovrgrid .empty {{ background: transparent; }}
.fc-abil {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px 18px; }}
.fc-abil .grp {{ grid-column: 1 / -1; margin-top: 6px; font-size: 12px; font-weight: 800; color: {t['ink2']}; }}
.fc-abil .it {{ display: flex; justify-content: space-between; font-size: 13px; color: {t['ink2']}; padding: 2px 0;
  border-bottom: 1px dashed {t['grid']}; }}
.fc-abil .it b {{ font-variant-numeric: tabular-nums; }}
.fc-abil .v1 {{ color: #8fb3ff; }} .fc-abil .v2 {{ color: #2fd389; }} .fc-abil .v3 {{ color: #f2c94c; }}
.fc-abil .v4 {{ color: #ff6b6b; }}
@media (max-width: 760px) {{ .fc-abil {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
.fc-sum {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }}
.fc-sum > div {{ flex: 1 1 120px; min-width: 0; border-radius: 12px; padding: 8px 12px; background: {t['track']}; }}
.fc-sum span {{ display: block; font-size: 11px; color: {t['muted']}; }}
.fc-sum b {{ font-size: 18px; color: {t['ink']}; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.fc-sum b.txt {{ font-size: 15px; }}
.fc-sum b .star-up {{ display: inline; font-size: inherit; color: {ACCENT}; }}   /* 강화로 더해진 개인기 별 — 초록 = 오른 만큼 */
.fc-sum small {{ font-size: 11px; color: {t['ink2']}; }}

/* 선수 상세의 Q&A · AI 리포트 · 근거 문서 — 이 서비스의 중심(근거 기반 질문 답변)이라 초록 테두리 상자로 눈에 띄게 */
.st-key-qa_zone {{ border: 1.5px solid rgba(22,224,138,.55); border-radius: 18px; padding: 14px 18px 16px;
  background: linear-gradient(180deg, rgba(22,224,138,.10) 0%, {t['surface']} 70%); box-shadow: 0 0 22px rgba(22,224,138,.12);
  margin: 6px 0 14px; }}
.fc-qa-head {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; font-size: 19px; font-weight: 900;
  color: {t['ink']}; }}
.fc-qa-head small {{ font-size: 12.5px; font-weight: 600; color: {t['ink2']}; }}
.fc-qa-head .tag {{ font-size: 11px; font-weight: 800; color: {ACCENT}; border: 1px solid rgba(22,224,138,.5);
  border-radius: 999px; padding: 1px 9px; }}
.st-key-qa_zone [data-baseweb="tab"] p {{ font-size: 15.5px; font-weight: 800; }}
.st-key-qa_zone [data-baseweb="tab"][aria-selected="true"] p {{ color: {ACCENT}; }}
.st-key-qa_zone .stButton button[data-testid="stBaseButton-secondary"] {{ border: 1.5px solid rgba(22,224,138,.45);
  background: {t['raise']}; }}
.st-key-qa_zone .stButton button[data-testid="stBaseButton-secondary"]:hover {{ border-color: {ACCENT};
  background: rgba(22,224,138,.14); }}
.st-key-qa_zone [data-testid="stChatInput"] {{ border: 1.5px solid {ACCENT}; border-radius: 14px;
  box-shadow: 0 0 0 4px rgba(22,224,138,.12); }}
.st-key-qa_zone [data-testid="stChatInput"] textarea {{ font-size: 15px; }}
.fc-gauge {{ height: 8px; border-radius: 4px; background: {t['grid']}; overflow: hidden; margin-top: 6px; }}
.fc-gauge i {{ display: block; height: 8px; background: {ACCENT}; }}
.fc-gauge.over i {{ background: #ff6b6b; }}

/* 강화 등급 버튼 (views/widgets.grade_buttons) — 등급마다 공식 색, 고른 것은 흰 테두리 · 나머지는 흐리게 */
.fc-gradelabel {{ font-size: 14px; line-height: 1.5; color: {t['ink']}; margin: 0; }}
div[class*="st-key-gbtn_"] button {{ min-height: 0; padding: 3px 9px; border-radius: 7px; font-weight: 900; }}
div[class*="st-key-gbtn_"] button p {{ font-size: 12.5px; font-weight: 900; }}
div[class*="st-key-gbtn_g1_"] button {{ color: #c5c8c9; background: linear-gradient(140deg, #51545a 0%, #42464d 100%); border: 1.5px solid #393a3c; }}
div[class*="st-key-gbtn_g2_"] button {{ color: #7e3f27; background: linear-gradient(140deg, #de946b 0%, #ad5f42 100%); border: 1.5px solid #864229; }}
div[class*="st-key-gbtn_g5_"] button {{ color: #4e545e; background: linear-gradient(140deg, #d8d9dc 0%, #b8bdca 100%); border: 1.5px solid #a5a8ae; }}
div[class*="st-key-gbtn_g8_"] button {{ color: #695100; background: linear-gradient(140deg, #f9dd62 0%, #dca908 100%); border: 1.5px solid #cda000; }}
div[class*="st-key-gbtn_g11_"] button {{ color: #2d2b43; background: linear-gradient(140deg, #f4f6ff 0%, #c9d2f2 45%, #9fb0e6 100%); border: 1.5px solid #5274c0; }}
div[class*="st-key-gbtn_"][class*="_off_"] button {{ opacity: .5; }}
div[class*="st-key-gbtn_"][class*="_off_"] button:hover {{ opacity: .85; }}
div[class*="st-key-gbtn_"][class*="_on_"] button {{ opacity: 1; box-shadow: 0 0 0 2px #ffffff, 0 0 12px rgba(255,255,255,.45); }}

/* 스쿼드 위 요약 (팀컬러 · 구단가치 · 주요 선수) · 경기장 아래 칩 (포메이션 · 소속 · 특성 팀컬러 · 급여) */
.fc-sqtop {{ display: flex; gap: 0; flex-wrap: wrap; border-radius: 14px; background: {t['surface']}; border: 1px solid {t['border']};
  margin-bottom: 12px; }}
.fc-sqtop > div {{ flex: 1 1 160px; padding: 10px 16px; border-left: 1px solid {t['border']}; min-width: 0; }}
.fc-sqtop > div:first-child {{ border-left: none; }}
.fc-sqtop span {{ display: block; font-size: 11.5px; color: #c2a5ff; font-weight: 700; }}
.fc-sqtop b {{ display: block; font-size: 16px; color: {t['ink']}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.fc-sqtop small {{ font-size: 11px; color: {t['muted']}; font-weight: 500; }}
.fc-sqchips {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 10px 0 4px; }}
.fc-sqchips span {{ padding: 3px 12px; border-radius: 999px; font-size: 13px; font-weight: 800; border: 1px solid; }}
.fc-sqchips .fm {{ color: #c2a5ff; border-color: rgba(194,165,255,.45); background: rgba(194,165,255,.10); }}
.fc-sqchips .tc {{ color: #6cb8ff; border-color: rgba(108,184,255,.45); background: rgba(108,184,255,.10); }}
.fc-sqchips .ft {{ color: #2fd389; border-color: rgba(47,211,137,.45); background: rgba(47,211,137,.10); }}
.fc-sqchips .en {{ color: #f2c94c; border-color: rgba(242,201,76,.45); background: rgba(242,201,76,.10); }}
.fc-sqchips .pay {{ color: {t['ink2']}; border-color: {t['border']}; background: {t['track']}; }}
.fc-sqchips .pay.over {{ color: #ff6b6b; border-color: rgba(255,107,107,.5); }}
.fc-sqchips .none {{ color: {t['muted']}; border-color: {t['border']}; font-weight: 600; }}
button[data-testid="stPopoverButton"] p {{ white-space: nowrap; }}

/* 칩 */
.fc-chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0 10px; }}
.fc-chip {{ font-size: 12px; padding: 3px 10px; border-radius: 999px; background: {t['track']}; color: {t['ink2']}; }}
.fc-chip b {{ color: {t['ink']}; }}

/* AI 한줄 스카우팅 */
.fc-ai {{ border-radius: 14px; padding: 12px 14px; margin: 8px 0 12px; font-size: 14.5px; line-height: 1.6;
  background: linear-gradient(90deg, rgba(22,224,138,.12), rgba(57,135,229,.10)); border: 1px solid rgba(22,224,138,.25); }}
.fc-ai .tag {{ font-size: 11px; font-weight: 800; color: {ACCENT}; letter-spacing: .04em; display: block; margin-bottom: 3px; }}

/* 선수 소개 — 길면 다섯 줄에서 접고 아래를 흐리게. '더 보기'는 <details> 의 열린 상태를 :has 로 읽는다 */
.fc-intro {{ margin: 0 0 12px; }}
.fc-intro .body, .fc-intro .body p {{ font-size: 14.5px; line-height: 1.75; color: {t['ink']}; }}
.fc-intro .body p {{ margin: 0 0 .4em; }}
.fc-intro.folded:not(:has(details[open])) .body {{ max-height: 8.75em; overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, #000 55%, transparent); mask-image: linear-gradient(180deg, #000 55%, transparent); }}
.fc-intro summary {{ display: inline-block; list-style: none; cursor: pointer; margin-top: 2px;
  font-size: 13px; font-weight: 700; color: {ACCENT}; }}
.fc-intro summary::-webkit-details-marker {{ display: none; }}
.fc-intro details[open] .more, .fc-intro details:not([open]) .less {{ display: none; }}

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

def _no_cache(url: str | None) -> str | None:
    return url or None


_resolve = _no_cache


def _no_frame(url: str | None) -> dict | None:
    return None


_frame = _no_frame


def use_card_frames(frame) -> None:
    """시즌 카드 배경 → {box: (x0, y0, w, h) 비율, light: OVR 자리가 밝은지} (services/images.ImageCache.frame)."""
    global _frame
    _frame = frame


def use_image_cache(resolve) -> None:
    """이미지 주소 → 받아 둔 사본 주소 (services/images.ImageCache.resolve). 앱이 켜질 때 boot() 가 끼운다.
    resolve 는 없는 이미지로 확인된 주소에 None 을 돌려준다."""
    global _resolve
    _resolve = resolve


def image(url: str | None) -> str:
    """화면에 넣을 이미지 주소 — 받아 둔 사본이 있으면 그 주소, 없는 이미지면 빈 문자열."""
    return _resolve(url) or ""


def photo(player: dict) -> str:
    """액션 이미지 → 없으면 얼굴 이미지. 받아 둔 사본이 있으면 그 주소로.
    아직 받아 보지 않은 액션 이미지는 <object> 로 감싸, 없을 때 얼굴 이미지가 대신 보이게 한다."""
    name = escape(player["name"])
    action, face = _resolve(player.get("image")), _resolve(player.get("face"))
    if not action and not face:
        return '<div style="font-size:64px;line-height:1">⚽</div>'
    if action and face and action != face and action.startswith("http"):
        return (f'<object data="{escape(action)}" type="image/png" aria-label="{name}">'
                f'<img src="{escape(face)}" alt="{name}"></object>')
    return f'<img src="{escape(action or face)}" alt="{name}" loading="lazy">'


CARD_BG = "https://ssl.nexon.com/s2/game/fc/online/obt/externalAssets/new/card/{code}.png"   # 시즌별 카드 배경
ACTION_HIGH = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersActionHigh/p{spid}.png"
POSITION_KIND = {**dict.fromkeys(("ST", "LS", "RS", "CF", "LF", "RF", "LW", "RW"), "fw"),
                 **dict.fromkeys(("CAM", "LAM", "RAM", "CM", "LCM", "RCM", "CDM", "LDM", "RDM", "LM", "RM"), "mf"),
                 **dict.fromkeys(("CB", "LCB", "RCB", "SW", "LB", "RB", "LWB", "RWB"), "df"), "GK": "gk"}
SUMMARY = ("스피드", "슛", "패스", "드리블", "수비", "피지컬")


def season_code(season_img: str | None) -> str:
    """'.../new/season/ICONTM.png' → 'ICONTM'."""
    return (season_img or "").rsplit("/", 1)[-1].split(".")[0]


def grade_tier(grade: int) -> str:
    """강화 색 묶음 — 1 회색(g1) · 2~4 동(g2) · 5~7 은(g5) · 8~10 금(g8) · 11~13 백금(g11)."""
    return "g11" if grade >= 11 else "g8" if grade >= 8 else "g5" if grade >= 5 else "g2" if grade >= 2 else "g1"


def grade_badge(grade: int | None) -> str:
    g = grade or 1
    return f'<span class="fc-grade {grade_tier(g)}">+{g}강</span>'


def position_badge(position: str | None) -> str:
    return f'<span class="fc-pos {POSITION_KIND.get(position or "", "mf")}">{escape(position)}</span>' if position else ""


def card_visual(player: dict, ovr: int | None = None, position: str | None = None, grade: int | None = None,
                high: bool = False, pay: int | None = None, inner: str = "", exact: bool = True, stack: str = "") -> str:
    """시즌 카드 배경 위에 선수 — 왼쪽 위에 OVR · 포지션(· 급여), 오른쪽 위에 강화. 배경 · 선수 이미지는 받아 둔 사본으로.
    inner 는 카드 안에 더 얹을 것 (경기장 카드의 특성 · 이름), stack 은 왼쪽 위 칸의 급여 밑에 이어 쌓을 것 (경기장 카드의 강화)."""
    code = season_code(player.get("season_img"))
    bg_url = CARD_BG.format(code=code) if code else None
    bg = image(bg_url) if code else ""
    frame = _frame(bg_url) if bg else None
    figure = photo(player)
    if high and player.get("spid"):
        sharp = image(ACTION_HIGH.format(spid=player["spid"]))
        if sharp and not sharp.startswith("http"):   # 받아 둔 고화질 이미지가 있을 때만 (없는 카드도 많다)
            figure = f'<img src="{escape(sharp)}" alt="{escape(player["name"])}">'
    overlay = ""
    if ovr or stack:
        kind = POSITION_KIND.get(position or "", "mf")
        overlay = ('<div class="ov">' + (f'<b>{ovr}{"" if exact else "<small>*</small>"}</b>' if ovr else "")
                   + (f'<span class="{kind}">{escape(position)}</span>' if position else "")
                   + (f'<i class="fc-pay" title="급여">{pay}</i>' if pay is not None else "") + stack + "</div>")
    if grade:
        overlay += f'<div class="gr">{grade_badge(grade)}</div>'
    box = ""
    if frame:
        x0, y0, w, h = frame["box"]
        box = f";--x0:{x0:.3f};--y0:{y0:.3f};--w:{w:.3f};--h:{h:.3f}"
    style = f' style="background-image:url({escape(bg)}){box}"' if bg else ""
    kind = "" if bg else " nobg"
    kind += " light" if frame and frame["light"] else ""
    return f'<div class="fc-cv{kind}"{style}><div class="fig">{figure}</div>{overlay}{inner}</div>'


def bp_short(value: int | None) -> str:
    """시세를 짧게 — 21.3억 · 9,720만 · 4,500 (경기장 카드 밑)."""
    if value is None:
        return "-"
    if value >= 10**8:
        eok = value / 10**8
        return f"{eok:,.0f}억" if eok >= 100 else f"{eok:.1f}".rstrip("0").rstrip(".") + "억"
    if value >= 10**4:
        return f"{value / 10**4:,.0f}만"
    return f"{value:,}"


def pitch_card(player: dict, code: str, ovr: int | None, exact: bool = True, grade: int | None = None,
               pay: int | None = None, trait: dict | None = None, flag: str = "", price: str = "") -> str:
    """경기장 자리의 선수 카드 (공식 스쿼드 메이커처럼) — 시즌 카드 위 왼쪽 칸에 그 자리 OVR · 포지션 · 급여 · 강화를 차례로,
    오른쪽 위 보유 신규 특성, 아래 국기 · 이름. 카드 밑에 시세 · 구단 가치 중 비율. trait = {name, icon}."""
    g = grade or 1
    flag_html = f'<img src="{escape(image(flag))}" alt="">' if flag and image(flag) else ""
    inner = (f'<img class="tr" src="{escape(image(trait["icon"]))}" alt="" title="보유 신규 특성: {escape(trait["name"])}">'
             if trait else "")
    inner += f'<div class="nm">{escape(player["name"])}</div>' + (f'<div class="fl">{flag_html}</div>' if flag_html else "")
    grade_html = f'<i class="gd {grade_tier(g)}" title="강화 {g}단계">{g}</i>'
    return (f'<div class="pc">{card_visual(player, ovr, code, pay=pay, inner=inner, exact=exact, stack=grade_html)}'
            f'<div class="price">{price}</div></div>')


def foot_text(foot: dict | None) -> str:
    """{"L": 5, "R": 3, "main": "L"} → 'L5 – R3' (주발은 굵게)."""
    if not foot:
        return ""

    def side(k: str) -> str:
        return f"<b>{k}{foot[k]}</b>" if foot.get("main") == k else f"{k}{foot[k]}"

    return f'<span class="foot">{side("L")} – {side("R")}</span>'


def card_row(player: dict, info: dict | None = None, *, grade: int | None = None, ovr: int | None = None,
             position: str | None = None, pay: int | None = None, foot: dict | None = None,
             crests: list[tuple[str, str]] = (), exact: bool = True, note: str = "", ovr_label: str = "",
             wait_text: str = "국적 · 팀컬러 불러오는 중…", compact: bool = False) -> str:
    """선수 줄 (공식 스쿼드 메이커의 선수 목록처럼) — 카드 그림 · 포지션 · 시즌 · 이름 · 급여 · 강화 / 시즌 이름 · 주발 /
    국적 · 팀컬러 / 요약 능력치(강화 반영) / 특성 · OVR. info(카드 상세)가 아직 없으면 그 칸은 '불러오는 중'.

    compact = 좁은 칸(스쿼드 메이커의 자리별 선수 · AI 추천 결과)용 — 오른쪽에는 OVR 만 두고 특성은 아래 줄로 내려,
    가운데 글자 칸이 넓어진다. 이름은 한 줄(넘치면 …), 시즌 · 급여 · 강화 · 주발은 덩어리째 줄을 바꾼다."""
    info = info or {}
    grade = grade or 1
    position = position or info.get("position") or ""
    pay = pay if pay is not None else info.get("pay")
    season_icon = image(player.get("season_img"))
    season_img = f'<img class="ssn" src="{escape(season_icon)}" alt="">' if season_icon else ""
    pay_html = f'<span class="fc-pay" title="급여">{pay}</span>' if pay is not None else ""
    name_html = f'<span class="nm" title="{escape(player["name"])}">{escape(player["name"])}</span>'
    foot_html = foot_text(foot or info.get("foot_lr"))
    icons = "".join(f'<img src="{escape(image(url))}" alt="" title="{escape(name)}">' for name, url in crests if image(url))
    l3 = icons or (f'<span class="wait">{escape(wait_text)}</span>' if not info and wait_text else "")
    summary = info.get("summary") or {}
    bonus = GRADE_OVR[grade]
    # 능력치 하나('속력 120')는 한 덩어리 — 줄을 바꿔도 이름과 숫자가 갈라지지 않게
    l4 = "".join(f"<span>{k} <b>{v + bonus}</b></span>" for k, v in summary.items() if k in SUMMARY)
    traits = info.get("traits") or []
    trait_html = "".join(f'<img class="{"new" if t["new"] else ""}" src="{escape(image(t["icon"]))}" alt="{escape(t["name"])}" '
                         f'title="{escape(t["name"])}{" (신규 특성)" if t["new"] else ""}">' for t in traits[:5])
    if len(traits) > 5:
        trait_html += f"<small>+{len(traits) - 5}</small>"
    shown = "-" if ovr is None else f"{ovr}{'' if exact else '*'}"
    label = f"<small>{escape(ovr_label)}</small>" if ovr_label else ""
    note_html = f'<div class="note">{escape(note)}</div>' if note else ""
    if compact:
        meta = (f'<span class="ss" title="{escape(player.get("season", ""))}">{season_img}{escape(player.get("season_short", ""))}</span>'
                + pay_html + grade_badge(grade) + foot_html)
        return (f'<div class="fc-prow compact"><div>{card_visual(player)}</div><div>'
                f'<div class="l1">{position_badge(position)}{name_html}</div><div class="meta">{meta}</div>'
                + (f'<div class="l3">{l3}</div>' if l3 else "") + (f'<div class="l4">{l4}</div>' if l4 else "")
                + (f'<div class="traits">{trait_html}</div>' if trait_html else "") + note_html
                + f'</div><div class="side"><div class="ovr">{shown}{label}</div></div></div>')
    l1 = position_badge(position) + season_img + name_html + pay_html + grade_badge(grade)
    l2 = escape(player.get("season", "")) + (f"<i>·</i>{foot_html}" if foot_html else "")
    return (f'<div class="fc-prow"><div>{card_visual(player)}</div><div>'
            f'<div class="l1">{l1}</div><div class="l2">{l2}</div><div class="l3">{l3}</div>'
            + (f'<div class="l4">{l4}</div>' if l4 else "") + note_html
            + f'</div><div class="side"><div class="traits">{trait_html}</div><div class="ovr">{shown}{label}</div></div></div>')


def _tier(value: int) -> str:
    return "v4" if value >= 130 else "v3" if value >= 115 else "v2" if value >= 100 else "v1" if value >= 80 else ""


def _up(value: int, base: int | None) -> str:
    return f'<small class="up">+{value - base}</small>' if base is not None and value > base else ""


def ovr_grid(position_ovr: dict[str, int], order: tuple[str, ...], bonus: int = 0, base: dict[str, int] | None = None) -> str:
    """포지션별 OVR 16칸 (데이터센터 선수 정보 화면 순서) — bonus 를 더해 보이고, base 보다 오른 만큼을 작게 (선수 상세는
    강화 · 적응도 · 소속/특성 팀컬러까지 넣은 값을 base 로 넘겨, 초록 +N 은 강화 팀컬러로 오른 만큼만).
    가장 높은 포지션은 테두리."""
    if not position_ovr:
        return '<div class="fc-note">포지션별 OVR 을 아직 받지 못했습니다.</div>'
    best = max(position_ovr.values())
    cells = "".join(
        f'<div class="{"best" if position_ovr.get(pos) == best else ""}"><span class="fc-pos {POSITION_KIND.get(pos, "mf")}" '
        f'style="border:none;padding:0">{pos}</span><b>{position_ovr[pos] + bonus}</b>'
        f'{_up(position_ovr[pos] + bonus, (base or {}).get(pos))}</div>'
        if pos in position_ovr else '<div class="empty"></div>' for pos in order)
    return f'<div class="fc-ovrgrid">{cells}</div>'


def ability_table(stats: dict[str, int], summary: dict[str, int], bonus: int = 0, base: dict[str, int] | None = None) -> str:
    """세부 능력치 34개를 묶음별로 (요약 값과 함께) — bonus 를 모든 능력치에 더하고, base 보다 오른 만큼을 작게 (ovr_grid 와 같다)."""
    parts = []
    for group, names in ABILITY_GROUPS.items():
        head = f" <b>{summary[group] + bonus}</b>" if group in summary else ""
        parts.append(f'<div class="grp">{group}{head}</div>')
        for name in names:
            if name in stats:
                value = stats[name] + bonus
                parts.append(f'<div class="it">{escape(name)}<span><b class="{_tier(value)}">{value}</b>'
                             f'{_up(value, (base or {}).get(name))}</span></div>')
    return f'<div class="fc-abil">{"".join(parts)}</div>'


def trait_chips(traits: list[dict]) -> str:
    if not traits:
        return '<span class="fc-trait muted">특성 없음</span>'
    return '<div class="fc-chipwrap">' + "".join(
        f'<span class="fc-trait{" new" if t["new"] else ""}" title="{"신규 특성" if t["new"] else "일반 특성"}">'
        f'<img src="{escape(image(t["icon"]))}" alt="">{escape(t["name"])}{"<b>NEW</b>" if t["new"] else ""}</span>'
        for t in sorted(traits, key=lambda t: not t["new"])) + "</div>"


def club_history(clubs: list[dict]) -> str:
    if not clubs:
        return '<div class="fc-note">클럽 경력 정보가 없습니다.</div>'
    rows = "".join(f'<div class="it"><span>{escape(c["years"])}</span><b>{escape(c["club"])}'
                   f'{" <small>(임대)</small>" if c.get("loan") else ""}</b></div>' for c in clubs)
    return f'<div class="fc-abil" style="grid-template-columns:1fr">{rows}</div>'


def teamcolor_list(items: list[tuple[str, str, str]]) -> str:
    """[(그룹, 이름, 엠블럼 주소)] → 엠블럼 칩."""
    if not items:
        return '<div class="fc-note">들어가는 팀컬러가 없습니다.</div>'
    return '<div class="fc-chipwrap">' + "".join(
        f'<span class="fc-trait" title="{escape(group)} 팀컬러">'
        + (f'<img src="{escape(image(icon))}" alt="">' if icon and image(icon) else '<i class="noimg"></i>')
        + f'{escape(name)}<small style="margin-left:4px;opacity:.7">{escape(group)}</small></span>'
        for group, name, icon in items) + "</div>"


def _season(player: dict) -> str:
    icon = image(player.get("season_img"))
    icon_html = f'<img src="{escape(icon)}" alt="">' if icon else ""
    return f'<div class="season">{icon_html}{escape(player["season_short"])}</div>'


# ── 타일 · 카드 ─────────────────────────────────────────────────

def tile(player: dict, rank: int | None = None, kpi: str | None = None, badge: str = "", note: str = "",
         ovr: int | None = None, position: str | None = None, pay: int | None = None, foot: dict | None = None) -> str:
    """카드 타일 — 시즌 카드 그림(OVR · 포지션) · 이름 · 급여 · 주발/약발. badge(예: 랭킹 순위)와 note(예: AI 추천 이유)는
    카드 안 아래쪽에."""
    icon = " icon" if "ICON" in player.get("season", "") else ""
    if kpi is None:   # 포지션에 맞는 대표 스탯 (공격 골 · 도움 / 수비 태클 · 블록 / 골키퍼 선방률)
        kpi = key_stat_line(player) or ("랭커 기록 불러오기 전" if player.get("lite") else "랭커 기록 없음")
    sub = player.get("position_group") or ("카드 정보" if player.get("lite") else "포지션 기록 없음")
    rank_html = f'<div class="rank">#{rank}</div>' if rank else ""
    extra = (f'<div class="badge">{escape(badge)}</div>' if badge else "") + (f'<div class="why">{escape(note)}</div>' if note else "")
    return (
        f'<div class="fc-tile{icon}">{_season(player)}{rank_html}'
        f'<div class="cvw">{card_visual(player, ovr, position)}</div>'
        f'<div class="nm">{escape(player["name"])}</div>{_tile_meta(pay, foot)}'
        f'<div class="sub">{escape(sub)}</div><div class="kpi">{kpi}</div>{extra}</div>'
    )


def _tile_meta(pay: int | None, foot: dict | None) -> str:
    """타일의 급여(육각형) · 주발 – 약발 한 줄 — 아직 모르면 '…'."""
    pay_html = f'<span class="fc-pay" title="급여">{pay}</span>' if pay is not None else '<span class="fc-pay wait">…</span>'
    foot_html = foot_text(foot) if foot else '<span class="foot">L? – R?</span>'
    return f'<div class="meta">{pay_html}{foot_html}</div>'


def person_tile(name: str, pid: int, card_count: int) -> str:
    """선수 고르기용 작은 카드 — 얼굴 · 이름 · 카드 수."""
    face = escape(image(f"https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/players/p{pid}.png"))
    return (f'<div class="fc-tile fc-person"><div class="ph" style="height:72px;margin-top:4px">'
            f'<img src="{face}" alt="{escape(name)}" loading="lazy"></div>'
            f'<div class="nm">{escape(name)}</div><div class="sub">카드 {card_count}장</div></div>')


def player_card(player: dict, ovr: int | None = None, position: str | None = None) -> str:
    """큰 선수 카드 — 시즌 카드 배경 위에 선수(고화질 이미지가 있으면 그것) · OVR · 포지션, 아래에 이름 · 시즌."""
    icon = " icon" if "ICON" in player.get("season", "") else ""
    return (
        f'<div class="fc-card{icon}">{_season(player)}'
        f'<div class="cvbig">{card_visual(player, ovr, position, high=True)}</div>'
        f'<div class="name">{escape(player["name"])}</div>'
        f'<div class="sub">{escape(player.get("season", ""))}</div></div>'
    )


def chips(items: list[tuple[str, str]]) -> str:
    return '<div class="fc-chips">' + "".join(
        f'<span class="fc-chip">{escape(k)} <b>{escape(v)}</b></span>' for k, v in items) + "</div>"


def ai_box(text_html: str, tag: str = "AI 스카우팅") -> str:
    return f'<div class="fc-ai"><span class="tag">🤖 {escape(tag)}</span>{text_html}</div>'


INTRO_FOLD = 300   # 소개가 이 글자 수보다 길면 다섯 줄쯤에서 접는다


def intro_card(player: dict) -> str:
    """위키백과 소개 카드. 길면 접어 두고 '더 보기'로 펼친다 — 버튼(다시 실행) 없이 브라우저에서 바로."""
    paragraphs = "".join(f"<p>{escape(line.strip())}</p>" for line in player["intro"].splitlines() if line.strip())
    folded = len(player["intro"]) > INTRO_FOLD
    more = ('<details><summary><span class="more">더 보기 ▾</span><span class="less">접기 ▴</span></summary></details>'
            if folded else "")
    return (f'<div class="fc-panel fc-intro{" folded" if folded else ""}">'
            f'<div class="title">선수 소개<small>한국어 위키백과</small></div><div class="body">{paragraphs}</div>{more}'
            f'<div class="fc-note">출처: <a href="{escape(player["wiki_url"])}" target="_blank">위키백과 — '
            f'{escape(player["wiki_title"])}</a> (CC BY-SA 4.0)</div></div>')


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
