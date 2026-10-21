"""저장된 위키 문서를 지금 설정(청크 크기 · 중복 길이)으로 다시 색인한다. (F2005 · 검증 4.2)

    python reindex_wiki.py

설정마다 컬렉션 이름이 달라서(player_wiki_c1000_o200 …) 값을 바꿔 색인해도 이전 결과가 지워지지 않는다.
settings.toml 의 chunk_size · chunk_overlap 을 바꾼 뒤 실행하고, 컬렉션별 청크 수를 비교한다.
"""

from __future__ import annotations

import sys

import config
import llm
from data_prep.collector import load_stored_docs
from vector_store import PlayerStore


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    settings = config.load_settings()
    try:
        store = PlayerStore(settings, llm.make_embeddings(settings))
    except llm.MissingKeyError as exc:
        sys.exit(str(exc))
    folders = sorted(p for p in settings.docs_dir.glob("*") if p.is_dir()) if settings.docs_dir.exists() else []
    added = sum(store.index_wiki_docs(f.name, load_stored_docs(settings.docs_dir, f.name)) for f in folders)
    print(f"선수 {len(folders)}명의 저장 문서 → {settings.wiki_collection} 에 새 청크 {added}개")
    for name, count in sorted(store.collection_counts().items()):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
