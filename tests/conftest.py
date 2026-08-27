"""Pytest fixtures. LLM calls are stubbed so tests never hit OpenAI."""

import pytest


@pytest.fixture(autouse=True)
def stub_llms(monkeypatch):
    def fake_invoke(messages: list[dict], *, role: str = "prosecution") -> str:
        last = ""
        if messages:
            last = str(messages[-1].get("content", ""))
        who = "Prosecution" if role == "prosecution" else "Defense"
        return f"[{who} counsel] {last[:160]}"

    monkeypatch.setattr("app.agents.counsel_agent.invoke_counsel_llm", fake_invoke)
    monkeypatch.setattr("app.llm.invoke_counsel_llm", fake_invoke)
