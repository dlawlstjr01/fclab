"""답변 본문의 [근거 N] 표기 읽기 — 실제로 인용한 근거만 화면에 남긴다. (F2008)"""

from __future__ import annotations

import re

# [근거 3], [근거 3, 5], [근거 3,5] 를 찾는다.
CITE_PATTERN = re.compile(r"\[근거\s*([\d,\s]+)\]")


def cited_numbers(answer: str) -> set[int]:
    return {
        int(n) for group in CITE_PATTERN.findall(answer)
        for n in group.split(",") if n.strip().isdigit()
    }


def cited_evidence(answer: str, evidence: list[dict]) -> list[dict]:
    """답변이 인용한 근거만 번호 순으로. 목록에 없는 번호(LLM 이 지어낸 번호)는 무시한다. 없으면 빈 목록."""
    numbers = cited_numbers(answer)
    return [e for e in evidence if e["number"] in numbers]
