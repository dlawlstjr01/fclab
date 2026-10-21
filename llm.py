"""LLM · 임베딩 클라이언트와 LLM 응답 읽기 도우미.

GMS 는 OpenAI 와 같은 모양의 API 라서 OpenAI 용 라이브러리를 그대로 쓰고 주소(base_url)만 바꾼다.
"""

from __future__ import annotations

import json
import re
import ssl
from functools import cache

import httpx
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

import config


class MissingKeyError(RuntimeError):
    """.env 에 GMS_KEY 가 없다."""


def _key() -> str:
    key = config.gms_key()
    if not key:
        raise MissingKeyError("GMS_KEY가 없습니다. .env 에 GMS_KEY=발급받은_키 를 넣으세요.")
    return key


@cache
def _http_clients() -> dict:
    """채팅 · 임베딩이 같이 쓰는 HTTP 연결 — 클라이언트마다 인증서를 따로 읽으면 앱이 켜질 때 1.7초가 더 걸렸다."""
    context = ssl.create_default_context()
    return {"http_client": httpx.Client(verify=context, timeout=60),
            "http_async_client": httpx.AsyncClient(verify=context, timeout=60)}


def make_chat_model(settings: config.Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.chat_model, api_key=_key(), base_url=settings.base_url, timeout=60, max_retries=2,
        **_http_clients(),
    )


def make_embeddings(settings: config.Settings) -> OpenAIEmbeddings:
    # check_embedding_ctx_length=False → 토큰 배열이 아니라 문자열을 그대로 보낸다.
    # (True 면 tiktoken 인코딩 파일을 내려받아 토큰으로 바꿔 보내는데, OpenAI 호환 서버에서는 문자열이 안전하다)
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=_key(),
        base_url=settings.base_url,
        check_embedding_ctx_length=False,
        **_http_clients(),
    )


def reply_text(message) -> str:
    """invoke() 가 돌려준 메시지 객체에서 글자만 꺼낸다. content 가 블록 목록으로 와도 읽는다."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def parse_json(text: str, expect: type):
    """LLM 응답에서 JSON 하나를 꺼낸다. ```json 코드 블록이나 앞뒤 설명이 붙어도 읽는다. 실패하면 None."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    opener, closer = ("[", "]") if expect is list else ("{", "}")
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, expect) else None
