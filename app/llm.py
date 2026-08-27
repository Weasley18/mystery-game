"""
OpenAI counsel LLM. Prosecution and defense share the same hosted model;
temperature is slightly higher for defense so the two voices still differ.
"""

import os

from langchain_openai import ChatOpenAI


def _require_openai_key() -> str:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key or key.startswith("sk-..."):
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to .env.")
    return key


def _openai_counsel_model() -> str:
    return os.getenv("OPENAI_COUNSEL_MODEL") or "gpt-4o-mini"


def get_counsel_llm(*, temperature: float = 0.8) -> ChatOpenAI:
    return ChatOpenAI(
        model=_openai_counsel_model(),
        temperature=temperature,
        api_key=_require_openai_key(),
    )


def invoke_counsel_llm(messages: list[dict], *, role: str = "prosecution") -> str:
    temperature = 0.85 if role == "defense" else 0.7
    response = get_counsel_llm(temperature=temperature).invoke(messages)
    return _content_text(response)


def _content_text(response) -> str:
    content = response.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content
    )
