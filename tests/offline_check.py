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

    def invoke(self, messages, **_options):
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
        if "선수 카드가 맞는지" in system:
            self.calls.append("explore")
            return SimpleNamespace(content='[{"no": 2, "match": true, "reason": "조건과 맞는 이유"}, '
                                           '{"no": 1, "match": false, "reason": "국적이 다름"}, '
                                           '{"no": 99, "match": true, "reason": "없는 번호"}]')
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
# 골키퍼 선방률 화면 점검용 — 선방 기록을 넣을 1명(keeper) + 기록이 없는 1명 (순위에 둘 다 보여야 한다)
keepers = [p for p in with_wiki if p.get("position_group") == "골키퍼"][:2]
keeper = keepers[0]
sample += [k for k in keepers if k not in sample]
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
check(len(found["items"]) == 1 and found["items"][0]["player_id"] in by_id,
      f"LLM 이 맞다고 한 번호만, 없는 번호(99)는 버린다: {found['items']}")
check([e["reason"] for e in found["excluded"]] == ["국적이 다름"] and found["matched"] == 1
      and found["checked"] >= 2, "맞지 않는 후보는 이유와 함께 따로 — 화면의 '제외한 후보'")

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
board = leaderboard([{**keeper, "player_id": "x"}, enough], "골키퍼", "전체", "save_rate", min_matches=99)
check([r["player"]["player_id"] for r in board] == [enough["player_id"]],
      "선방률 순위: 표본이 있는 골키퍼는 랭커 최소 경기 수와 상관없이 넣는다")
board = leaderboard([{**keeper, "player_id": "x"}, enough], "골키퍼", "전체", "save_rate", min_matches=1)
check([r["player"]["player_id"] for r in board] == [enough["player_id"], "x"],
      "선방률 순위: 표본이 없는 골키퍼도 빼지 않고 표본이 있는 골키퍼 뒤에")

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
    """데이터센터 대신 — 선수 검색(이름 → 그 선수 카드들의 강화별 현재가)과 일별 시세."""

    def __init__(self, blocked=False, listing=None):
        self.calls, self.blocked, self.listing = [], blocked, listing or {}

    def player_list(self, name, seasons=None):
        if self.blocked:
            raise MarketBlocked("robots")
        self.calls.append(("list", name, tuple(seasons or ())))
        return [{"spid": spid, "name": name, "prices": {g: 5000 * g for g in (1, 2)}, "ovr": 100, "pay": 20,
                 "positions": [], "rating": None} for spid in self.listing.get(name, [])]

    def price(self, spid, grade):
        if self.blocked:
            raise MarketBlocked("robots")
        self.calls.append(("graph", spid, grade))
        return {"current": 5000 * grade, "history": series([5000 * grade] * 20)}


def settle(service, seconds=10):
    end = _time.monotonic() + seconds
    while (service._queued or service._running) and _time.monotonic() < end:
        _time.sleep(0.05)


names = {111: "가", 222: "나", 333: "다", 444: "라"}
mdb = FcDb(tmp / "storage" / "market.db")
fake_market = FakeMarket(listing={"가": [111, 112], "나": [222]})
service = MarketService(fake_market, mdb, ttl_hours=6, name_of=names.get, refresh_minutes=0)
service.want([111, 222], grades=[1, 2])
settle(service)
check(fake_market.calls[:2] == [("list", "가", ()), ("list", "나", ())]
      and fake_market.calls[2:] == [("graph", 111, 1), ("graph", 222, 1), ("graph", 111, 2), ("graph", 222, 2)],
      "현재가는 선수 검색 1번으로 먼저, 일별 시세는 그 뒤 — 두 카드는 강화 단계를 번갈아")
check(set(service.table(112)) == {1, 2} and service.table(111)[2]["current"] == 10_000,
      "선수 검색 한 번에 그 선수의 다른 카드 시세도 같이 저장된다")
service.want([111], grades=[1, 2])
settle(service)
check(len(fake_market.calls) == 6, "현재가 30분 · 일별 시세 6시간 안에 받은 것은 다시 받지 않는다")
mdb.put_listing([{"spid": 111, "prices": {1: 5000}, "name": "가"}])
mdb.put_listing([{"spid": 111, "prices": {1: 6000}, "name": "가"}])
with mdb._conn() as conn:
    snaps = [v for (v,) in conn.execute("SELECT value FROM market_snapshots WHERE spid = 111 AND grade = 1 ORDER BY at")]
check(snaps == [5000, 6000] and service.table(111)[1]["prev"]["value"] == 5000,
      "같은 시세는 다시 기록하지 않고, 바뀐 시세만 시각과 함께 쌓는다 (직전 기록 대비)")
with mdb._conn() as conn:   # 앱이 하루 꺼져 있었던 것처럼
    yesterday = (__import__("datetime").datetime.now() - _td(days=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE market_prices SET fetched_at = '2000-01-01T00:00:00' WHERE spid IN (222, 112)")
    conn.execute("UPDATE market_watch SET viewed_at = '2000-01-01T00:00:00' WHERE spid = 222")
    conn.execute("UPDATE market_prices SET fetched_at = ? WHERE spid = 111", (yesterday,))
    conn.execute("UPDATE market_history_fetched SET fetched_at = ? WHERE spid = 111", (yesterday,))
fake_market.calls.clear()
queued = service.catch_up(limit=10)
settle(service)
check(queued == 3 and sorted(fake_market.calls) == [("graph", 111, 1), ("graph", 111, 2), ("list", "가", ())]
      and not service.table(222),
      "다시 켜면 최근 본 카드의 현재가 · 밀린 일별 시세를 따라잡고, 30일 넘게 안 본 카드는 지운다")
fake_market.calls.clear()
service.want([333], grades=[1])
settle(service)
check([c for c in fake_market.calls if c[0] == "list"] == [("list", "다", ()), ("list", "다", (0,))]
      and ("graph", 333, 13) in fake_market.calls and service.table(333)[1]["current"] == 5000,
      "이름 검색에 없는 카드는 시즌을 좁혀 한 번 더, 그래도 없으면 일별 시세 응답의 현재가로")
blocked = MarketService(FakeMarket(blocked=True), mdb, name_of=names.get, refresh_minutes=0)
blocked.want([444], grades=[1, 2])
settle(blocked)
check(blocked.blocked and not blocked._queued, "robots.txt 가 막으면 남은 요청을 모두 멈춘다")

print("\n스쿼드 메이커 · 팀컬러 · 능력치 DB · 강화 확률")
from data_prep.datacenter import parse_foot, parse_teamcolor_detail  # noqa: E402
from data_prep.enhance import GRADE_OVR, odds, simulate, skill_stars  # noqa: E402
from data_prep.tactics import BASE_POSITION, OVR_GRID, ovr_at  # noqa: E402
from services.squad import slot_ovr  # noqa: E402
from services.stats import PAGE, PASSES, StatsCrawler  # noqa: E402

check([skill_stars(3, g) for g in (4, 5, 8, 11, 13)] == [(3, 0), (3, 1), (3, 2), (3, 3), (3, 3)]
      and skill_stars(5, 13) == (5, 1) and skill_stars(6, 11) == (6, 0),
      "개인기 별: 5 · 8 · 11강에서 하나씩 더하고 최대 6개 (3성 → 13강 6성, 5성 → 6성까지만)")
from data_prep.players import is_live_season  # noqa: E402

check([is_live_season(n) for n in ("25 LIVE (25 LIVE)", "17 LIVE (17 LIVE)", "25 PL (25 Premium Live)",
                                    "26 TOTS (26 Team Of The Season)", "ICON (ICON)", None)]
      == [True, True, False, False, False, False],
      "LIVE 시즌 판별 — '25 LIVE' 는 LIVE, Premium Live(PL) · TOTS · ICON 은 아님 (스쿼드 이미지 바꾸기는 LIVE 만 막는다)")
import ui_parts  # noqa: E402
from data_prep.tactics import FORMATIONS  # noqa: E402
from views.widgets import season_family, sorted_seasons  # noqa: E402

fake_catalog = SimpleNamespace(
    seasons={113: {"className": "25IM - S&P (25 ICONS MATCH - Show & Prove)"},
             114: {"className": "25IM - FB (25 ICONS MATCH - Fan Boost)"},
             222: {"className": "20 TOTY-N (20 Team Of The Nominated)"}, 101: {"className": "ICON (ICON)"}},
    cards=[{"id": s * 1_000_000 + i} for s, n in ((113, 3), (114, 2), (222, 1), (101, 4)) for i in range(n)])
merged = dict(sorted_seasons(fake_catalog, -1))
check(season_family("25IM - S&P (25 ICONS MATCH - Show & Prove)") == ("25IM", "25 ICONS MATCH")
      and merged.get((113, 114)) == "25IM (25 ICONS MATCH) · 5장" and (222,) in merged and (101,) in merged,
      f"시즌 고르기: 갈래만 다른 시즌(25IM - S&P · 25IM - FB)은 한 줄로 합친다, TOTY-N 은 따로 — {merged}")
from views.tactics import applied_html, list_order, teamcolor_note  # noqa: E402

check(list_order(FORMATIONS["4-2-3-1"], "4-2-3-1") == ["ST", "LAM", "CAM", "RAM", "LDM", "RDM", "LB", "LCB", "RCB", "RB", "GK"],
      "자리별 선수 목록은 FC 온라인처럼 공격수부터 (같은 줄은 왼쪽부터, 골키퍼가 맨 아래)")
row = ui_parts.card_row({"name": "가", "player_id": "1"}, {"summary": {"스피드": 100, "패스": 90}}, grade=1,
                        tc_summary={"패스": 5})
check('<span>패스 <b class="up" title="팀컬러 +5">95</b>' in row and "<span>스피드 <b>100</b>" in row,
      "선수 줄: 팀컬러로 오른 요약 능력치(특정 능력치 효과 포함)는 더해서 초록으로")
chelsea = ("첼시", 4, ["전체 능력치 +4", "볼 컨트롤 +3", "크로스 +3"])
line = applied_html({"groups": {"소속": [{"name": chelsea[0], "level": 4, "count": 11, "effects": chelsea[2], "icon": ""}],
                                "강화": [], "특성": []}, "unknown": 2})
check(">첼시</b>" in line and "4단계" in line and "전체 능력치 +4, 볼 컨트롤 +3, 크로스 +3" in line
      and "적용되는 팀컬러 없음" in line and "2명의 팀컬러 정보를 받는 중" in line
      and teamcolor_note({"tc_bonus": {"applied": [chelsea]}}) == "🎨 첼시 4단계: 전체 능력치 +4, 볼 컨트롤 +3, 크로스 +3",
      "AI 추천 스쿼드 · 자리별 선수: 적용되는 팀컬러(소속 · 강화 · 특성)와 효과를 그대로 (전체 + 팀컬러별 능력치)")
from services.teamcolor import squad_teamcolors  # noqa: E402

grid = {pos: 100 for pos in OVR_GRID} | {"CAM": 120, "CM": 117}
check(ovr_at("LCM", 5, grid) == (117 + GRADE_OVR[5], True) and ovr_at("ST", 1, None, [("ST", 110)], 105) == (110, True)
      and ovr_at("RB", 3, None, [("ST", 110)], 105) == (105 + GRADE_OVR[3], False),
      "자리 OVR = 그 포지션의 OVR(16칸 → 선수 검색의 포지션 목록) + 강화, 없으면 대표 OVR 로 추정 표시")
check(slot_ovr({"ovr": 125, "usage_position": "ST"}, "LS", 3) == (125 + GRADE_OVR[3], True)
      and slot_ovr({"ovr": 120, "positions": [("CAM", 120)]}, "ST", 1) == (120, False),
      "AI 추천 스쿼드도 같은 자리 OVR — 데일리 차트 카드는 쓰인 포지션의 OVR")
check(parse_foot("<strong>L5</strong> – R3") == {"L": 5, "R": 3, "main": "L"} and parse_foot("L5 - <strong>R5</strong>")["main"] == "R",
      "주발 — 굵은 쪽이 주발 (선수 검색 · 선수 정보 둘 다)")
gold = parse_teamcolor_detail('<div class="tit"><strong>금빛 물결 : </strong><span>8강 이상의 선수들로 구성된 팀컬러입니다.</span></div>'
                              '<div class="level lvu1"><div class="num">5</div><ul><li>전체 능력치 +3</li><li>-</li></ul></div>'
                              '<div class="level lvu2"><div class="num">8</div><ul><li>전체 능력치 +4</li></ul></div>')
check(gold and gold["min_grade"] == 8 and [lv["count"] for lv in gold["levels"]] == [5, 8],
      "강화 팀컬러 단계(lvu1 · lvu2)와 '8강 이상' 조건을 읽는다")

tcs = [{"id": 1016, "name": "FC 바르셀로나", "kind": "클럽", "max_level": 4, "max_count": 11, "icon": "",
        "detail": {"levels": [{"level": i + 1, "count": c, "effects": [f"전체 능력치 +{e}"]}
                              for i, (c, e) in enumerate(((3, 1), (6, 3), (8, 3), (11, 4)))]}},
       {"id": 10001, "name": "금빛 물결", "kind": "강화", "max_level": 2, "max_count": 8, "icon": "", "detail": gold}]
members = ([{"name": f"바르사{i}", "grade": 8, "teamcolors": {"소속": [{"id": 1016, "name": "FC 바르셀로나"}], "관계": []}}
            for i in range(6)] + [{"name": f"기타{i}", "grade": 1, "teamcolors": {"소속": [], "관계": []}} for i in range(4)]
           + [{"name": "모름", "grade": 9, "teamcolors": None}])
team = squad_teamcolors(members, tcs, {})
club, wave = team["groups"]["소속"][0], team["groups"]["강화"][0]
check(club["count"] == 6 and club["level"] == 2 and club["next"]["count"] == 8 and wave["count"] == 7 and wave["level"] == 1
      and team["unknown"] == 1,
      "팀컬러: 클럽 6명 → 2단계(다음 8명), 8강 이상 7명 → 금빛 물결 1단계, 팀컬러를 모르는 선수는 따로 센다")

from services.cardinfo import bonus_stats  # noqa: E402

card = {"ovr": 127, "stats": {"속력": 131, "짧은 패스": 123}, "summary": {"스피드": 131},
        "position_ovr": {"CAM": 127, "CB": 108}}
eight_five = bonus_stats(card, 8, 5, [])
barca = bonus_stats(card, 8, 1, [["전체 능력치 +4", "짧은 패스 +3", "볼 컨트롤 +3"]])
check(eight_five["ovr"] == 146 and eight_five["stats"]["속력"] == 150 and eight_five["position_ovr"]["CB"] == 127
      and eight_five["exact"] and barca["stats"]["짧은 패스"] == 145 and barca["stats"]["속력"] == 150 and not barca["exact"],
      "추가 능력치 바로 계산 = 데이터센터 값 (마라도나 8강 · 적응도 5 → OVR 146 · 속력 150, 바르셀로나 Lv.4 → 짧은 패스 145)")

exact, mc = odds(1, 5, 100), simulate(1, 5, 100, runs=20000, seed=3)
check(abs(exact["mean"] - mc["mean"]) < 0.4 and exact["median"] == mc["median"] and odds(1, 2, 0)["mean"] is None,
      f"강화 확률(정확 계산) 평균 {exact['mean']:.2f}번 ≈ 모의 실험 {mc['mean']:.2f}번 · 게이지 0% 면 닿지 않음")

sdb = FcDb(tmp / "storage" / "stats.db")
sdb.put_card_stats([{"spid": 300000001, "name": "가", "ovr": 80, "pay": 10, "positions": [("ST", 80)], "stats": {"sprintspeed": 70}}])
sdb.put_card_stats([{"spid": 300000001, "name": "가", "ovr": 80, "pay": 12, "positions": [("ST", 80)], "stats": {"finishing": 75}}])
row = sdb.card_stats([300000001])[300000001]
check(row["stats"] == {"sprintspeed": 70, "finishing": 75} and row["pay"] == 12 and row["pay_change"]["before"] == 10,
      "능력치는 받을 때마다 칸별로 합치고, 급여가 바뀌면(로스터 패치) 전 값과 함께 남긴다")
check(sdb.put_teamcolors([{"id": 1, "name": "가", "kind": "클럽"}]) == [] and sdb.put_teamcolors(
      [{"id": 1, "name": "가", "kind": "클럽"}, {"id": 2, "name": "나", "kind": "국가"}]) == [2]
      and {t["id"]: bool(t["first_seen"]) for t in sdb.teamcolors()[0]} == {1: False, 2: True},
      "팀컬러 목록을 DB 에 — 다시 받을 때 새로 생긴 팀컬러만 '처음 본 날'과 함께 더한다")
sdb.save_squad("내 스쿼드", {"formation": "4-4-2", "squad": {"GK": {"pid": "1", "grade": 3}}})
saved_squads = sdb.squads()
sdb.delete_squad("내 스쿼드")
check(saved_squads[0]["data"]["squad"]["GK"]["grade"] == 3 and not sdb.squads(), "스쿼드 저장 · 불러오기 · 지우기")


class FakeList:
    """선수 검색 대신 — 시즌 하나에 카드 n장 (포지션 · OVR 이 고르게). 200장까지만 돌려준다."""

    def __init__(self, n, spread=40, base=400_000_000):
        positions = list(__import__("data_prep.datacenter", fromlist=["POSITION_FILTER"]).POSITION_FILTER)
        self.cards = [{"spid": base + i, "name": f"c{i}", "ovr": 60 + i % spread, "pay": 5, "height": 165 + i % 37,
                       "weight": 60 + i % 31, "positions": [(positions[i % len(positions)], 60 + i % spread)]} for i in range(n)]
        self.calls = 0

    def player_list(self, seasons=None, positions=None, ovr_min=None, ovr_max=None, skills=(), height=None, weight=None):
        self.calls += 1
        rows = [c for c in self.cards if (not positions or c["positions"][0][0] == positions[0])
                and (ovr_min is None or c["ovr"] >= ovr_min) and (ovr_max is None or c["ovr"] <= ovr_max)
                and (not height or height[0] <= c["height"] <= height[1])
                and (not weight or weight[0] <= c["weight"] <= weight[1])]
        rows.sort(key=lambda c: -c["ovr"])
        return [{**c, "stats": {k: 1 for k in skills}} for c in rows[:PAGE]]


fake_list = FakeList(4000)
crawler = StatsCrawler(fake_list, sdb, interval=0)
crawler._split(400)
for part in sdb.crawl_parts(400):
    for _ in range(len(PASSES) - 1):
        crawler._fill(part)
        part = {**part, "passes": part["passes"] + 1}
stored = sdb.card_stats([c["spid"] for c in fake_list.cards])
check(len(stored) == 4000 and all(len(r["stats"]) == 36 for r in stored.values()),
      f"시즌 4,000장(200장 넘음) → 포지션 · OVR 구간으로 나눠 모두 받고, 능력치 36칸이 다 모인다 (요청 {fake_list.calls}번)")
calls = fake_list.calls
crawler._split(400)
check(all(p["passes"] == len(PASSES) for p in sdb.crawl_parts(400)) and fake_list.calls - calls < 60,
      "다시 훑어도 바뀐 카드가 없으면 능력치 칸은 다시 받지 않는다 (급여 · OVR 만 갱신)")
dense = FakeList(4500, spread=1, base=401_000_000)   # 모두 OVR 60 — 포지션(15)마다 300장, 200장이 넘는다 ('25 LIVE 의 낮은 OVR 처럼)
crawler = StatsCrawler(dense, sdb, interval=0)
crawler._split(401)
got = sdb.card_stats([c["spid"] for c in dense.cards])
dense_parts = sdb.crawl_parts(401)
check(len(got) == 4500 and all(p["cards"] < PAGE for p in dense_parts) and any(":h" in p["part"] for p in dense_parts),
      f"OVR 하나가 200장을 넘어도 키 구간으로 반씩 나눠 빠짐없이 받는다 ({len(got)}/4500장 · 조각 {len(dense_parts)}개 · "
      f"요청 {dense.calls}번)")
import threading as _threading  # noqa: E402

import data_prep.datacenter as dcm  # noqa: E402

turn_client = dcm.DatacenterClient(1.0)
turn_client.allowed = lambda path="": True
sent: list[str] = []


def _fake_send(path, data, method):
    sent.append(_threading.current_thread().name)
    _time.sleep(0.05)
    return ""


turn_client._send = _fake_send


def _bulk():
    turn_client.set_priority(dcm.BULK)
    for _ in range(6):
        turn_client._post("/x", {})


def _screen():
    for _ in range(3):
        turn_client._post("/x", {})
        _time.sleep(0.1)   # 카드 상세 두 번 사이처럼 잠깐 쉬어도


with patch.object(dcm, "QUIET", 0.4):
    bulk_t, screen_t = _threading.Thread(target=_bulk, name="bulk"), _threading.Thread(target=_screen, name="screen")
    bulk_t.start()
    _time.sleep(0.12)
    screen_t.start()
    screen_t.join()
    bulk_t.join()
first, last = sent.index("screen"), len(sent) - 1 - sent[::-1].index("screen")
check(sent[first:last + 1] == ["screen"] * 3 and sent.count("bulk") == 6,
      f"데이터센터 창구 순서: 화면 요청(카드 상세 · 팀컬러)이 오면 능력치 모으기는 끝날 때까지 끼어들지 않는다 ({' '.join(sent)})")
sdb.put_crawl_part("402:ST:60-60", 402, {"position": "ST", "ovr_min": 60, "ovr_max": 60}, PAGE, 1)   # 예전 방식의 '일부만' 조각
check(StatsCrawler(dense, sdb, interval=0).next_job() == ("split", 402),
      "예전에 OVR 하나가 200장을 넘어 일부만 받은 시즌은 먼저 다시 나눈다 (빠진 카드까지)")

print("\nF2009 · F2010 · F2011 · F2012 · NF2004  화면 (AppTest)")
os.environ["FCLAB_SETTINGS"] = str(tmp / "settings.toml")


def app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)


def keys(at) -> set[str]:
    return {str(getattr(btn, "key", "")) for btn in at.button}


def pitch_slots_of(at) -> list[dict]:
    """경기장(브라우저 부품)이 받은 자리들 — AppTest 에서는 알 수 없는 요소(bidi_component)로 보인다."""
    found = []

    def walk(node):
        children = getattr(node, "children", None)
        for child in (children.values() if isinstance(children, dict) else children or []):
            proto = getattr(child, "proto", None)
            if getattr(child, "type", "") == "bidi_component" and "fclab_pitch" in proto.component_name:
                found.extend(json.loads(proto.json)["slots"])
            walk(child)

    walk(at._tree)
    return found


at = app().run()
check(any("GMS_KEY" in e.value for e in at.error), "키가 없으면 키 누락 안내")

os.environ["GMS_KEY"] = "dummy-for-offline-check"
empty_toml = toml.replace((tmp / "storage").as_posix(), (tmp / "empty_storage").as_posix())
(tmp / "empty.toml").write_text(empty_toml, encoding="utf-8")
shutil.copytree(real.storage_dir / "meta", tmp / "empty_storage" / "meta", dirs_exist_ok=True)
with patch.object(llm, "make_chat_model", lambda s: FakeChat()), patch.object(llm, "make_embeddings", fake_embeddings), \
        patch("data_prep.wiki_api.WikiClient", lambda *a_, **k: FakeWiki()), \
        patch("services.images.ImageCache._schedule", lambda self, url: None):   # 이미지도 받지 않는다 (네트워크 없음)
    os.environ["FCLAB_SETTINGS"] = str(tmp / "empty.toml")
    import streamlit as st  # noqa: E402
    st.cache_resource.clear()
    at = app().run()
    check(any("build_index.py" in w.value for w in at.warning), "색인이 비어 있으면 색인 스크립트 안내")

    os.environ["FCLAB_SETTINGS"] = str(tmp / "settings.toml")
    seed = FcDb(tmp / "storage" / "fclab.db")   # 강화 팀컬러 하나 — 선수 상세의 '초록 +N 은 강화 팀컬러만' 점검용
    seed.put_teamcolors([{"id": 10001, "name": "금빛 물결", "kind": "강화", "max_level": 2, "max_count": 8,
                          "effects": ["전체 능력치 +4"], "icon": ""}])
    seed.put_teamcolor_detail(10001, {"name": "금빛 물결", "description": "8강 이상의 선수들로 구성된 팀컬러입니다.",
                                      "levels": [{"level": 1, "count": 5, "effects": ["전체 능력치 +3"]},
                                                 {"level": 2, "count": 8, "effects": ["전체 능력치 +4"]}], "min_grade": 8})
    seed.put_teamcolors([{"id": 1001, "name": "점검 클럽", "kind": "클럽", "max_level": 1, "max_count": 3,   # 특정 능력치만 오르는 소속 팀컬러
                          "effects": ["짧은 패스 +3"], "icon": ""}])
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
    cmp_db = FcDb(tmp / "storage" / "fclab.db")   # 두 카드의 상세 (데이터센터 대신) — 능력치 비교 점검용
    for p_, ovr_ in ((a, 100), (b, 104)):
        cmp_db.put_card_info(int(p_["spid"]), {"v": 2, "ovr": ovr_, "position": "ST", "pay": 20,
                                               "position_ovr": {pos: ovr_ - 10 for pos in OVR_GRID} | {"ST": ovr_},
                                               "teamcolors": {"소속": [], "관계": [], "강화": []}, "traits": [],
                                               "summary": {"스피드": ovr_}, "stats": {"속력": ovr_, "가속력": ovr_ - 2},
                                               "foot_lr": {"L": 3, "R": 5, "main": "R"}, "clubs": [], "skill_moves": 4})
    at.session_state["cmp_a"], at.session_state["cmp_b"] = a["player_id"], b["player_id"]
    at.session_state["page"] = "compare"
    at.run()
    check(at.session_state["page"] == "compare" and any('class="fc-panel fc-compare"' in m.value for m in at.markdown),
          "비교: 두 선수 스탯 차트")

    def cmp_html() -> str:
        return next((m.value for m in at.markdown if 'class="fc-abcmp"' in m.value), "")

    check('<div class="b win">' in cmp_html() and "<b>104</b>" in cmp_html() and "<b>100</b>" in cmp_html(),
          "비교: 카드 능력치 나란히 — 처음은 +1강 (대표 OVR 100 vs 104, 높은 쪽에 색 · 막대는 차이만큼 짧게)")
    at.button(key=f"pc_grade_{a['spid']}__8").click().run()
    at.selectbox(key=f"pc_adapt_{b['spid']}").select(5).run()
    check(not at.exception and f"<b>{100 + GRADE_OVR[8]}</b>" in cmp_html()
          and '<b>108</b><small class="up">+4</small>' in cmp_html() and '<div class="a win">' in cmp_html(),
          f"비교: A 를 8강 → 대표 OVR {100 + GRADE_OVR[8]}, B 를 적응도 5 → 108 · 초록 +4 (카드마다 강화 · 적응도 · 팀컬러를 골라 비교)")
    at.button(key="make_compare").click().run()
    check(f"compare:{a['player_id']}:{b['player_id']}" in at.session_state["results"], "AI 커리어 비교(특화 기능)")
    at.button(key="cmp_b_pick_again").click().run()
    at.text_input(key="cmp_b_pick_name").input(a["name"]).run()
    person_keys = [k for k in keys(at) if k.startswith("cmp_b_pick_p_")]
    check(bool(person_keys), f"비교 선택기: 이름 입력 → 이름이 들어간 선수 카드 {len(person_keys)}명")
    at.button(key=person_keys[0]).click().run()
    seasons = [k for k in keys(at) if k.startswith("cmp_b_pick_c_")]
    check(len(seasons) >= 1 and at.session_state["cmp_b_pick_who"] == int(person_keys[0].rsplit("_", 1)[1])
          and any('class="fc-tile fc-ssn on"' in m.value and 'class="fc-cv' in m.value for m in at.markdown),
          f"선수 카드를 누르면 그 선수의 시즌만 — 시즌마다 카드 그림(배경 · 액션샷)으로 ({len(seasons)}개)")
    at.button(key=seasons[-1]).click().run()
    check(at.session_state["cmp_b_pick_card"] == int(seasons[-1].rsplit("_", 1)[1]),
          "시즌 카드 그림을 누르면 그 카드로 고른다")

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
    listed = [k for k in keys(at) if k.startswith("rank_open_")]
    check(not at.exception and at.session_state["rank_sort"] == "랭커 경기 수" and len(listed) == len(keepers),
          f"파워 랭킹 골키퍼: 선방 기록이 있어도 기본 정렬은 랭커 경기 수 · 골키퍼 {len(listed)}명 모두")
    at.selectbox(key="rank_sort").select("선방률(경기 기록)").run()
    check(not at.exception and any("선방률 71%" in m.value for m in at.markdown)
          and len([k for k in keys(at) if k.startswith("rank_open_")]) == len(keepers),
          f"선방률 순: 기록이 있는 골키퍼가 위, 기록이 없는 골키퍼도 그 아래에 ({keeper['name']} 3경기 · 유효슈팅 21 · 실점 6)")
    at.session_state["rank_group"] = "전체"
    at.session_state["rank_sort"] = None
    at.run()
    first = next(k for k in keys(at) if k.startswith("rank_open_"))
    at.button(key=first).click().run()
    check(at.session_state["page"] == "player", "상세보기 → 선수 페이지")
    at.button(key="nav_tactics").click().run()
    check(not at.exception and not any(at.session_state["tac_squad"].values())
          and not any(s_["name"] for s_ in pitch_slots_of(at)),
          "스쿼드 메이커: 처음 들어가면 빈 스쿼드 (11자리 모두 비어 있음)")
    at.button(key="tac_power").click().run()
    squad_state = at.session_state["tac_squad"]
    filled = [code for code, v in squad_state.items() if v]
    on_pitch = [s_ for s_ in pitch_slots_of(at) if s_["name"]]
    check(len(filled) >= 1 and len(on_pitch) == len(filled),
          f"스쿼드: 파워 랭킹으로 채우기 → 경기장에 {len(filled)}자리 (표본 {len(sample)}명이라 다 못 채울 수 있다)")
    check(any('class="fc-sum"' in m.value and "총 급여" in m.value for m in at.markdown)
          and any('class="fc-prow compact"' in m.value for m in at.markdown) and any('class="fc-tc"' in m.value for m in at.markdown),
          "스쿼드: 평균 OVR · 총 급여/310 요약 · 자리별 선수 줄(좁은 칸용) · 팀컬러(소속 · 강화 · 관계)")
    code = filled[0]
    spid_t = int(squad_state[code]["pid"])
    app_db = FcDb(tmp / "storage" / "fclab.db")
    app_db.put_card_info(spid_t, {"v": 2, "ovr": 110, "position": BASE_POSITION[code], "pay": 21,
                                  "position_ovr": {pos: 90 for pos in OVR_GRID} | {BASE_POSITION[code]: 108},
                                  "teamcolors": {"소속": [{"id": 1001, "name": "점검 클럽"}], "관계": [], "강화": []}, "traits": [],
                                  "summary": {"스피드": 100}, "foot_lr": {"L": 3, "R": 5, "main": "R"}, "clubs": [],
                                  "stats": {"짧은 패스": 80}, "skill_moves": 4})
    squad_state[code]["grade"] = 5
    at.run()
    expect = 108 + GRADE_OVR[5]
    check(any(s_["code"] == code and s_["sub"].startswith(f"{expect} · ") for s_ in pitch_slots_of(at))
          and any(f">{expect}<small>{code} OVR" in m.value for m in at.markdown),
          f"스쿼드: {code} 자리 OVR = 그 포지션의 OVR 108 + 5강({GRADE_OVR[5]}) = {expect} — 경기장 · 목록 모두")
    card_html = next(s_["html"] for s_ in pitch_slots_of(at) if s_["code"] == code)
    start = card_html.find('<div class="ov">')
    left_stack = card_html[start: card_html.find("</div>", start)]
    check(start >= 0 and left_stack.find('class="fc-pay"') < left_stack.find('class="gd g5"') and 'class="lc"' not in card_html,
          "스쿼드 경기장 카드: 급여(21) 밑에 강화(5)를 같은 왼쪽 칸에 차례로 — 따로 고정해 두어 겹치던 것을 없앴다")
    shown_order = [c for m in at.markdown for c in filled if 'class="fc-prow compact"' in m.value and f">{c} OVR<" in m.value]
    check(shown_order == [c for c in list_order(list(squad_state), at.session_state["tac_formation"]) if c in filled],
          f"스쿼드 자리별 선수: 공격수부터 위로 ({' → '.join(shown_order)})")
    pickable = sorted({k.split("_")[2] for k in keys(at) if k.startswith("tac_img_")})
    meta_cards = json.loads((real.storage_dir / "meta" / "spid.json").read_text(encoding="utf-8"))
    season_names = {s["seasonId"]: s["className"]
                    for s in json.loads((real.storage_dir / "meta" / "seasonid.json").read_text(encoding="utf-8"))}

    def can_swap(c: str) -> bool:   # LIVE 가 아니고 같은 선수의 다른 시즌 카드가 있다
        spid = int(squad_state[c]["pid"])
        return not is_live_season(season_names.get(spid // 1_000_000)) and any(
            m["id"] % 1_000_000 == spid % 1_000_000 and m["id"] != spid for m in meta_cards)

    check(bool(pickable) and pickable == sorted(c for c in filled if can_swap(c)),
          f"스쿼드 선수 이미지: 다른 시즌 액션샷을 그림으로 보여 주고 눌러 고른다 (골키퍼도) — 고를 수 있는 자리 {pickable}")
    img_code = "GK" if "GK" in pickable else pickable[0]
    img_key = next(k for k in sorted(keys(at)) if k.startswith(f"tac_img_{img_code}_") and not k.endswith("_0"))
    at.button(key=img_key).click().run()
    check(at.session_state["tac_squad"][img_code]["img"] == int(img_key.rsplit("_", 1)[1])
          and at.button(key=img_key).disabled and not at.button(key=f"tac_img_{img_code}_0").disabled,
          f"스쿼드 {img_code} 이미지 고르기 → 그 액션샷으로 ('✓ 사용 중'), 기본으로 되돌릴 수 있다")
    at.button(key=f"tac_img_{img_code}_0").click().run()
    row_html = next(m.value for m in at.markdown if 'class="fc-prow compact"' in m.value and f"{code} OVR" in m.value)
    check('class="meta"' in row_html and '<span>스피드 <b>' in row_html and 'class="side"><div class="ovr">' in row_html,
          "스쿼드 자리별 줄: 시즌 · 급여 · 강화 · 주발은 덩어리째 줄바꿈, 능력치는 '이름 숫자'가 갈라지지 않게, 오른쪽은 OVR 만")
    at.text_input(key="tac_save_name").input("점검 스쿼드").run()
    at.button(key="tac_save").click().run()
    at.button(key="tac_clear").click().run()
    at.button(key="tac_load_0").click().run()
    check(at.session_state["tac_squad"].get(code) and at.session_state["tac_squad"][code]["grade"] == 5
          and "불러왔습니다" in (at.session_state["tac_msg"] or ""), "스쿼드 저장 → 비우기 → 불러오기 (강화까지 그대로)")
    at.button(key="tac_reset_all").click().run()
    check(at.session_state["tac_squad"][code]["grade"] is None and at.session_state["tac_squad"][code]["pid"] == str(spid_t),
          "스쿼드 ↺ 초기화 → 강화 · 신규 특성 · 이미지만 처음으로 (선수는 그대로)")
    others_before = {c: v for c, v in at.session_state["tac_squad"].items() if c != code}
    at.button(key=f"tac_remove_{code}").click().run()
    check(code in at.session_state["tac_squad"] and at.session_state["tac_squad"][code] is None
          and {c: v for c, v in at.session_state["tac_squad"].items() if c != code} == others_before
          and not any(s_["code"] == code and s_["name"] for s_ in pitch_slots_of(at))
          and at.button(key=f"tac_remove_{code}").disabled and "뺐습니다" in (at.session_state["tac_msg"] or "")
          and any(f"{code}</span> <span class=\"wait\">비어 있음 — 오른쪽 '➕ 추가'" in m.value for m in at.markdown),
          f"스쿼드 ✕ 빼기 → {code} 자리만 비운다 (다른 자리 · 포메이션 그대로, 빈 자리의 빼기 버튼은 꺼짐)")
    at.button(key="tac_load_0").click().run()   # 뒤 점검(AI 전술 추천)을 위해 다시 채운다

    at.session_state["player_id"] = str(spid_t)
    at.session_state["page"] = "player"
    at.run()

    def grid_html() -> str:
        return next((m.value for m in at.markdown if 'class="fc-ovrgrid"' in m.value), "")

    check(grid_html() != "", "선수 상세: 카드 정보 — 포지션별 오버롤 (받아 둔 상세)")
    at.button(key=f"pc_grade_{spid_t}__8").click().run()
    check(f"<b>{90 + GRADE_OVR[8]}</b>" in grid_html() and 'class="up"' not in grid_html()
          and at.selectbox(key=f"pc_adapt_{spid_t}").options == ["+1", "+5"],
          f"선수 상세: 강화만 8강으로 → 포지션 OVR 90+{GRADE_OVR[8]}, 초록 +N 없음 (강화분은 숫자에) · 적응도는 1 · 5 두 단계만")
    at.selectbox(key=f"pc_adapt_{spid_t}").select(5).run()
    check(f"<b>{90 + GRADE_OVR[8] + 4}</b>" in grid_html() and 'class="up">+4<' in grid_html(),
          f"선수 상세: 적응도 5 → 포지션 OVR 90+{GRADE_OVR[8]}+4, 초록 +4 (적응도로 오른 만큼)")
    enh = at.selectbox(key=f"pc_enh_{spid_t}_8")
    enh.select_index(next(i for i, label in enumerate(enh.options) if "금빛 물결 Lv.2" in label)).run()
    check(f"<b>{90 + GRADE_OVR[8] + 4 + 4}</b>" in grid_html() and 'class="up">+8<' in grid_html()
          and any("금빛 Lv.2 +8" in c.value for c in at.caption),
          f"선수 상세: 거기에 금빛 물결 Lv.2 → OVR {90 + GRADE_OVR[8] + 8}, 초록 +8 = 적응도 4 + 금빛 4 (데이터센터 기본값 +8 과 같다)")

    def ability_html() -> str:
        return next((m.value for m in at.markdown if 'class="fc-abil"' in m.value), "")

    at.selectbox(key=f"pc_tc_{spid_t}").select_index(1).run()
    short = 80 + GRADE_OVR[8] + 4 + 4 + 3
    check(f">{short}</b><small class=\"up\">+{short - 80 - GRADE_OVR[8]}<" in ability_html()
          and f"<b>{90 + GRADE_OVR[8] + 8}" in grid_html() and 'class="it focus" title="팀컬러가 따로 올려 주는 능력치 +3"' in ability_html(),
          f"선수 상세: 소속 팀컬러 '짧은 패스 +3' → 짧은 패스 {short}, 초록 +{short - 80 - GRADE_OVR[8]} = 적응도 4 + 금빛 4 + 소속 3 "
          "(특정 능력치만 오르는 효과도 그 능력치의 +N 에)")
    check(any('★★★★<span class="star-up"' in m.value and ">★★</span>" in m.value for m in at.markdown),
          "선수 상세: 개인기 4성 카드를 8강으로 → 5 · 8강 별 2개를 초록으로 더해 6성")
    check(any('class="fc-qa-head"' in m.value for m in at.markdown), "선수 상세: Q&A · AI 리포트 · 근거 문서를 눈에 띄는 상자로 (제목 · 근거 기반 표시)")
    at.button(key=f"pc_reset_{spid_t}").click().run()
    check(f"<b>{90 + GRADE_OVR[1]}</b>" in grid_html() and 'class="up"' not in grid_html(),
          "선수 상세: ↺ 초기화 → +1강 · 적응도 1 · 팀컬러 없음")
    at.session_state["page"] = "tactics"   # 스쿼드로 돌아가 AI 전술 추천
    at.run()
    if len(filled) >= 7:
        at.button(key="make_tactic").click().run()
        check(any(k.startswith("tactic:") for k in at.session_state["results"]), "AI 전술 추천(특화 기능)이 근거와 함께")
    at.button(key="nav_grow").click().run()
    check(not at.exception and any("공식 강화 확률표" in e.label for e in at.expander), "강화 시뮬: 카드를 고르기 전에는 공식 확률표")
    at.session_state["gr_pick_name"] = a["name"]
    at.session_state["gr_pick_who"] = a["pid"]
    at.session_state["gr_pick_card"] = a["spid"]
    at.run()
    at.button(key="gr_try").click().run()
    run_state = at.session_state["gr_run"]
    check(not at.exception and len(run_state["history"]) == 1 and run_state["history"][0]["to"] == 2
          and any("평균 시도" in m.value for m in at.markdown),
          "강화 시뮬: +1 → +2 는 100% 성공, 목표까지 평균 시도 · 50% · 90% 횟수를 계산")
    check(any('class="fc-growbd"' in m.value and "강화 확률" in m.value for m in at.markdown)
          and any('class="fc-growrec"' in m.value and "<td>1</td>" in m.value for m in at.markdown)
          and any('class="fc-growcard"' in m.value and 'class="pc"' in m.value for m in at.markdown),
          "강화 시뮬: 공식 시뮬레이터처럼 — 큰 카드 · 지금 › 다음 등급 칸 · 강화 확률 · 게이지 칸 · 전체/성공/실패/성공 비율 표")
    at.session_state["gr_target"] = 4
    at.run()
    at.button(key="gr_goal").click().run()
    run_state = at.session_state["gr_run"]
    check(not at.exception and run_state["grade"] >= 4 and run_state["goal"]["reached"]
          and run_state["tries"] == 1 + run_state["goal"]["tries"] and at.button(key="gr_goal").disabled,
          f"강화 시뮬: '+4 까지 강화' → 닿을 때까지 계속 ({run_state['goal']['tries']}번 · 기록 표에 모두 셈)")
    at.checkbox(key="gr_lock").check().run()
    before = (run_state["grade"], run_state["tries"])
    for _ in range(5):
        at.button(key="gr_try").click().run()
    run_state = at.session_state["gr_run"]
    check(run_state["grade"] == before[0] and run_state["tries"] == before[1] + 5 and at.button(key="gr_goal").disabled,
          f"강화 시뮬: 지금 등급 고정 → 5번 강화해도 +{before[0]} 그대로, 결과만 센다 (목표까지 버튼은 꺼짐)")
    at.button(key="nav_players").click().run()
    check(not at.exception and any('class="fc-prow"' in m.value for m in at.markdown) or not at.session_state.get("pl_page") is None,
          "선수 찾기: 공식 스쿼드 메이커처럼 선수 줄 목록")
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
