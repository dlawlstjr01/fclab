"""선수 강화 규칙 — FC 온라인 공식 공지 '(26.4 기준) 선수 강화 성공 확률, 강화 실패 시 복구 확률 및 크리티컬 확률 안내'와 같다.

- 성공 확률은 강화 부스트 게이지 100% 일 때 값이다. 게이지가 모자라면 [100% 일 때 확률] × [게이지 비율] (공지 그대로).
  게이지는 소모 선수의 가치로 정해진다 — 정확한 값은 데이터센터 '강화 부스트 도우미'가 계산한다 (data_prep/datacenter.boost).
- 실패하면 등급이 내려간다. 몇 강으로 '복구'될지는 아래 표의 확률로 정해지고, 부스트 게이지와는 상관없다.
  같은 선수를 소모하면 그 소모 선수의 강화 등급에 따라 더 높은 등급으로 복구될 수 있다 (2024.12.5 점검 이후 표).
- 크리티컬 강화(+2): 1~4강에서 같은 선수를 소모해 성공했을 때만, 소모한 같은 선수의 등급에 따른 확률로.
- 강화 보호권 · BP 강화 등급 보호를 쓰지 않았을 때의 표다.
출처: https://fconline.nexon.com/news/notice/view?n4ArticleSN=5106 · 동일 선수 복구 확률 상세표(공지 링크)
게임 업데이트로 확률이 바뀌면 이 파일만 고친다.
"""

from __future__ import annotations

import random

import numpy as np

MAX_GRADE = 13
SUCCESS = {1: 100, 2: 81, 3: 64, 4: 50, 5: 26, 6: 15, 7: 7, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}   # 지금 등급 → 성공 % (게이지 100%)
GRADE_OVR = {1: 0, 2: 1, 3: 2, 4: 4, 5: 6, 6: 8, 7: 11, 8: 15, 9: 17, 10: 19, 11: 21, 12: 24, 13: 27}   # +1 대비 OVR


def _table(spec: str) -> dict[int, float]:
    return {int(g): float(p) for g, p in (part.split(":") for part in spec.split())}


# 실패 → x강으로 복구될 확률 % (같은 선수를 소모하지 않았을 때) — 지금 등급: {복구 등급: %}
RECOVER = {grade: _table(spec) for grade, spec in {
    1: "1:100", 2: "1:100", 3: "1:65 2:35", 4: "1:55 2:45", 5: "1:35 2:40 3:25", 6: "1:10 2:32 3:36 4:22",
    7: "1:4 2:10 3:30 4:35 5:21", 8: "1:2 2:4 3:10 4:28 5:35 6:21", 9: "1:1 2:2 3:4 4:10 5:28 6:34 7:21",
    10: "1:1 2:1 3:2 4:4 5:10 6:28 7:34 8:20", 11: "1:1 2:1 3:1 4:2 5:4 6:9 7:28 8:34 9:20",
    12: "1:1 2:1 3:1 4:1 5:2 6:3 7:9 8:28 9:34 10:20",
}.items()}

# 실패 → x강으로 복구될 확률 % (같은 선수를 소모했을 때) — (지금 등급, 소모한 같은 선수의 등급): {복구 등급: %}
RECOVER_SAME = {key: _table(spec) for key, spec in {
    (2, 1): "1:80 2:20", (2, 2): "2:100",
    (3, 1): "1:55 2:35 3:10", (3, 2): "2:80 3:20", (3, 3): "3:100",
    (4, 1): "1:45 2:35 3:15 4:5", (4, 2): "2:55 3:35 4:10", (4, 3): "3:80 4:20", (4, 4): "4:100",
    (5, 1): "1:28 2:38 3:29 4:5", (5, 2): "2:45 3:35 4:15 5:5", (5, 3): "3:55 4:35 5:10", (5, 4): "4:80 5:20",
    (5, 5): "5:100",
    (6, 1): "1:10 2:28 3:38 4:24", (6, 2): "2:28 3:38 4:29 5:5", (6, 3): "3:45 4:35 5:15 6:5", (6, 4): "4:55 5:35 6:10",
    (6, 5): "5:80 6:20", (6, 6): "6:100",
    (7, 1): "1:4 2:10 3:28 4:36 5:22", (7, 2): "2:10 3:28 4:38 5:24", (7, 3): "3:28 4:38 5:29 6:5",
    (7, 4): "4:45 5:35 6:15 7:5", (7, 5): "5:55 6:35 7:10", (7, 6): "6:80 7:20", (7, 7): "7:100",
    (8, 1): "1:2 2:3 3:10 4:28 5:35.5 6:21.5", (8, 2): "2:4 3:10 4:28 5:36 6:22", (8, 3): "3:10 4:28 5:38 6:24",
    (8, 4): "4:28 5:38 6:29 7:5", (8, 5): "5:45 6:35 7:15 8:5", (8, 6): "6:55 7:35 8:10", (8, 7): "7:80 8:20",
    (8, 8): "8:100",
    (9, 1): "1:1 2:1 3:4 4:10 5:28 6:34.5 7:21.5", (9, 2): "2:2 3:4 4:10 5:28 6:35 7:21",
    (9, 3): "3:4 4:10 5:28 6:36 7:22", (9, 4): "4:10 5:28 6:38 7:24", (9, 5): "5:28 6:38 7:29 8:5",
    (9, 6): "6:45 7:35 8:15 9:5", (9, 7): "7:55 8:35 9:10", (9, 8): "8:80 9:20", (9, 9): "9:100",
    (10, 1): "1:1 2:1 3:1 4:4 5:10 6:28 7:34 8:21", (10, 2): "2:1 3:1 4:4 5:10 6:28 7:34.5 8:21.5",
    (10, 3): "3:2 4:4 5:10 6:28 7:35 8:21", (10, 4): "4:4 5:10 6:28 7:36 8:22", (10, 5): "5:10 6:28 7:38 8:24",
    (10, 6): "6:28 7:38 8:29 9:5", (10, 7): "7:45 8:35 9:15 10:5", (10, 8): "8:55 9:35 10:10", (10, 9): "9:80 10:20",
    (10, 10): "10:100",
    (11, 1): "1:1 2:1 3:1 4:1 5:4 6:10 7:28 8:33.5 9:20.5", (11, 2): "2:1 3:1 4:1 5:4 6:10 7:28 8:34 9:21",
    (11, 3): "3:1 4:1 5:4 6:10 7:28 8:34.5 9:21.5", (11, 4): "4:2 5:4 6:10 7:28 8:35 9:21",
    (11, 5): "5:4 6:10 7:28 8:36 9:22", (11, 6): "6:10 7:28 8:38 9:24", (11, 7): "7:28 8:38 9:29 10:5",
    (11, 8): "8:45 9:35 10:15 11:5", (11, 9): "9:55 10:35 11:10", (11, 10): "10:80 11:20", (11, 11): "11:100",
    (12, 1): "1:1 2:1 3:1 4:1 5:1 6:3.5 7:10 8:27.5 9:33.5 10:20.5", (12, 2): "2:1 3:1 4:1 5:1 6:4 7:10 8:28 9:33.5 10:20.5",
    (12, 3): "3:1 4:1 5:1 6:4 7:10 8:28 9:34 10:21", (12, 4): "4:1 5:1 6:4 7:10 8:28 9:34.5 10:21.5",
    (12, 5): "5:2 6:4 7:10 8:28 9:35 10:21", (12, 6): "6:4 7:10 8:28 9:36 10:22", (12, 7): "7:10 8:28 9:38 10:24",
    (12, 8): "8:28 9:38 10:29 11:5", (12, 9): "9:45 10:35 11:15 12:5", (12, 10): "10:55 11:35 12:10",
    (12, 11): "11:80 12:20", (12, 12): "12:100",
}.items()}

# 크리티컬(+2) % — 같은 선수를 소모해 성공했을 때, (지금 등급, 소모한 같은 선수의 등급)
CRITICAL = {(1, 1): 5.0, (2, 1): 1.5, (2, 2): 3.0, (3, 1): 0.5, (3, 2): 1.0, (3, 3): 2.0,
            (4, 1): 0.25, (4, 2): 0.5, (4, 3): 0.75, (4, 4): 1.0}


def success_rate(grade: int, boost: float) -> float:
    """지금 등급에서 한 번 강화할 때 성공 % — 100% 게이지 확률 × 게이지 비율."""
    return SUCCESS[grade] * max(0.0, min(boost, 100.0)) / 100


def failure_table(grade: int, same_grade: int | None = None) -> dict[int, float]:
    """실패했을 때 복구 등급 분포 %. 같은 선수 등급은 지금 등급을 넘을 수 없다 (표가 그 범위까지)."""
    if same_grade and grade >= 2:
        return RECOVER_SAME[(grade, min(same_grade, grade))]
    return RECOVER[grade]


def attempt(grade: int, boost: float, same_grade: int | None = None, rng: random.Random = random) -> tuple[int, str]:
    """한 번 강화 → (새 등급, '크리티컬' · '성공' · '실패')."""
    if rng.random() * 100 < success_rate(grade, boost):
        critical = CRITICAL.get((grade, min(same_grade, grade))) if same_grade else None
        if critical and rng.random() * 100 < critical:
            return min(grade + 2, MAX_GRADE), "크리티컬"
        return grade + 1, "성공"
    table = failure_table(grade, same_grade)
    roll, acc = rng.random() * sum(table.values()), 0.0
    for result, pct in sorted(table.items()):
        acc += pct
        if roll < acc:
            return result, "실패"
    return max(table), "실패"


def transition(target: int, boost: float, same_grade: int | None = None) -> np.ndarray:
    """한 번 강화할 때 등급이 바뀌는 확률 행렬 (행 · 열 = +1 ~ +target, 목표 등급은 도착하면 머문다)."""
    P = np.zeros((target, target))
    for g in range(1, target):
        p = success_rate(g, boost) / 100
        crit = (CRITICAL.get((g, min(same_grade, g))) or 0) / 100 if same_grade else 0
        P[g - 1, min(g + 2, target) - 1] += p * crit
        P[g - 1, min(g + 1, target) - 1] += p * (1 - crit)
        table = failure_table(g, same_grade)
        total = sum(table.values())
        for result, pct in table.items():
            P[g - 1, result - 1] += (1 - p) * pct / total
    P[target - 1, target - 1] = 1.0
    return P


def odds(start: int, target: int, boost: float, same_grade: int | None = None) -> dict:
    """start 에서 target 까지 — 모의 실험이 아니라 확률로 정확히 (등급 = 상태인 마르코프 연쇄).

    돌려주는 값: mean(평균 시도 횟수, 닿을 수 없으면 None), median · p90(이 횟수 안에 50% · 90%),
    within{시도 횟수: 그 안에 닿을 확률}, curve[(시도 횟수, 누적 확률)], first{결과 등급: 첫 시도 확률}.
    """
    P = transition(target, boost, same_grade)
    i, j = start - 1, target - 1

    def reach(t: int) -> float:
        return float(np.linalg.matrix_power(P, t)[i, j]) if t > 0 else 0.0

    def quantile(q: float, cap: int = 10**9) -> int | None:
        if boost <= 0 or reach(cap) < q:
            return None
        lo, hi = 1, cap
        while lo < hi:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if reach(mid) >= q else (mid + 1, hi)
        return lo

    mean = None
    if boost > 0:
        transient = np.eye(j) - P[:j, :j]
        mean = float(np.linalg.solve(transient, np.ones(j))[i])
    p90 = quantile(0.9)
    end = max(p90 or 0, 10) if p90 else 1000
    marks = sorted({1, 2, 3, 5, *(int(round(end * k / 40)) for k in range(1, 41))} - {0})
    return {
        "mean": mean, "median": quantile(0.5), "p90": p90,
        "within": {t: reach(t) for t in (1, 10, 50, 100, 500, 1000)},
        "curve": [(t, reach(t)) for t in marks],
        "first": {g + 1: float(P[i, g]) for g in range(target) if P[i, g] > 0},
    }


def simulate(start: int, target: int, boost: float, same_grade: int | None = None, runs: int = 10_000,
             max_tries: int = 200, seed: int | None = None) -> dict:
    """start 에서 target 까지 강화를 runs 번 해 본다 (한 번에 최대 max_tries 번 시도).

    돌려주는 값: 목표 달성 확률, 시도 횟수(평균 · 중앙 · 90%), 시도 횟수 분포, 처음 한 번의 결과 분포.
    """
    rng = random.Random(seed)
    tries_needed: list[int] = []
    failed = 0
    first = {"크리티컬": 0, "성공": 0, "실패": 0}
    first_grades: dict[int, int] = {}
    for _ in range(runs):
        grade, tries = start, 0
        while grade < target and tries < max_tries:
            grade, outcome = attempt(grade, boost, same_grade, rng)
            if tries == 0:
                first[outcome] += 1
                first_grades[grade] = first_grades.get(grade, 0) + 1
            tries += 1
        if grade >= target:
            tries_needed.append(tries)
        else:
            failed += 1
    tries_needed.sort()

    def quantile(q: float) -> int | None:
        return tries_needed[min(len(tries_needed) - 1, int(q * len(tries_needed)))] if tries_needed else None

    histogram: dict[int, int] = {}
    for t in tries_needed:
        histogram[t] = histogram.get(t, 0) + 1
    return {
        "runs": runs, "reached": len(tries_needed) / runs, "gave_up": failed / runs,
        "mean": sum(tries_needed) / len(tries_needed) if tries_needed else None,
        "median": quantile(0.5), "p90": quantile(0.9), "histogram": histogram,
        "first": {k: v / runs for k, v in first.items()}, "first_grades": {g: n / runs for g, n in sorted(first_grades.items())},
    }
