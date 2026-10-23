"""켜 둔 앱을 지키는 두 가지 — UI 진입점(app.py)이 매 실행 맨 처음에 부른다.

1. quiet_connection_reset — Windows 의 asyncio(Proactor)는 브라우저 연결이 갑자기 끊기면(창 닫기 · 새로 고침 · 네트워크)
   이미 끊긴 소켓을 shutdown 하다 ConnectionResetError(WinError 10054)를 내고, 터미널에
   'Exception in callback _ProactorBasePipeTransport._call_connection_lost' 트레이스백을 남긴다. 상대가 먼저 끊은 연결이라
   무시해도 되는 오류 — 그 경우만 조용히 소켓을 닫고 나머지 정리(서버에서 떼기 · 끝났다는 표시)는 그대로 한다.
2. reload_changed — Streamlit 은 코드가 바뀌면 '같은 창에서 다음에 실행될 때'만 바뀐 모듈을 다시 읽는다. 브라우저를 새로 고치면
   (새 창) 예전 모듈이 그대로 남아 예전 화면이 보이거나 'object has no attribute' 에러가 났다. 매 실행 처음에 프로젝트 모듈의
   파일 수정 시각을 읽은 때와 비교해, 하나라도 바뀌었으면 프로젝트 모듈을 모두 내린다 (Streamlit 이 같은 창에서 하는 것과 같다 —
   하나만 내리면 그 모듈을 import 해 둔 다른 모듈이 예전 것을 계속 쓴다). 그다음 app.py 의 import 가 새 코드를 읽는다.
   이번 실행에 새로 읽은 모듈은 remember() 로 바로 적어 둔다 (다음 실행 전에 바뀌어도 알아채게).
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
# 데이터를 쥔 객체(창구 · 시세 · 능력치 · 수집 스레드)는 cache_resource 에 남아, 다시 켜야 새 코드로 만들어진다
SERVICE_PARTS = ("services", "data_prep", "rag", "config", "llm", "vector_store")


def quiet_connection_reset() -> None:
    """이미 끊긴 브라우저 연결의 shutdown 오류(WinError 10054 · 10053)만 조용히 — Windows 에서만, 한 번만 바꿔 끼운다."""
    if sys.platform != "win32":
        return
    from asyncio import proactor_events

    transport = proactor_events._ProactorBasePipeTransport
    if getattr(transport, "_fclab_quiet", False):
        return
    original = transport._call_connection_lost

    def call_connection_lost(self, exc):
        try:
            original(self, exc)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # 원래 함수는 shutdown 에서 멈췄다 (protocol.connection_lost 는 이미 불렸다) — 그 뒤 정리만 마저 한다
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            server = self._server
            if server is not None:
                server._detach()
                self._server = None
            self._called_connection_lost = True

    transport._call_connection_lost = call_connection_lost
    transport._fclab_quiet = True


def _scan(root: Path) -> tuple[list[str], list[str]]:
    """(프로젝트 모듈 이름, 읽은 뒤 파일이 바뀐 모듈 이름). 처음 보거나 새로 읽힌 모듈은 지금 수정 시각을 적어 둔다 — 적어 둔 것은
    sys 에 두어 이 모듈이 다시 읽혀도 남는다."""
    seen: dict[str, tuple[int, int]] = sys.__dict__.setdefault("_fclab_module_mtimes", {})
    mine, changed = [], []
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None) if module is not None else None
        if name == "__main__" or not path or not str(path).endswith(".py"):
            continue
        try:
            file = Path(path).resolve()
            if root not in file.parents:
                continue
            mtime = file.stat().st_mtime_ns
        except OSError:
            continue
        mine.append(name)
        before = seen.get(name)
        if before is None or before[0] != id(module):
            seen[name] = (id(module), mtime)
        elif mtime != before[1]:
            changed.append(name)
    return mine, sorted(changed)


def reload_changed(base: Path | None = None) -> list[str]:
    """읽은 뒤 파일이 바뀐 프로젝트 모듈이 있으면 프로젝트 모듈을 모두 sys.modules 에서 내린다. 돌려주는 값: 파일이 바뀐 모듈 이름.
    base 는 점검용 (기본은 이 프로젝트 폴더)."""
    mine, changed = _scan((base or BASE).resolve())
    if changed:
        seen = sys.__dict__["_fclab_module_mtimes"]
        for name in mine:
            sys.modules.pop(name, None)
            seen.pop(name, None)
    return changed


def remember(base: Path | None = None) -> None:
    """이번 실행에 새로 읽은 프로젝트 모듈의 수정 시각을 적어 둔다 (app.py 의 import 뒤에)."""
    _scan((base or BASE).resolve())


def needs_restart(changed: list[str]) -> bool:
    """바뀐 것이 데이터를 쥔 쪽(services · data_prep · rag · config · llm · vector_store)이면 그 객체는 다시 켜야 새 코드로."""
    return any(name.split(".")[0] in SERVICE_PARTS for name in changed)
