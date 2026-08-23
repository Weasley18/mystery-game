"""Pytest fixtures. LLM calls are stubbed so tests never hit OpenAI/Ollama."""

import pytest


@pytest.fixture(autouse=True)
def stub_llms(monkeypatch):
    def fake_invoke(is_killer: bool, messages: list[dict]) -> str:
        last = ""
        if messages:
            last = str(messages[-1].get("content", ""))
        who = "Prosecution" if is_killer else "Defense"
        return f"[{who} counsel] {last[:160]}"

    monkeypatch.setattr("app.agents.counsel_agent.invoke_suspect_llm", fake_invoke)
    monkeypatch.setattr("app.llm.invoke_suspect_llm", fake_invoke)
