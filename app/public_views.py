"""Public-safe serialization helpers — never leak secrets to clients."""

from __future__ import annotations

from app.models import Case, GameState
from app.scenarios import public_counsel


SECRET_KEYS = frozenset({
    "verdict_truth",
    "solution",
    "brief",
    "is_killer",
    "killer_id",
    "facts",
})


def evidence_public(case: Case, revealed_ids: list[str]) -> list[dict]:
    out = []
    for item in case["evidence"]:
        if item["id"] in revealed_ids:
            out.append({"id": item["id"], "text": item["text"]})
    return out


def build_public_state(
    case: Case,
    *,
    status: str,
    debate_round: int = 0,
    evidence_revealed: list[str] | None = None,
    vote_count: int = 0,
    expected_voters: int = 1,
    majority: str | None = None,
    correct: bool | None = None,
    vote_reason: str | None = None,
) -> dict:
    revealed = evidence_revealed or []
    payload = {
        "scenario_id": case["scenario_id"],
        "level": case["level"],
        "title": case["title"],
        "charge": case["charge"],
        "setting": case["setting"],
        "case_summary": case["case_summary"],
        "defendant": case["defendant"],
        "prosecution": public_counsel(case["prosecution"]),
        "defense": public_counsel(case["defense"]),
        "status": status,
        "debate_round": debate_round,
        "evidence_revealed": evidence_public(case, revealed),
        "vote_count": vote_count,
        "expected_voters": expected_voters,
    }
    if status == "finished":
        payload["majority"] = majority
        payload["correct"] = correct
        payload["vote_reason"] = vote_reason
        payload["solution"] = case["solution"]
        payload["verdict_truth"] = case["verdict_truth"]
    return payload


def history_payload(state: GameState) -> dict:
    """Reconnect payload — transcript + revealed evidence; no secrets."""
    case = state["case"]
    revealed = state.get("evidence_revealed") or []
    return {
        "status": state.get("status"),
        "debate_round": state.get("debate_round", 0),
        "transcript": list(state.get("transcript") or []),
        "evidence_revealed": evidence_public(case, revealed),
        "vote_count": len(state.get("votes") or {}),
        "expected_voters": state.get("expected_players", 1),
        "majority": state.get("majority") if state.get("status") == "finished" else None,
        "correct": state.get("correct") if state.get("status") == "finished" else None,
        "solution": case["solution"] if state.get("status") == "finished" else None,
        "verdict_truth": case["verdict_truth"] if state.get("status") == "finished" else None,
    }


def assert_no_secrets(payload: dict) -> None:
    """Raise if secret keys appear outside finished reveal fields."""
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in SECRET_KEYS and path != "finished_ok":
                    # allowed only when caller marks finished reveal
                    raise AssertionError(f"secret key leaked at {path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(payload)
