"""오프라인 점검 — 키 · 네트워크 없이 명세서 4장 검증 항목을 확인한다.

    python tests/offline_check.py

가짜 LLM(정해 둔 JSON · 답변), 가짜 임베딩(DeterministicFakeEmbedding), 가짜 위키백과를 끼워 넣는다.
그래서 '흐름과 규칙'은 확인하지만 '답의 품질'은 확인하지 못한다 — 그건 check_live.py 로 본다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["GMS_KEY"] = ""          # .env 에 키가 있어도 이 점검은 키 없이 돈다 (load_dotenv 는 이미 있는 값을 덮어쓰지 않는다)
os.environ["NEXON_API_KEY"] = ""    # 넥슨 호출도 하지 않는다 (전체 카드 찾기는 꺼진 상태로 점검)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import config  # noqa: E402
import llm  # noqa: E402
from data_prep import prompts  # noqa: E402
from data_prep.collector import Collector  # noqa: E402
from data_prep.players import index_text, load_players  # noqa: E402
from rag.citations import cited_evidence  # noqa: E402
from rag.graph import build_graph, merge_evidence  # noqa: E402
from services.answering import run_task  # noqa: E402
from services.explore import explore_players  # noqa: E402
from vector_store import PlayerStore  # noqa: E402

FAILED: list[str] = []


def check(ok: bool, message: str) -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + message)
    if not ok:
        FAILED.append(message)


# ── 가짜 부품 ─────────────────────────────────────────────────────

class FakeChat:
    """시스템 프롬프트로 어떤 프롬프트인지 알아보고 정해 둔 응답을 낸다."""

    def __init__(self):
        self.calls: list[str] = []

    def invoke(self, messages):
        # 프롬프트마다 다른 문구로 알아본다 (앞부분은 역할 설명이 같아서 겹친다)
        system = messages[0]["content"]
        if "검색어를 뽑는다" in system:
            self.calls.append("keyword")
            return SimpleNamespace(content='```json\n["토트넘 홋스퍼 FC", "2020년", "선수본인"]\n```')
        if "근거로 쓸 문서만 고른다" in system:
            self.calls.append("select")
            return SimpleNamespace(content="[1]")
        if "전문 스카우터" in system:
            self.calls.append("scout")
            return SimpleNamespace(content="테스트 스카우팅 문장입니다 [근거 1]. 랭커는 주로 ST로 씁니다 [근거 1].")
        if "전술 코치" in system:
            self.calls.append("tactic")
            return SimpleNamespace(content="### 한 줄 컨셉\n테스트 전술 [근거 1, 2].")
        if "FC 온라인 코치" in system:
            self.calls.append("coach")
            return SimpleNamespace(content="### 한 줄 진단\n테스트 진단 [근거 1].")
        if "조건에 맞는 선수 카드를" in system:
            self.calls.append("explore")
            return SimpleNamespace(content='[{"no": 2, "reason": "조건과 맞는 이유"}, {"no": 99, "reason": "없는 번호"}]')
        self.calls.append("answer")
        return SimpleNamespace(content="데뷔 기록은 다음과 같습니다 [근거 1]. 클럽 경력 [근거 2, 99].")


class FakeWiki:
    name = "한국어 위키백과 (가짜)"

    def __init__(self):
        self.fetched: list[str] = []
        self.searched: list[str] = []

    def search(self, query, limit=5, movie_id=None):
        self.searched.append(query)
        return [
            {"title": f"{query}", "page_id": str(abs(hash(query)) % 10**6), "snippet": f"{query} 요약"},
            {"title": "2020년", "page_id": "2020", "snippet": "연도 문서"},
        ]

    def fetch(self, title):
        self.fetched.append(title)
        text = (f"{title}에 대한 문서 본문입니다. " * 120).strip()
        return {"page_id": str(abs(hash(title)) % 10**6 + 10**6), "title": title,
                "url": f"https://ko.wikipedia.org/wiki/{title}", "text": text}


def fake_embeddings(_settings=None):
    return DeterministicFakeEmbedding(size=64)


# ── 준비: 임시 저장소 · 선수 표본 ─────────────────────────────────

real = config.load_settings()
if not real.items_path.exists():
    sys.exit("data/players.json 이 없습니다. 먼저 python build_items.py 를 실행하세요.")
all_players = load_players(real.items_path)
with_wiki = [p for p in all_players if p.get("wiki_title") and p.get("ranker")]
sample = with_wiki[:20]
keeper = next(p for p in with_wiki if p.get("position_group") == "골키퍼")   # 골키퍼 선방률 화면 점검용
if keeper not in sample:
    sample.append(keeper)
tmp = Path(tempfile.mkdtemp(prefix="fclab_check_"))
(tmp / "data").mkdir()
(tmp / "data" / "players.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
toml = (ROOT / "settings.toml").read_text(encoding="utf-8")
toml = toml.replace('data_dir = "data"', f'data_dir = "{(tmp / "data").as_posix()}"')
toml = toml.replace('storage_dir = "storage"', f'storage_dir = "{(tmp / "storage").as_posix()}"')
toml = toml.replace("enabled = true", "enabled = false")   # 시세(데이터센터)는 점검 중에 부르지 않는다
(tmp / "settings.toml").write_text(toml, encoding="utf-8")
settings = config.load_settings(tmp / "settings.toml", request_interval=0)
a, b = sample[0], sample[1]
print(f"표본 선수 {len(sample)}명 · 임시 폴더 {tmp}")

print("\nF2001  카드 → 선수 문서 매칭 규칙 (동명이인)")


def intro(title, text):
    return {"title": title, "intro": text, "disambiguation": False, "url": "", "page_id": "1"}


cases = [
    ("L. 수아레스", True, intro("루이스 수아레스 미라몬테스", "루이스 수아레스 미라몬테스(스페인어: Luis Suárez Miramontes, 1935년 5월 2일~2023년 7월 9일)는 스페인의 축구 선수"), False),
    ("L. 수아레스", True, intro("루이스 수아레스", "루이스 알베르토 수아레스 디아스(스페인어: Luis Alberto Suárez Díaz, 1987년 1월 24일 ~ )는 우루과이의 축구 선수"), True),
    ("M. 상가레", True, intro("이브라힘 상가레", "이브라힘 상가레(프랑스어: Ibrahim Sangaré, 1997년 12월 2일 ~ )는 코트디부아르의 축구 선수"), False),
    ("M. 고츠", True, intro("샤르헤이 고츠마나우", "샤르헤이 고츠마나우(벨라루스어: Сяргей, 1959년)는 축구 선수"), False),
    ("위리엔 팀버", True, intro("유리언 팀버르", "유리언 팀버르(네덜란드어: Jurriën Timber, 2001년 6월 17일 ~ )는 네덜란드의 축구 선수"), True),
    ("카를루스 아우베르투", False, intro("카를루스 아우베르투 파헤이라", "카를루스 아우베르투 파헤이라(포르투갈어: Carlos Alberto Parreira, 1943년)는 브라질의 축구 감독이다."), False),
    ("카를루스 아우베르투", False, intro("카를루스 아우베르투 토히스", "카를루스 아우베르투 토히스(포르투갈어: Carlos Alberto Torres, 1944년)는 브라질의 축구 선수이다."), True),
    ("D. 소보슬러이", True, intro("소보슬러이 도미니크", "소보슬러이 도미니크(헝가리어: Szoboszlai Dominik, 2000년 10월 25일 ~ )는 헝가리의 축구 선수"), True),
    ("누누 멘데스", True, intro("누누 멘드스", "누누 멘드스(포르투갈어: Nuno Mendes, 2002년 6월 19일 ~ )는 포르투갈의 축구 선수"), True),
    ("레프 야신", False, intro("레프 야신", "레프 이바노비치 야신(러시아어: Лев Яшин, 1929년 ~ 1990년)은 소련의 축구 골키퍼였다."), True),
]
from data_prep.player_builder import plausible  # noqa: E402

for name, current, info, expected in cases:
    reason = plausible(name, info, current, searched=True)
    check((reason is None) == expected, f"{name} → {info['title']}: {'받아들임' if reason is None else '버림 — ' + reason}")

print("\nF2001 · F2002  항목 메타데이터 색인")
text = index_text(a)
check(all(f in text for f in ("선수:", "FC 온라인 카드:", "랭커 주 포지션:", "랭커 평균 기록:", "소개:")),
      "색인 텍스트에 이름 · 카드 · 포지션 · 랭커 기록 · 소개가 모두 있다")
store = PlayerStore(settings, fake_embeddings())
store.index_players(sample)
first = store.meta_count()
store.index_players(sample)
check(first == len(sample) and store.meta_count() == first, f"두 번 색인해도 {first}건 그대로 (로드 {len(sample)}건)")

print("\nF2003 · F2004 · NF2002  검색어 추출 · 선별 · 수집 캐시")
chat, wiki = FakeChat(), FakeWiki()
collector = Collector(settings, chat, wiki)
keywords = collector.extract_keywords({**a, "name": "선수본인"}, "질문")
check(keywords == ["토트넘 홋스퍼 FC", "2020년"], f"코드 블록 JSON 을 읽고 선수 이름은 뺀다: {keywords}")
report = collector.collect(a, "데뷔는 언제야?")
stored = collector.stored_docs(a["player_id"])
check(stored and stored[0]["is_self"] and stored[0]["title"] == a["wiki_title"], "선수 자체 문서를 항상 수집한다")
check(("2020년", "연도 문서") in report.rejected, "연도 문서는 제목 규칙으로 버린다")
check(any(why == "선별 프롬프트가 고르지 않음" for _, why in report.rejected) or len(report.new_docs) >= 2,
      f"선별 결과 — 수집 {report.selected}, 버림 {report.rejected[:3]}")
fetched_before, calls_before = len(wiki.fetched), chat.calls.count("keyword")
again = collector.collect(a, "데뷔는 언제야?")
check(again.cached and len(wiki.fetched) == fetched_before and chat.calls.count("keyword") == calls_before,
      "같은 질문을 다시 하면 프롬프트 1 · 검색 · 수집이 다시 일어나지 않는다")

print("\nF2005  외부 문서 청킹 · 색인")
added = store.index_wiki_docs(a["player_id"], stored)
got = store.wiki.get(where={"player_id": a["player_id"]}, include=["metadatas"])
meta0 = got["metadatas"][0]
check(added > 0 and all(i.startswith(f"{a['player_id']}:") and i.count(":") == 2 for i in got["ids"]),
      f"청크 {added}개, ID = 선수 ID:문서 ID:순번 (예: {got['ids'][0]})")
check(all(k in meta0 for k in ("title", "url", "player_id", "related_player", "search_query", "collected_at")),
      "청크에 제목 · 원문 URL · 관련 선수 · 검색어 · 수집일이 있다")
check(store.index_wiki_docs(a["player_id"], stored) == 0, "이미 색인한 문서는 다시 색인하지 않는다")
small = PlayerStore(config.load_settings(tmp / "settings.toml", chunk_size=500, chunk_overlap=100), fake_embeddings())
small_added = small.index_wiki_docs(a["player_id"], stored)
check(small_added > added and small.settings.wiki_collection != settings.wiki_collection,
      f"청크 크기를 1000→500 으로 바꾸면 청크 수 {added}→{small_added}, 컬렉션도 따로")

print("\nF2006 · F2007 · F2008  검색 병합 · LangGraph · 근거 표시")
graph = build_graph(store, chat, settings)
check({"retrieve", "generate"} <= set(graph.get_graph().nodes), f"그래프 노드: {sorted(graph.get_graph().nodes)}")
result = run_task("qa", [a], "데뷔는 언제야?", collector, store, graph)
ev = result["evidence"]
check(ev[0]["source_type"] == "player_meta" and ev[0]["number"] == 1, "선수 메타데이터 청크가 항상 [근거 1]")
check(len([e for e in ev if e["source_type"] == "wiki"]) <= settings.top_k, f"위키 근거 수 ≤ Top-k({settings.top_k})")
check({e["source_label"] for e in ev} == set(prompts.SOURCE_LABELS.values()), "근거에 출처 유형(메타데이터/위키백과)이 붙는다")
used = cited_evidence(result["answer"], ev)
check([e["number"] for e in used] == [1, 2], "답변이 인용한 [근거 1, 2]만 남기고 없는 번호(99)는 무시한다")
check(cited_evidence("근거 없는 답", ev) == [], "인용이 없으면 빈 목록 → 화면에 '인용된 근거 없음' 안내")
meta_doc = store.get_meta(a["player_id"])
check(len(merge_evidence([meta_doc, meta_doc], [])) == 1, "같은 청크가 두 번 와도 하나만 남긴다")
both = run_task("compare", [a, b], "", collector, store, graph)
check({e["player_id"] for e in both["evidence"] if e["source_type"] == "player_meta"} == {a["player_id"], b["player_id"]},
      "비교는 두 선수의 메타데이터를 모두 근거에 넣는다")

print("\nF2011  조건 기반 탐색")
by_id = {p["player_id"]: p for p in sample}
found = explore_players("대한민국 출신 공격수", store, chat, by_id, settings)
check(len(found) == 1 and found[0]["player_id"] in by_id, f"LLM 이 고른 번호만, 없는 번호(99)는 버린다: {found}")

print("\nNF2001 · NF2002  SQLite 저장소 · 백그라운드 미리 불러오기")
# 넥슨 메타데이터는 디스크 사본을 쓴다 (네트워크 없이). 실제 storage/meta 가 있어야 한다.
shutil.copytree(real.storage_dir / "meta", tmp / "storage" / "meta", dirs_exist_ok=True)
from data_prep.db import FcDb  # noqa: E402
from data_prep.nexon_api import NexonClient  # noqa: E402
from data_prep.player_builder import CardCatalog  # noqa: E402
from services.prefetch import Prefetcher  # noqa: E402


class FakeNexon(NexonClient):
    """메타데이터는 디스크 사본, 랭커 스탯은 정해 둔 값 — 호출 횟수를 센다."""

    def __init__(self):
        super().__init__(None)
        self.ranker_calls = 0

    def ranker_stats(self, pairs, matchtype=50, batch=40):
        self.ranker_calls += 1
        status = {"shoot": 3.0, "effectiveShoot": 2.0, "assist": 0.5, "goal": 1.5, "dribble": 100, "dribbleTry": 10,
                  "dribbleSuccess": 8, "passTry": 10, "passSuccess": 9, "block": 0.1, "tackle": 0.2, "matchCount": 20}
        return [{"spid": s, "spPosition": 25, "status": status, "createDate": "2026-10-20T17:30:00"}
                for s, po in pairs if po == 25]


FakeWiki.fetch_intros = lambda self, titles: {t: None for t in titles}
db = FcDb(tmp / "storage" / "test.db")
db.put_player({**a, "player_id": "1"})
check(db.get_player("1")["name"] == a["name"], "SQLite: 선수 저장 · 조회")
db.put_ranker_rows({}, [999])
check(db.ranker_rows(999) == [], "SQLite: 랭커 기록이 0건인 카드도 '받았음'으로 남아 다시 묻지 않는다")
db.toggle_favorite("1")
check(db.favorite_ids() == ["1"], "SQLite: 즐겨찾기")
fake_nexon = FakeNexon()
catalog = CardCatalog(fake_nexon, tmp / "storage" / "meta")
prefetcher = Prefetcher(catalog, FakeWiki(), db, 50, {})
outside_spid = next(c["id"] for c in catalog.cards if str(c["id"]) not in {p["player_id"] for p in sample})
prefetcher.schedule([outside_spid])
loaded = prefetcher.get(outside_spid, timeout=30)
calls = fake_nexon.ranker_calls
again = prefetcher.get(outside_spid)
check(loaded["position_group"] == "스트라이커" and db.get_player(str(outside_spid)) is not None,
      f"미리 불러오기: 예약한 카드({loaded['name']})를 백그라운드에서 분석해 SQLite 에 저장")
check(again["player_id"] == loaded["player_id"] and fake_nexon.ranker_calls == calls, "다시 열면 넥슨을 다시 부르지 않는다 (SQLite)")

# 골키퍼 선방률 — 랭커 API 에 없어 경기 상세(상대 유효슈팅 · 실점)로 계산한다
from datetime import date as _date  # noqa: E402

from data_prep.players import key_stat_line, save_rate  # noqa: E402
from services.insights import gk_games, leaderboard  # noqa: E402


def fake_detail(match_id, home_keeper, away_keeper, home_shots, away_shots):
    """경기 상세 1건 — shots = (유효슈팅, 골). 유저 식별자는 아무 값."""
    def team(ouid, keeper, shots):
        return {"ouid": ouid, "player": [{"spId": keeper, "spPosition": 0}, {"spId": 1, "spPosition": 25}],
                "shoot": {"effectiveShootTotal": shots[0], "goalTotal": shots[1]}}
    return {"matchId": match_id, "matchDate": _date.today().isoformat() + "T20:00:00",
            "matchInfo": [team("u1", home_keeper, home_shots), team("u2", away_keeper, away_shots)]}


rows = gk_games(fake_detail("m1", keeper["spid"], 777, (5, 2), (6, 1)))
check([(r["spid"], r["on_target"], r["goals"]) for r in rows] == [(keeper["spid"], 6, 1), (777, 5, 2)]
      and all("ouid" not in r for r in rows),
      "선방 재료: 양 팀 골키퍼마다 '상대' 유효슈팅 · 실점, 유저 식별자는 남기지 않는다")
check(gk_games({"matchId": "m0", "matchInfo": [{"player": []}, {"player": []}]}) == [], "몰수 · 기권 경기(선수 기록 없음)는 건너뛴다")
db.put_gk_games(rows)
db.put_gk_games(rows)   # 같은 경기를 두 번 받아도 한 번만
db.put_gk_games([{**rows[0], "match_id": "old", "match_date": "2000-01-01"}])   # 30일 지난 기록은 지운다
check(db.gk_saves()[keeper["spid"]] == {"games": 1, "on_target": 6, "goals": 1}, "SQLite: 경기 단위로 한 번만 · 30일 지난 기록은 빼고 합산")
thin = {**keeper, "gk_saves": {"games": 1, "on_target": 6, "goals": 1}}
enough = {**keeper, "gk_saves": {"games": 4, "on_target": 20, "goals": 5}}
check(save_rate(thin) is None and "선방 기록 부족" in key_stat_line(thin), "유효슈팅이 적으면 선방률을 숨기고 '선방 기록 부족'")
check(save_rate(enough) == 75.0 and "선방 <b>75%</b>" in key_stat_line(enough), "골키퍼 카드 아래 한 줄 = 선방률 (패스 대신)")
board = leaderboard([enough, {**keeper, "player_id": "x"}], "골키퍼", "전체", "save_rate", min_matches=99)
check([r["player"]["player_id"] for r in board] == [enough["player_id"]], "선방률 순위: 표본이 있는 골키퍼만 (랭커 최소 경기 수 대신)")

print("\n강화 단계별 시세 · 사기/팔기 판단 · 꺼져 있던 동안 채우기 (네트워크 없음)")
import time as _time  # noqa: E402
from datetime import timedelta as _td  # noqa: E402

from data_prep.market import MarketBlocked, _dates, bp, parse_price, signal  # noqa: E402
from services.market import MarketService  # noqa: E402


def price_html(current, labels, values):
    """데이터센터 시세 응답과 같은 모양 (현재가 strong · json1 time/value)."""
    q = lambda xs: ", ".join(f'"{x}"' for x in xs)  # noqa: E731
    return (f'<span>현재가</span>\n<strong alt="{current:,}" title="{current:,}">{bp(current)}</strong>'
            f'<script>var json1 = {{ "time": [ {q(labels)}, ], "value": [ {q(values)}, ] }} var option = {{}}</script>')


today = _date(2026, 10, 21)
check([d.isoformat() for d in _dates(["12.30", "12.31", "1.01", "1.02"], _date(2027, 1, 3))]
      == ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"], "연도 없는 날짜(12.31 → 1.01)를 해를 넘겨 이어 붙인다")
parsed = parse_price(price_html(10_500_000, ["10.19", "10.20"], [9_582_700, 9_131_800]), today)
check(parsed == {"current": 10_500_000, "history": [["2026-10-19", 9_582_700], ["2026-10-20", 9_131_800]]},
      "응답 HTML → 현재가 · 일별 시세")
check(parse_price("<html>바뀐 화면</html>", today) is None, "화면 구조가 바뀌어 못 읽으면 None (앱은 '시세 없음'으로 계속)")


def series(values, end=today):
    return [[(end - _td(days=len(values) - 1 - i)).isoformat(), v] for i, v in enumerate(values)]


falling_then_flat = series([100] * 10 + [95, 90, 85, 80, 75, 72, 70, 70, 70, 71])
rising_then_flat = series([100] * 10 + [105, 110, 115, 120, 125, 128, 130, 130, 130, 129])
still_falling = series([100] * 10 + [98, 96, 94, 92, 90, 88, 86, 80, 75, 70])
check(signal(falling_then_flat)["action"] == "buy", "30일 중 싼 구간 + 하락이 멈춤 → 구매 적기")
check(signal(still_falling)["action"] == "wait", "싼 구간이지만 아직 떨어지는 중 → 구매 대기")
check(signal(rising_then_flat)["action"] == "sell", "비싼 구간 + 상승이 꺾임 → 판매 적기")
check(signal(series([100] * 20))["action"] == "neutral" and signal(series([100] * 5))["action"] == "unknown",
      "평균 근처 → 관망, 기록이 14일보다 짧으면 판단 보류")
check(all(len(signal(s)["reasons"]) >= 4 for s in (falling_then_flat, rising_then_flat)), "판단마다 숫자로 된 근거 줄이 붙는다")

spid_m = 866200104
check(db.put_price(spid_m, 5, {"current": 75, "history": series([80, 79, 78], today - _td(days=5))}) == 0,
      "처음 받은 시세 — 채운 날짜 0")
gap = db.put_price(spid_m, 5, {"current": 70, "history": series([79, 78, 77, 76, 75, 74, 73, 72])})
stored = db.prices(spid_m, 6)[5]
check(gap == 5 and len(stored["history"]) == 8 and stored["current"] == 70,
      "앱이 꺼져 있던 5일: 다시 받은 응답의 일별 시세로 빈 날짜를 채우고 겹치는 날은 덮어쓴다")


class FakeMarket:
    def __init__(self, blocked=False):
        self.calls, self.blocked = [], blocked

    def price(self, spid, grade):
        if self.blocked:
            raise MarketBlocked("robots")
        self.calls.append((spid, grade))
        return {"current": 1000 * grade, "history": series([1000 * grade] * 20)}


def settle(service, seconds=10):
    end = _time.monotonic() + seconds
    while (service._queued or service._running) and _time.monotonic() < end:
        _time.sleep(0.05)


mdb = FcDb(tmp / "storage" / "market.db")
fake_market = FakeMarket()
service = MarketService(fake_market, mdb, ttl_hours=6)
service.want([111, 222], grades=[1, 2])
settle(service)
check(fake_market.calls == [(111, 1), (222, 1), (111, 2), (222, 2)] and set(service.table(111)) == {1, 2},
      "비교하는 두 카드는 강화 단계를 번갈아 받아 같이 채운다")
service.want([111], grades=[1, 2])
settle(service)
check(len(fake_market.calls) == 4, "6시간 안에 받은 시세는 다시 받지 않는다")
with mdb._conn() as conn:   # 앱이 하루 꺼져 있었던 것처럼
    conn.execute("UPDATE market_prices SET fetched_at = '2000-01-01T00:00:00' WHERE spid = 222")
    conn.execute("UPDATE market_prices SET fetched_at = ? WHERE spid = 111",
                 ((__import__("datetime").datetime.now() - _td(days=1)).isoformat(timespec="seconds"),))
fake_market.calls.clear()
queued = service.catch_up(limit=10)
settle(service)
check(queued == 2 and sorted(fake_market.calls) == [(111, 1), (111, 2)] and not service.table(222),
      "다시 켜면 30일 안에 본 카드의 밀린 시세를 따라잡고, 30일 넘게 안 본 카드는 지운다")
blocked = MarketService(FakeMarket(blocked=True), mdb)
blocked.want([333], grades=[1, 2])
settle(blocked)
check(blocked.blocked and not blocked._queued, "robots.txt 가 막으면 남은 요청을 모두 멈춘다")

print("\nF2009 · F2010 · F2011 · F2012 · NF2004  화면 (AppTest)")
os.environ["FCLAB_SETTINGS"] = str(tmp / "settings.toml")


def app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)


def keys(at) -> set[str]:
    return {str(getattr(btn, "key", "")) for btn in at.button}


at = app().run()
check(any("GMS_KEY" in e.value for e in at.error), "키가 없으면 키 누락 안내")

os.environ["GMS_KEY"] = "dummy-for-offline-check"
empty_toml = toml.replace((tmp / "storage").as_posix(), (tmp / "empty_storage").as_posix())
(tmp / "empty.toml").write_text(empty_toml, encoding="utf-8")
shutil.copytree(real.storage_dir / "meta", tmp / "empty_storage" / "meta", dirs_exist_ok=True)
with patch.object(llm, "make_chat_model", lambda s: FakeChat()), patch.object(llm, "make_embeddings", fake_embeddings), \
        patch("data_prep.wiki_api.WikiClient", lambda *a_, **k: FakeWiki()):
    os.environ["FCLAB_SETTINGS"] = str(tmp / "empty.toml")
    import streamlit as st  # noqa: E402
    st.cache_resource.clear()
    at = app().run()
    check(any("build_index.py" in w.value for w in at.warning), "색인이 비어 있으면 색인 스크립트 안내")

    os.environ["FCLAB_SETTINGS"] = str(tmp / "settings.toml")
    st.cache_resource.clear()
    at = app().run()
    check(not at.exception and any(k.startswith("top_") for k in keys(at)), "홈: 파워 랭킹 카드가 보인다")

    at.text_input(key="q").input(a["name"]).run()
    check(f"res_{a['player_id']}" in keys(at), f"홈: 이름 검색 '{a['name']}' → 카드 결과 (네트워크 없이)")
    at.button(key=f"res_{a['player_id']}").click().run()
    check(at.session_state["page"] == "player" and at.session_state["player_id"] == a["player_id"], "카드를 누르면 선수 페이지")
    check(any('class="fc-ai"' in m.value for m in at.markdown), "선수 페이지: AI 한줄 스카우팅이 보인다 (LLM)")
    check(any("강화 단계별 시세" in m.value for m in at.markdown) and any("시세 기능이 꺼져" in c.value for c in at.caption),
          "선수 페이지: 강화 단계별 시세 칸 (점검에서는 꺼 둔다 — 데이터센터를 부르지 않는다)")
    check(any(a["intro"][:40] in m.value for m in at.markdown) if a.get("intro") else True, "위키 소개를 자르지 않고 보여 준다")
    at.button(key="sample_프로 데뷔는 언제, 어느 팀에서 했어?").click().run()
    check(len(at.session_state["chats"][a["player_id"]]) == 2, "예시 질문 → 질문 · 답변이 이 선수 대화에 쌓인다 (F2009)")
    check(any("답변에 사용한 근거" in e.label for e in at.expander), "답변 아래에 인용한 근거 목록 (F2008)")
    at.button(key="fav_toggle").click().run()

    at.session_state["player_id"] = b["player_id"]
    at.session_state["page"] = "player"
    at.run()
    check(not at.session_state["chats"].get(b["player_id"]), "다른 선수로 바꾸면 대화가 섞이지 않는다 (F2009)")
    at.button(key="make_intro").click().run()
    check(f"intro:{b['player_id']}" in at.session_state["results"], "AI 선수 리포트(특화 기능)가 근거와 함께")
    check(any(str(getattr(btn, "key", "")) == "to_compare" for btn in at.button), "선수 상세: '비교하기' 버튼 (모달)")
    at.session_state["cmp_a"], at.session_state["cmp_b"] = a["player_id"], b["player_id"]
    at.session_state["page"] = "compare"
    at.run()
    check(at.session_state["page"] == "compare" and any('class="fc-panel fc-compare"' in m.value for m in at.markdown),
          "비교: 두 선수 스탯 차트")
    at.button(key="make_compare").click().run()
    check(f"compare:{a['player_id']}:{b['player_id']}" in at.session_state["results"], "AI 커리어 비교(특화 기능)")
    at.button(key="cmp_b_pick_again").click().run()
    at.text_input(key="cmp_b_pick_name").input(a["name"]).run()
    person_keys = [k for k in keys(at) if k.startswith("cmp_b_pick_p_")]
    check(bool(person_keys), f"비교 선택기: 이름 입력 → 이름이 들어간 선수 카드 {len(person_keys)}명")
    at.button(key=person_keys[0]).click().run()
    seasons = at.selectbox(key="cmp_b_pick_card").options
    check(len(seasons) >= 1 and at.session_state["cmp_b_pick_who"] == int(person_keys[0].rsplit("_", 1)[1]),
          f"선수 카드를 누르면 그 선수의 시즌만 ({len(seasons)}개)")

    at.button(key="nav_home").click().run()
    at.text_input(key="q").input("대한민국 출신 공격수").run()
    at.button(key="ai_search").click().run()
    items = (at.session_state["explore_results"] or {}).get("items", [])
    check(bool(items) and f"ai_{items[0]['player_id']}" in keys(at), f"AI로 찾기 → 조건에 맞는 카드 (F2011): {items}")

    at.text_input(key="q").input("태클이 좋은 수비형 미드필더").run()
    at.button(key="q_clear").click().run()   # AI 결과 지우기
    at.text_input(key="q").input("태클이 좋은 수비형 미드필더").run()
    check(any(k.startswith("sem_") for k in keys(at)) or any("AI로 찾기" in i.value for i in at.info),
          "이름이 아닌 조건을 적으면: 분석된 선수 중 비슷한 선수 + 'AI로 찾기' 안내 (요청 7)")
    at.button(key="q_clear").click().run()
    check(at.session_state["q"] == "" and any(k.startswith("top_") for k in keys(at)), "✕ 지우기 → 첫 화면 (요청 6)")

    at.button(key="nav_players").click().run()
    check(at.session_state["page"] == "players" and any(k.startswith("pl_") for k in keys(at)),
          "상단 '선수' → 전체 선수 찾기 페이지 (요청 8)")
    at.button(key="back_players").click().run()
    check(at.session_state["page"] == "home", "← 뒤로 → 이전 페이지 (요청 6 · 8)")
    at.button(key="nav_ranking").click().run()
    at.button(key="nav_brand").click().run()
    check(at.session_state["page"] == "home", "로고를 누르면 홈 (요청 1)")

    at.button(key="nav_ranking").click().run()
    check(not at.exception and any(k.startswith("rank_open_") for k in keys(at)), "파워 랭킹: 줄마다 상세보기 버튼 (요청 9)")
    FcDb(tmp / "storage" / "fclab.db").put_gk_games(
        [{**r, "match_id": f"m{i}"} for i, r in enumerate(gk_games(fake_detail("x", keeper["spid"], 777, (0, 0), (7, 2))) * 3)
         if r["spid"] == keeper["spid"]])
    at.session_state["rank_group"] = "골키퍼"
    at.session_state["rank_sort"] = None   # 포지션을 바꾼 것처럼 — 기본 정렬을 다시 고르게
    at.run()
    check(not at.exception and at.session_state["rank_sort"] == "선방률(경기 기록)"
          and any("선방률 71%" in m.value for m in at.markdown),
          f"파워 랭킹 골키퍼: 선방률이 기본 정렬 · 줄마다 선방률 ({keeper['name']} 3경기 · 유효슈팅 21 · 실점 6)")
    at.session_state["rank_group"] = "전체"
    at.session_state["rank_sort"] = None
    at.run()
    first = next(k for k in keys(at) if k.startswith("rank_open_"))
    at.button(key=first).click().run()
    check(at.session_state["page"] == "player", "상세보기 → 선수 페이지")
    at.button(key="nav_tactics").click().run()
    at.button(key="tac_power").click().run()
    filled = [s for s in ("GK", "LB", "LCB", "RCB", "RB", "LDM", "RDM", "LAM", "CAM", "RAM", "ST") if at.session_state[f"tac_{s}"]]
    check(len(filled) >= 1 and any('class="fc-pitch"' in m.value for m in at.markdown),
          f"전술 추천: 파워 랭킹으로 채우기 → 경기장에 {len(filled)}자리 (표본 {len(sample)}명이라 다 못 채울 수 있다)")
    if len(filled) >= 7:
        at.button(key="make_tactic").click().run()
        check(any(k.startswith("tactic:") for k in at.session_state["results"]), "AI 전술 추천(특화 기능)이 근거와 함께")
    at.button(key="nav_favorites").click().run()
    check(f"favs_{a['player_id']}" in keys(at), "즐겨찾기: 담은 선수가 보인다")
    at.button(key="nav_me").click().run()
    at.text_input(key="link_nick").input("테스트").run()
    at.button(key="link").click().run()
    check(not at.exception, "계정 연결: 넥슨 키 없이도 오류 안내만 (요청 12)")
    at.text_input(key="nickname").input("테스트").run()
    at.button(key="load_me").click().run()
    check(not at.exception, "내 기록: 넥슨 키 없이도 오류 안내만 (트레이스백 없음)")

    outside = next(c for c in __import__("json").loads((real.storage_dir / "meta" / "spid.json").read_text(encoding="utf-8"))
                   if str(c["id"]) not in {p["player_id"] for p in sample})
    at.session_state["player_id"] = str(outside["id"])
    at.session_state["page"] = "player"
    at.run()
    check(not at.exception and any(outside["name"] in m.value for m in at.markdown),
          f"분석 안 된 카드({outside['name']})도 바로 열린다 — 카드 정보는 네트워크 없이")
    check(not at.exception, "화면에 예외(트레이스백)가 없다 (NF2004)")

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'모두 통과' if not FAILED else f'실패 {len(FAILED)}건'}")
sys.exit(1 if FAILED else 0)
