"""🏠 홈 — 검색이 첫 화면이다. 이름 · 시즌으로 바로 찾고(네트워크 0회), 말로 찾고(AI), 랭커 픽을 둘러본다."""

from __future__ import annotations

from dataclasses import replace
from html import escape

import streamlit as st

import config
from data_prep import namu
from data_prep.players import stat_values
from services.explore import describe, explore_players, suggest_conditions
from services.finder import Filters
from services.namufind import NamuFinder
from views.common import Ctx, back_button, daily_wait, friendly_error, get_player, log, search_players, tile_grid
from views.widgets import season_family, season_select

AI_PAGE = 12   # AI 찾기 결과 한 화면 (2줄 × 6장) — 많으면 ◀ ▶ 로 넘긴다
AI_VIEWS = ("선수마다 대표 카드", "모든 시즌 카드")
# 데이터센터 조건 검색으로도 찾을 만한 사실 — 이 중 하나라도 있으면 AI 명단 밖의 카드도 '그 밖의 카드'로 보인다
FACTS = ("nation", "league", "club", "teamcolor", "teamcolor_any", "teamcolor_also", "player_teamcolors", "traits", "traits_any",
         "seasons", "physical", "skill_moves", "reputation")


def _facts(filters: Filters | None, condition: str) -> Filters | None:
    """AI 명단과 함께 데이터센터 조건 검색으로도 찾을 조건 — 국적 · 리그 · 클럽 · 팀컬러(트레블 · 듀오) · 특성 같은 사실이 있을 때.
    수상 · 업적 말로 읽은 시즌 팀컬러('발롱도르' → Ballon d'Or 시즌 카드)는 수상자와 다르다(후보도 든다) — '팀컬러'라고 말했을
    때만 쓴다. 포지션 · 레전드만이면 None (전체에서 OVR 높은 카드일 뿐이라)."""
    if filters is None or filters.empty():
        return None
    f = filters if "팀컬러" in condition.replace(" ", "") else replace(filters, player_teamcolors=())
    return f if any(getattr(f, name) for name in FACTS) else None


def _run_ai_search(ctx: Ctx, condition: str) -> None:
    """말 → 1차 나무위키 → 2차 AI 가 조건에 맞는 선수를 모두 → 3차 FC 온라인 DB 와 맞추기 (services/namufind.py).
    AI 가 읽은 조건(국적 · 포지션 · 레전드 …)은 3차에서 카드를 거르고 국적 · 클럽을 확인하는 데 쓴다.
    국적 · 리그 · 클럽 · 팀컬러 같은 사실이 조건에 있으면 데이터센터 조건 검색의 다른 카드도 '그 밖의 카드'로 함께 보인다 (_facts).
    AI 명단을 하나도 맞추지 못하면 데이터센터 조건 검색으로, 그것도 없으면 분석해 둔 카드에서 뜻이 가까운 카드(예전 방식)."""
    try:
        with st.status("🤖 AI로 찾는 중… (1차 나무위키 → 2차 AI 명단 → 3차 FC 온라인 DB)", expanded=True) as status:
            status.write("조건 읽기 — 국적 · 포지션 · 레전드 같은 칸 (AI)")
            filters, unknown = ctx.finder.parse(condition, ctx.model) if ctx.finder.enabled else (None, [])
            try:
                result = NamuFinder(namu.client(config.wiki_contact()), ctx.db, ctx.catalog, ctx.finder,
                                    ctx.model).run(condition, filters, progress=status.write)
            except Exception as exc:  # noqa: BLE001 — AI 명단을 못 만들면 데이터센터 조건 검색으로
                log.exception("AI 로 찾기 (나무위키 → AI 명단) 실패")
                status.write(f"AI 명단을 만들지 못했습니다 — {friendly_error(exc)}")
                result = None
            extra = []
            players = (result or {}).get("players", [])
            facts = _facts(filters, condition) or (filters if not players and filters is not None and not filters.empty()
                                                   else None)   # AI 명단이 비면 읽은 조건 그대로라도
            if facts is not None and ctx.finder.enabled:
                status.write("데이터센터 조건 검색 — 같은 조건의 다른 카드")
                shown = {p["pid"] for p in players}
                extra = [r["spid"] for r in ctx.finder.search(facts) if r["spid"] % 1_000_000 not in shown]
            status.update(label=f"🤖 AI로 찾기 — {len((result or {}).get('players', []))}명 · 다른 카드 {len(extra)}장",
                          state="complete", expanded=False)
        if (result and result["players"]) or extra:
            st.session_state.explore_results = {"condition": condition, "mode": "namu", "filters": filters,
                                                "unknown": unknown, "namu": result, "extra": extra}
        else:
            found = explore_players(condition, ctx.store, ctx.model, ctx.by_id, ctx.settings)
            st.session_state.explore_results = {"condition": condition, **found}
        st.session_state.ai_page = st.session_state.ai_page2 = 0
        ctx.db.log_search(condition)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI 검색 실패")
        st.error(friendly_error(exc))


def _pages(ctx: Ctx, spids: list[int], key: str, page_key: str, reasons: dict[str, str] | None = None) -> None:
    """카드 타일을 한 화면 12장씩 (◀ ▶)."""
    total = len(spids)
    pages = (total + AI_PAGE - 1) // AI_PAGE
    page = min(st.session_state.get(page_key, 0), pages - 1)
    shown = [get_player(ctx, str(s)) for s in spids[page * AI_PAGE: (page + 1) * AI_PAGE]]
    tile_grid(ctx, shown, key=f"{key}{page}", reasons=reasons)
    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("◀ 이전", key=f"{key}_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update({page_key: page - 1}))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages} · "
                          f"{page * AI_PAGE + 1}~{min(total, (page + 1) * AI_PAGE)}번째</div>", unsafe_allow_html=True)
        next_col.button("다음 ▶", key=f"{key}_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update({page_key: page + 1}))


def _namu_results(ctx: Ctx, found: dict) -> None:
    """AI 로 찾은 선수 — 세 단계(나무위키 문서 · AI 명단 · FC 온라인 DB)를 칸으로 보이고, 선수마다 대표 카드(조건에 맞는 카드 중
    OVR 이 가장 높은 것) 또는 모든 시즌 카드를 12장씩. DB 에 없는 선수 · 조건으로 뺀 선수는 접어 둔다."""
    res, filters = found.get("namu") or {}, found.get("filters")
    players = res.get("players", [])
    st.markdown(f'<div class="fc-section">🤖 AI로 찾기 — "{escape(found["condition"])}"<small>{len(players)}명 · 1차 나무위키 → '
                '2차 AI 명단 → 3차 FC 온라인 DB 순서로 찾았습니다</small></div>', unsafe_allow_html=True)
    if res:
        docs = " · ".join(f'<a href="{escape(d["url"])}" target="_blank" rel="noopener">{escape(d["title"])}</a>'
                          for d in res.get("docs", []))
        first = (f"문서 {docs}" if docs else "읽은 문서 없음") + f" · 검색 결과 {res.get('search', 0)}건"
        if res.get("error"):
            first = f"닿지 않음 — {escape(res['error'])} (AI 지식만으로)"
        cells = [("📚 1차 나무위키", first),
                 ("🧠 2차 AI 명단", f"<b>{res.get('listed', 0)}명</b> — 나무위키 근거 {res.get('cited', 0)} · "
                                   f"AI 지식 {res.get('listed', 0) - res.get('cited', 0)}"),
                 ("🗂️ 3차 FC 온라인 DB", f"<b>{len(players)}명</b> 카드 있음 · DB 에 없음 {len(res.get('missing', []))} · "
                                       f"조건으로 뺌 {len(res.get('dropped', []))}")]
        st.markdown('<div class="fc-sum fc-steps">' + "".join(f"<div><span>{k}</span><small>{v}</small></div>" for k, v in cells)
                    + "</div>", unsafe_allow_html=True)
    # 실제로 쓴 조건만 — 수상 · 업적 말로 읽은 시즌 팀컬러(발롱도르 → Ballon d'Or 카드)는 '팀컬러'라고 말했을 때만 쓴다 (_facts)
    used = filters if filters is None or "팀컬러" in found["condition"].replace(" ", "") else replace(filters, player_teamcolors=())
    if used is not None and not used.empty():
        read = "".join(f'<span class="fc-chip">{escape(c)}</span>' for c in used.chips(ctx.finder.season_names()))
        st.markdown(f'<div class="fc-chips"><span class="fc-chip lead">AI가 읽은 조건</span>{read}</div>', unsafe_allow_html=True)
    if found.get("unknown"):
        st.caption("목록에서 찾지 못해 빼고 찾은 말: " + ", ".join(found["unknown"]))
    if players:
        st.session_state.setdefault("ai_view", AI_VIEWS[0])
        view = st.segmented_control("보기", AI_VIEWS, key="ai_view", label_visibility="collapsed",
                                    on_change=lambda: st.session_state.update(ai_page=0)) or AI_VIEWS[0]
        reasons = {}
        for p in players:
            why = (p.get("why") or "조건에 맞음") + (" · AI 지식" if p.get("src") == "AI 지식" else "")
            for s in (p["spids"] if view == AI_VIEWS[1] else [p["spid"]]):
                reasons[str(s)] = why
        spids = [s for p in players for s in (p["spids"] if view == AI_VIEWS[1] else [p["spid"]])]
        _pages(ctx, spids, "ai", "ai_page", reasons)
        if res.get("unsure"):
            st.caption("국적 · 클럽을 데이터센터로 다 확인하지 못한 선수: " + ", ".join(res["unsure"]))
    elif res:
        st.info("AI 가 찾은 선수 중 FC 온라인에 카드가 있고 조건에 맞는 선수가 없습니다.")
    left = res.get("missing", []) + res.get("dropped", [])
    if left:
        with st.expander(f"DB 에 없는 선수 · 조건으로 뺀 선수 ({len(left)}명)"):
            lines = [f"- **{escape(m['name'])}** — FC 온라인에 카드가 없음" + (f" ({escape(m['why'])})" if m.get("why") else "")
                     for m in res.get("missing", [])]
            lines += [f"- **{escape(d['name'])}** — {escape(d['reason'])}" for d in res.get("dropped", [])]
            st.markdown("\n".join(lines))
    if found.get("extra"):
        st.markdown(f'<div class="fc-section">📋 데이터센터 조건 검색 — 그 밖의 카드<small>{len(found["extra"])}장'
                    f'{" (OVR 높은 순 200장까지)" if len(found["extra"]) >= 190 else ""} · AI 명단에 없어도 조건에 맞는 카드'
                    '</small></div>', unsafe_allow_html=True)
        _pages(ctx, found["extra"], "aix", "ai_page2")
    if res:
        st.caption(f"출처: 나무위키 ({res.get('license', 'CC BY-NC-SA 2.0 KR')}) — 선수 명단의 근거. 카드 · 포지션 · OVR 은 "
                   "FC 온라인 데이터, 국적 · 클럽 조건은 데이터센터 선수 검색으로 확인합니다. AI 지식으로 넣은 선수는 따로 표시합니다.")


def _finder_results(ctx: Ctx, found: dict) -> None:
    """AI 로 찾은 카드 — AI 가 읽은 조건(칩) · 데이터센터에서 거른 카드를 한 화면 12장씩 (◀ ▶)."""
    filters, spids = found["filters"], found["spids"]
    total = len(spids)
    st.markdown(f'<div class="fc-section">🤖 AI로 찾기 — "{escape(found["condition"])}"<small>{total}장'
                f'{" (데이터센터 검색은 OVR 높은 순 200장까지)" if total >= 200 else ""} · 국적 · 포지션 같은 사실은 '
                'FC 온라인 데이터센터 데이터로 거릅니다</small></div>', unsafe_allow_html=True)
    read = "".join(f'<span class="fc-chip">{escape(c)}</span>' for c in filters.chips(ctx.finder.season_names()))
    st.markdown(f'<div class="fc-chips"><span class="fc-chip lead">AI가 읽은 조건</span>{read}</div>', unsafe_allow_html=True)
    if found.get("unknown"):
        st.caption("목록에서 찾지 못해 빼고 찾은 말: " + ", ".join(found["unknown"]))
    if not total:
        st.info("조건에 맞는 카드가 없습니다. 조건을 줄여 보세요.")
        return
    pages = (total + AI_PAGE - 1) // AI_PAGE
    page = min(st.session_state.get("ai_page", 0), pages - 1)
    shown = [get_player(ctx, str(s)) for s in spids[page * AI_PAGE: (page + 1) * AI_PAGE]]
    tile_grid(ctx, shown, key=f"ai{page}")
    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        prev_col.button("◀ 이전", key="ai_prev", disabled=page == 0, width="stretch",
                        on_click=lambda: st.session_state.update(ai_page=page - 1))
        info_col.markdown(f"<div style='text-align:center;padding-top:6px'>{page + 1} / {pages} · "
                          f"{page * AI_PAGE + 1}~{min(total, (page + 1) * AI_PAGE)}번째</div>", unsafe_allow_html=True)
        next_col.button("다음 ▶", key="ai_next", disabled=page >= pages - 1, width="stretch",
                        on_click=lambda: st.session_state.update(ai_page=page + 1))


def _shuffle_chips(ctx: Ctx) -> None:
    st.session_state.chips = suggest_conditions(ctx.db.search_counts())


def _use_example(text: str) -> None:
    st.session_state.q = text
    st.session_state.pending_ai = text


def _clear() -> None:
    """검색 지우고 첫 화면으로."""
    st.session_state.q = ""
    st.session_state.q_season = None
    st.session_state.explore_results = None


def render(ctx: Ctx) -> None:
    analyzed = sum(1 for p in ctx.players if p.get("ranker"))
    dates = [p["ranker"]["date"] for p in ctx.players if p.get("ranker")]
    new_cards = ctx.db.new_cards(days=7)
    st.markdown(
        '<div class="fc-hero"><h1>FC 온라인 선수, <b>한 번에</b> 찾고 비교하세요</h1>'
        "<p>랭커 실전 기록 · 실제 커리어 · AI 분석을 근거와 함께. 이름으로 찾거나, 원하는 선수를 말로 설명해 보세요.</p>"
        f'<div class="stats"><div><b>{len(ctx.catalog.cards):,}</b>전체 카드</div>'
        f'<div><b>{len(ctx.catalog.by_pid):,}</b>선수</div><div><b>{analyzed:,}</b>랭커 분석 카드</div>'
        f'<div><b>{max(dates) if dates else "-"}</b>랭커 집계일</div>'
        + (f'<div><b>+{new_cards:,}</b>최근 7일 새 카드</div>' if new_cards else "") + "</div></div>",
        unsafe_allow_html=True,
    )

    # 검색 줄 — ← 뒤로 · 검색어 · 시즌 · ↺ 초기화 · 🤖 AI로 찾기 (초기화는 실행 버튼 바로 왼쪽, 넓은 화면은 한 줄: ui_parts.css)
    back_col, q_col, s_col, clear_col, ai_col = st.container(key="wrap_search").columns(
        [0.9, 4, 3.4, 1, 1.4], vertical_alignment="bottom")
    back_button("home", back_col)
    query = q_col.text_input("선수 검색", key="q", placeholder="선수 이름 (예: 손흥민, 호날두)  ·  또는 조건 (예: 발 빠른 한국 윙어)",
                             label_visibility="collapsed")
    with s_col:
        season = season_select(ctx, key="q_season")
    ai_clicked = ai_col.button("🤖 AI로 찾기", key="ai_search", type="primary", width="stretch")
    searching = bool(query.strip() or season or st.session_state.explore_results)
    clear_col.button("↺ 초기화", key="q_clear", on_click=_clear, width="stretch", disabled=not searching,
                     help="검색어 · 시즌 · AI 결과를 지우고 첫 화면으로")

    if "chips" not in st.session_state:   # 자주 찾는 조건 중 무작위 — 들어올 때마다 바뀌고, 🔀 로 다시 뽑는다
        _shuffle_chips(ctx)
    # 칩은 글자 길이만큼 — 칸을 나누면 좁은 칸에서 글자가 단어 중간에 끊기고 🔀 가 줄 하나를 다 차지했다
    with st.container(key="chipbar", horizontal=True, gap="small", vertical_alignment="center"):
        for example in st.session_state.chips:
            st.button(f"💡 {example}", key=f"chip_{example}", on_click=_use_example, args=(example,), type="tertiary",
                      width="content")
        st.button("🔀", key="chip_shuffle", on_click=_shuffle_chips, args=(ctx,), type="tertiary", help="다른 추천 검색어",
                  width="content")

    pending = st.session_state.pop("pending_ai", None)
    if (ai_clicked and query.strip()) or pending:
        _run_ai_search(ctx, pending or query.strip())   # 진행 상황은 그 안의 단계 상자(st.status)로
    elif ai_clicked:
        st.caption("찾고 싶은 조건을 먼저 적어 주세요.")

    found = st.session_state.explore_results
    if found and found.get("mode") == "namu":
        _namu_results(ctx, found)
    elif found and found.get("mode") == "finder":
        _finder_results(ctx, found)
    elif found:
        items = [i for i in found["items"] if i["player_id"] in ctx.by_id]
        st.markdown(f'<div class="fc-section">🤖 AI 추천 — "{escape(found["condition"])}"<small>{len(items)}명</small></div>',
                    unsafe_allow_html=True)
        st.caption(describe(found["condition"], ctx.players, found))
        if items:
            tile_grid(ctx, [ctx.by_id[i["player_id"]] for i in items], key="ai", ranked=True,
                      reasons={i["player_id"]: i["reason"] for i in items},
                      badges={i["player_id"]: i.get("rank", "").replace("파워 랭킹 ", "") for i in items})
        else:
            st.info("조건에 맞는 선수를 찾지 못했습니다. 조건을 바꿔 보세요.")

    # 이름 · 시즌 검색 — 입력하는 즉시 (네트워크 없이). 보이는 카드는 백그라운드로 미리 분석해 둔다.
    results = search_players(ctx, query, season) if (query.strip() or season) else []
    ctx.prefetcher.schedule([p["spid"] for p in results if p.get("lite")])
    if results:   # 이름이 맞는 카드가 있을 때만 '이름 검색 결과' — 조건을 적었을 때 '0장'을 보이지 않는다
        title = f"'{query.strip()}'" if query.strip() else ""
        # 갈래를 합친 시즌(25IM - S&P · 25IM - FB)은 묶음 이름 하나로
        where = (" · ".join(dict.fromkeys(season_family(ctx.catalog.seasons[s]["className"])[0] for s in season))
                 if season else "전체 시즌")
        st.markdown(f'<div class="fc-section">🔍 {title} 이름 검색 결과<small>{where} · {len(results)}장'
                    f'{" (더 있으면 이름을 더 정확히)" if len(results) >= 24 else ""}</small></div>', unsafe_allow_html=True)
        tile_grid(ctx, results, key="res")
    elif query.strip() and not found:
        st.info(f"'{query.strip()}' 이(가) 이름에 들어간 카드는 없습니다. 조건(예: 일본 출신 미드필더)이라면 **🤖 AI로 찾기**를 누르세요 — "
                "국적 · 리그 · 클럽 · 포지션 · 특성으로 전체 카드에서 찾습니다.")
    if searching:
        return

    favorite_ids = [i for i in ctx.favorites.ids() if i in ctx.by_id]
    if favorite_ids:
        st.markdown('<div class="fc-section">⭐ 즐겨찾기</div>', unsafe_allow_html=True)
        tile_grid(ctx, [ctx.by_id[i] for i in favorite_ids][:6], key="fav")

    if ctx.daily.enabled:
        _daily_sections(ctx)
        return
    _ranker_sections(ctx)   # 데이터센터를 끈 경우 — 넥슨 Open API 랭커 분석 카드로


def _daily_sections(ctx: Ctx) -> None:
    """어제 랭커가 실제로 쓴 카드 (데이터센터 데일리 기록) — 파워 랭킹 · 레전드(모든 시즌) · 수비 핵심. 시즌 · 이미지와 함께."""
    tables = {order: ctx.daily.stats(order) for order in ("ATTACKPOINT", "MATCHCOUNT", "RATING", "INTERCEPT")}
    if any(v is None for v in tables.values()):
        if ctx.daily.pending():
            st.markdown('<div class="fc-section">🏆 오늘의 파워 랭킹</div>', unsafe_allow_html=True)
            daily_wait(ctx)
            return
        if not any(tables.values()):
            _ranker_sections(ctx)
            return
    rows = {order: (data or {}).get("rows", []) for order, data in tables.items()}
    day = next((d["date"] for d in tables.values() if d), "")
    top = rows["ATTACKPOINT"]
    st.markdown(f'<div class="fc-section">🏆 오늘의 파워 랭킹 — 랭커 공격 포인트 TOP<small>{day} · 공식 경기 랭커 상위 1만 명이 '
                '쓴 카드의 경기당 골 + 도움 · FC 온라인 데이터센터 데일리 기록</small></div>', unsafe_allow_html=True)
    _daily_tiles(ctx, top, "top", lambda r: f"공격 포인트 <b>{r['stats'].get('ATTACKPOINT', 0):.1f}</b> · {r['matches']:,}경기",
                 limit=12)

    legends = ctx.finder.legends()
    seen, picked = set(), []
    for r in sorted((r for table in rows.values() for r in table), key=lambda r: -r["matches"]):
        if r["spid"] % 1_000_000 in legends and r["spid"] not in seen:
            seen.add(r["spid"])
            picked.append(r)
    st.markdown('<div class="fc-section">👑 랭커가 쓰는 레전드<small>아이콘 카드가 있는 선수의 모든 시즌 카드 · 출전 많은 순</small></div>',
                unsafe_allow_html=True)
    if picked:
        _daily_tiles(ctx, picked[:6], "legend", lambda r: f"{r['matches']:,}경기 · 평점 <b>{r['stats'].get('RATING', 0):.1f}</b>")
    else:
        st.caption("오늘 기록 TOP 에 든 레전드 카드가 없습니다.")

    st.markdown('<div class="fc-section">🛡️ 수비 핵심 — 가로채기 TOP<small>경기당 가로채기</small></div>', unsafe_allow_html=True)
    _daily_tiles(ctx, rows["INTERCEPT"], "def", lambda r: f"가로채기 <b>{r['stats'].get('INTERCEPT', 0):.1f}</b> · {r['matches']:,}경기",
                 limit=6)


def _daily_tiles(ctx: Ctx, rows: list[dict], key: str, kpi, limit: int = 6) -> None:
    """데일리 기록 카드 타일 — 같은 카드가 강화 등급만 달리 두 번 나오면 앞(높은 순위)의 것 하나만."""
    seen: set[int] = set()
    rows = [r for r in rows if not (r["spid"] in seen or seen.add(r["spid"]))][:limit]
    by_id = {str(r["spid"]): r for r in rows}
    players = [get_player(ctx, str(r["spid"])) for r in rows]
    tile_grid(ctx, players, key=key, ranked=key == "top", kpi=lambda p: kpi(by_id[p["player_id"]]))


def _ranker_sections(ctx: Ctx) -> None:
    """넥슨 Open API 랭커 분석 카드로 만든 랭킹 (데이터센터를 끈 경우)."""
    def points(p: dict) -> float:
        v = stat_values(p.get("ranker"))
        return v["goal"] + v["assist"] if v else -1

    active = [p for p in ctx.players if p.get("ranker") and p["ranker"]["match_count"] >= 10]
    st.markdown('<div class="fc-section">🏆 파워 랭킹 — 랭커 경기 공격 포인트 TOP<small>공식경기 랭커 경기당 골 + 도움 · '
                '분석해 둔 카드</small></div>', unsafe_allow_html=True)
    tile_grid(ctx, sorted(active, key=points, reverse=True)[:12], key="top", ranked=True)

    legends = ctx.finder.legends()
    picked = [p for p in active if p["pid"] in legends]
    st.markdown('<div class="fc-section">👑 랭커가 쓰는 레전드<small>아이콘 카드가 있는 선수의 모든 시즌 카드</small></div>',
                unsafe_allow_html=True)
    tile_grid(ctx, sorted(picked, key=points, reverse=True)[:6], key="legend")

    defenders = [p for p in active if p.get("position_group") in ("센터백", "풀백", "수비형 미드필더")]
    st.markdown('<div class="fc-section">🛡️ 수비 핵심 — 태클 + 블록 TOP</div>', unsafe_allow_html=True)
    tile_grid(ctx, sorted(defenders, key=lambda p: -(p["ranker"]["tackle"] + p["ranker"]["block"]))[:6], key="def")
