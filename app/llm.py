"""
Central LLM factory and routing.

- GM case generation: hosted OpenAI (quality matters once per game).
- Killer replies: hosted OpenAI.
- Innocent suspects: local Ollama, with OpenAI fallback if Ollama is down.
"""

import os

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


def _require_openai_key() -> str:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key or key.startswith("sk-..."):
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to .env — mock LLM mode is gone."
        )
    return key


def get_gm_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENAI_GM_MODEL", "gpt-4o"),
        temperature=0.9,
        api_key=_require_openai_key(),
    )


def get_openai_suspect_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENAI_SUSPECT_MODEL", "gpt-4o-mini"),
        temperature=0.8,
        api_key=_require_openai_key(),
    )


def get_ollama_suspect_llm() -> ChatOllama:
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.8,
    )


def get_suspect_llm(*, is_killer: bool):
    """Return the chat model for a suspect reply. Killers always use OpenAI."""
    if is_killer:
        return get_openai_suspect_llm()
    try:
        return get_ollama_suspect_llm()
    except Exception:
        return get_openai_suspect_llm()


def invoke_suspect_llm(is_killer: bool, messages: list[dict]) -> str:
    """
    Invoke the routed model for a suspect. If Ollama fails for an innocent,
    fall back to OpenAI once.
    """
    if is_killer:
        response = get_openai_suspect_llm().invoke(messages)
        return _content_text(response)

    try:
        response = get_ollama_suspect_llm().invoke(messages)
        return _content_text(response)
    except Exception:
        response = get_openai_suspect_llm().invoke(messages)
        return _content_text(response)


def _content_text(response) -> str:
    content = response.content
    if isinstance(content, str):
        return content
    # Some providers return a list of content blocks
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content
    )
