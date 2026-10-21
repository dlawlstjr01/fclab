"""강화 단계별 시세 — 데이터센터 '시세' 그래프 응답 읽기 · 등락 · 사기/팔기 판단 · BP 표기.

    POST https://fconline.nexon.com/datacenter/PlayerPriceGraph   spid=<카드>&n1strong=<강화 단계>

요청은 data_prep/datacenter.py 가 한다 (넥슨 Open API 가 아니라 홈페이지 화면용 응답 — 주의 사항은 거기에).
화면 구조가 바뀌면 아래 패턴(상수)만 고치면 된다. 못 읽으면 None — 앱은 '시세 없음'으로 계속 돈다.
"""

from __future__ import annotations

import re
from datetime import date

MAX_GRADE = 13

# 응답 HTML 에서 값을 찾는 패턴 — 화면이 바뀌면 여기만 고친다
CURRENT_PRICE = re.compile(r"현재가</span>\s*<strong[^>]*title=\"([\d,]+)\"")
HISTORY_BLOCK = re.compile(r"var\s+json1\s*=\s*\{(.*?)\}", re.S)
HISTORY_TIME = re.compile(r"\"time\"\s*:\s*\[(.*?)\]", re.S)
HISTORY_VALUE = re.compile(r"\"value\"\s*:\s*\[(.*?)\]", re.S)
QUOTED = re.compile(r"\"([^\"]*)\"")


class MarketError(RuntimeError):
    """시세를 받지 못했다 (네트워크 · 서버). 화면은 '잠시 뒤 다시'로 안내한다."""


class MarketBlocked(MarketError):
    """robots.txt 가 막았거나 확인하지 못했다 — 이때는 요청하지 않는다."""


def _dates(labels: list[str], today: date) -> list[date]:
    """'6.25' 처럼 연도 없는 날짜 → 날짜. 마지막 점이 가장 최근이고 오늘보다 뒤일 수 없다는 것으로 연도를 정한다."""
    out: list[date] = []
    year = today.year
    later: tuple[int, int] | None = None
    for label in reversed(labels):
        month, day = (int(x) for x in label.split("."))
        if later is None:
            if (month, day) > (today.month, today.day):
                year -= 1
        elif (month, day) > later:   # 거꾸로 가다 달이 커지면 해가 바뀐 것
            year -= 1
        later = (month, day)
        out.append(date(year, month, day))
    return list(reversed(out))


def parse_price(html: str, today: date) -> dict | None:
    """응답 HTML → {current, history: [[YYYY-MM-DD, BP], ...]}. 현재가를 못 찾으면 None."""
    found = CURRENT_PRICE.search(html)
    if not found:
        return None
    history: list[list] = []
    block = HISTORY_BLOCK.search(html)
    if block:
        times = HISTORY_TIME.search(block.group(1))
        values = HISTORY_VALUE.search(block.group(1))
        if times and values:
            labels = [t for t in QUOTED.findall(times.group(1)) if re.fullmatch(r"\d{1,2}\.\d{1,2}", t)]
            prices = [int(v) for v in QUOTED.findall(values.group(1)) if v.isdigit()]
            if labels and len(labels) == len(prices):
                history = [[d.isoformat(), v] for d, v in zip(_dates(labels, today), prices)]
    return {"current": int(found.group(1).replace(",", "")), "history": history}


def change(history: list[list], days: int = 30) -> dict | None:
    """최근 days 일 — 최저 · 최고 · 처음 대비 등락률(%). 점이 2개보다 적으면 None."""
    if len(history) < 2:
        return None
    last = date.fromisoformat(history[-1][0])
    recent = [v for d, v in history if (last - date.fromisoformat(d)).days < days]
    if len(recent) < 2 or not recent[0]:
        return None
    return {"low": min(recent), "high": max(recent), "pct": 100 * (recent[-1] - recent[0]) / recent[0]}


# ── 사기 · 팔기 판단 (규칙 기반) ─────────────────────────────────────
# 숫자로 설명할 수 있는 규칙만 쓴다 — 사용자가 '왜 그렇게 봤는지'를 근거 줄로 확인할 수 있게.
#   위치  = 최근 30일 최저~최고 사이에서 지금 값이 어디쯤인지 (0 = 최저, 100 = 최고)
#   평균比 = 30일 평균보다 몇 % 싼지/비싼지
#   흐름  = 최근 3일 등락 (하락이 멈췄나 · 상승이 꺾였나)
# 이벤트 · 새 시즌 출시 같은 외부 요인은 모른다 — 화면에 '참고용'으로 적는다.
MIN_POINTS = 14       # 이보다 기록이 짧으면 판단하지 않는다
LOW_ZONE, HIGH_ZONE = 30, 70
AVG_GAP = 5.0         # 30일 평균과 이만큼(%) 벌어져야 싸다/비싸다고 본다
TURN = 2.0            # 최근 3일 등락이 이 안쪽이면 '멈췄다'고 본다
SIGNALS = {
    "buy": "구매 적기", "wait": "구매 대기 (하락 중)", "neutral": "관망",
    "hold": "보유 (상승 중)", "sell": "판매 적기", "unknown": "판단 보류",
}


def _ago(history: list[list], days: int) -> int | None:
    """마지막 날에서 days 일 전(또는 그 직전)의 값."""
    last = date.fromisoformat(history[-1][0])
    older = [v for d, v in history if (last - date.fromisoformat(d)).days >= days]
    return older[-1] if older else None


def signal(history: list[list], current: int | None = None) -> dict:
    """가격 추이 → {action, label, reasons[], metrics{}}. current 가 있으면 그 값을 '지금'으로 쓴다."""
    if len(history) < MIN_POINTS:
        return {"action": "unknown", "label": SIGNALS["unknown"], "metrics": {},
                "reasons": [f"시세 기록이 {len(history)}일뿐이라 판단하지 않습니다 ({MIN_POINTS}일 이상 필요)."]}
    now = current or history[-1][1]
    last = date.fromisoformat(history[-1][0])
    month = [v for d, v in history if (last - date.fromisoformat(d)).days < 30]
    low, high = min(month), max(month)
    avg = sum(month) / len(month)
    pos = 50.0 if high == low else 100 * (now - low) / (high - low)
    vs_avg = 100 * (now / avg - 1)
    d3, d7 = _ago(history, 3), _ago(history, 7)
    chg3 = 100 * (now / d3 - 1) if d3 else 0.0
    chg7 = 100 * (now / d7 - 1) if d7 else 0.0
    recent = history[-31:]
    daily = [100 * (b / a - 1) for (_, a), (_, b) in zip(recent, recent[1:]) if a]
    vol = (sum(x * x for x in daily) / len(daily)) ** 0.5 if daily else 0.0
    metrics = {"position": pos, "vs_avg": vs_avg, "chg3": chg3, "chg7": chg7, "low": low, "high": high,
               "avg": avg, "volatility": vol}

    reasons = [f"지금 {bp(now)} — 최근 30일 최저 {bp(low)} ~ 최고 {bp(high)} 사이에서 {pos:.0f}% 위치",
               f"30일 평균({bp(round(avg))})보다 {abs(vs_avg):.1f}% {'싸다' if vs_avg < 0 else '비싸다'}",
               f"최근 3일 {chg3:+.1f}% · 7일 {chg7:+.1f}%"]
    if pos <= LOW_ZONE and vs_avg <= -AVG_GAP:
        if chg3 >= -TURN:
            action = "buy"
            reasons.append("30일 중 싼 구간이고 하락이 멈췄거나 반등을 시작했다")
        else:
            action = "wait"
            reasons.append("싼 구간이지만 아직 내려가는 중 — 하락이 멈출 때까지 기다리는 편이 낫다")
    elif pos >= HIGH_ZONE and vs_avg >= AVG_GAP:
        if chg3 <= TURN:
            action = "sell"
            reasons.append("30일 중 비싼 구간이고 상승이 꺾였거나 멈췄다")
        else:
            action = "hold"
            reasons.append("비싼 구간이지만 아직 오르는 중 — 가진 카드라면 조금 더 지켜볼 만하다 (새로 사기엔 비쌈)")
    else:
        action = "neutral"
        reasons.append("30일 평균 근처 — 뚜렷한 싸고 비싼 신호가 없다")
    if vol >= 8:
        reasons.append(f"하루 변동이 큰 카드(평균 ±{vol:.0f}%) — 신호가 자주 바뀐다")
    return {"action": action, "label": SIGNALS[action], "reasons": reasons, "metrics": metrics}


def bp(value: int | None) -> str:
    """BP 를 읽기 쉽게 — 1조 2,500억 · 15억 7,545만 · 7,545만 · 4,500 (게임 · 데이터센터와 같은 단위)."""
    if value is None:
        return "-"
    if value < 10**4:
        return f"{value:,}"
    man = round(value / 10**4)   # 만 단위로 반올림
    if man >= 10**8:             # 1조 이상은 억까지만
        jo, eok = divmod(round(value / 10**8), 10**4)
        return f"{jo:,}조 {eok:,}억" if eok else f"{jo:,}조"
    if man >= 10**4:
        eok, rest = divmod(man, 10**4)
        return f"{eok:,}억 {rest:,}만" if rest else f"{eok:,}억"
    return f"{man:,}만"
