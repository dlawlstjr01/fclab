"""선수 · 시즌 이미지를 한 번 받아 두고 앱이 직접 내준다 — 다음부터는 넥슨 CDN 을 다시 부르지 않고 바로 보인다.

- 받은 이미지는 static/cache/ 에 WebP 로 저장하고 Streamlit 정적 파일(app/static/...)로 내준다.
  CDN 원본 PNG(128px, 25~50KB)가 WebP 로 3~5KB 가 되어 카드 묶음이 훨씬 빨리 뜬다.
- 어떤 주소를 받았는지는 SQLite images 표에 남긴다. 없는 이미지(CDN 이 403/404)도 남겨,
  화면이 없는 액션 이미지를 다시 부르지 않고 바로 얼굴 이미지를 쓴다.
- 아직 안 받은 이미지는 원래 주소를 그대로 쓰고, 백그라운드에서 받아 둔다 (화면을 기다리게 하지 않는다).
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image

log = logging.getLogger("fclab.images")

URL_PREFIX = "app/static/cache/"   # Streamlit 이 static/ 폴더를 내주는 주소 (server.enableStaticServing)
MISSING = (403, 404)


class ImageCache:
    def __init__(self, db, folder: Path, workers: int = 4, max_pending: int = 400):
        self.db, self.folder = db, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.known: dict[str, tuple[str, int]] = db.images()   # 주소 → (파일 이름, 상태)
        self.max_pending = max_pending
        self.pending: set[str] = set()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fclab-img")
        self.session = requests.Session()
        self._frames: dict[str, dict] = {}

    def resolve(self, url: str | None) -> str | None:
        """받아 둔 사본 주소 · 없는 이미지면 None · 아직 모르면 원래 주소(그리고 백그라운드로 받기 시작)."""
        if not url:
            return None
        file, status = self.known.get(url, ("", 0))
        if status in MISSING:
            return None
        if status == 200 and (self.folder / file).exists():
            return URL_PREFIX + file
        self._schedule(url)
        return url

    def frame(self, url: str | None) -> dict | None:
        """시즌 카드 배경 그림에서 카드 몸통이 차지하는 칸(비율 x0, y0, w, h)과 OVR 자리가 밝은지 — 받아 둔 사본으로 한 번 계산.

        시즌마다 몸통 크기가 달라(ICON TM 은 넓고 날개 장식이 있다) 선수 그림 · OVR 을 몸통에 맞춰 놓으려고 쓴다.
        장식에 끌리지 않게 불투명한 칸이 가장 많은 열 · 행의 절반 이상인 곳만 몸통으로 본다. 아직 안 받았으면 None.
        """
        if not url:
            return None
        if url in self._frames:
            return self._frames[url]
        file, status = self.known.get(url, ("", 0))
        path = self.folder / file
        if status != 200 or not path.exists():
            if status == 0:
                self._schedule(url)
            return None
        try:
            pixels = np.asarray(Image.open(path).convert("RGBA")).astype(float)
        except OSError:
            return None
        opaque = pixels[..., 3] > 128
        height, width = opaque.shape
        cols, rows = opaque.sum(0), opaque.sum(1)
        xs, ys = np.flatnonzero(cols >= 0.5 * cols.max()), np.flatnonzero(rows >= 0.5 * rows.max())
        if not len(xs) or not len(ys):
            return None
        x0, x1, y0, y1 = xs[0], xs[-1] + 1, ys[0], ys[-1] + 1
        fw, fh = x1 - x0, y1 - y0
        patch = pixels[int(y0 + 0.06 * fh):int(y0 + 0.30 * fh), int(x0 + 0.05 * fw):int(x0 + 0.32 * fw)]   # OVR 자리
        rgb = patch[..., :3][patch[..., 3] > 128]
        light = bool(len(rgb)) and float((rgb @ [0.2126, 0.7152, 0.0722]).mean() / 255) > 0.6
        self._frames[url] = {"box": (x0 / width, y0 / height, fw / width, fh / height), "light": light}
        return self._frames[url]

    def _schedule(self, url: str) -> None:
        with self.lock:
            if url in self.pending or len(self.pending) >= self.max_pending:
                return
            self.pending.add(url)
        self.pool.submit(self._fetch, url)

    def _fetch(self, url: str) -> None:
        try:
            r = self.session.get(url, timeout=15)
            if r.status_code in MISSING:
                self._remember(url, "", r.status_code)
                return
            r.raise_for_status()
            image = Image.open(io.BytesIO(r.content))
            image = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
            file = hashlib.sha1(url.encode()).hexdigest()[:20] + ".webp"
            image.save(self.folder / file, "WEBP", quality=82, method=4)
            self._remember(url, file, 200)
        except Exception as exc:  # noqa: BLE001 — 못 받으면 다음에 다시 (화면은 원래 주소로 계속)
            log.warning("이미지 받기 실패 %s: %s", url, exc)
        finally:
            with self.lock:
                self.pending.discard(url)

    def _remember(self, url: str, file: str, status: int) -> None:
        self.known[url] = (file, status)
        self.db.put_image(url, file, status)
