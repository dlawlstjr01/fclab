"""설계서 부록(프롬프트 원문)을 data_prep/prompts.py 에서 그대로 뽑아 02_RAG_파이프라인_설계서.md 끝에 넣는다.

    python docs/make_prompt_appendix.py

문서와 코드의 프롬프트가 어긋나지 않게 하려고 손으로 옮겨 적지 않는다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data_prep import prompts  # noqa: E402

MARK = "## 부록. 프롬프트 원문"
doc = ROOT / "docs" / "02_RAG_파이프라인_설계서.md"
text = doc.read_text(encoding="utf-8").split(MARK)[0].rstrip() + "\n\n"

blocks = [
    ("프롬프트 1 — 검색어 추출 (F2003)", prompts.KEYWORD_SYSTEM),
    ("프롬프트 2 — 후보 문서 선별 (F2004)", prompts.SELECT_SYSTEM),
    ("프롬프트 3 — 근거 기반 답변 생성 (F2007)", prompts.ANSWER_SYSTEM),
    ("프롬프트 3 작업 규칙 — 선수 소개", prompts.TASK_RULES["intro"].strip()),
    ("프롬프트 3 작업 규칙 — 두 선수 비교", prompts.TASK_RULES["compare"].strip()),
    ("모든 프롬프트 끝에 붙는 보안 규칙 (근거 속 지시문 무시)", prompts.GUARD.strip()),
    ("조건 탐색 선별 (F2011)", prompts.EXPLORE_SYSTEM),
    ("AI 한줄 스카우팅 (F2010)", prompts.SCOUT_SYSTEM),
    ("AI 전술 추천 (F2010)", prompts.TACTIC_SYSTEM),
    ("AI 추천 스쿼드 설명 (F2010)", prompts.SQUAD_SYSTEM),
    ("AI 코치 (F2010)", prompts.COACH_SYSTEM),
]
parts = [MARK, "", "`data_prep/prompts.py` 에서 자동으로 뽑았다 (`python docs/make_prompt_appendix.py`). `{max_keywords}` 같은 자리는 settings.toml 값으로 채워진다.", ""]
for title, body in blocks:
    parts += [f"### {title}", "", "```text", body, "```", ""]
doc.write_text(text + "\n".join(parts), encoding="utf-8")
print(f"{doc.name} 에 프롬프트 {len(blocks)}개를 넣었습니다.")
