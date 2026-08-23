"""
Node functions for the courtroom per-turn graph.
"""

from __future__ import annotations

import os
from collections import Counter

from langgraph.graph import END

from app.models import GameState
from app.agents.counsel_agent import counsel_speak

DEBATE_SPEECHES = int(os.getenv("DEBATE_SPEECHES", "6"))  # 3 exchanges


def classify_intent(state: GameState) -> dict:
    event = state.get("incoming_event") or {}
    return {"intent": event.get("type")}


def _append_transcript(state: GameState, entry: dict) -> list[dict]:
    return list(state.get("transcript") or []) + [entry]


def next_argument(state: GameState) -> dict:
    """Alternate prosecution / defense speeches until DEBATE_SPEECHES reached."""
    debate_round = int(state.get("debate_round") or 0)
    if state.get("status") in ("questioning", "voting", "finished"):
        return {
            "pending_response": None,
            "pending_agent_id": None,
        }

    role = "prosecution" if debate_round % 2 == 0 else "defense"
    history = state["messages"].get(role, [])
    speech = counsel_speak(state["case"], role, mode="argue", conversation=history)

    updated_history = history + [
        {"role": "user", "content": "Deliver your next courtroom argument."},
        {"role": "assistant", "content": speech},
    ]
    new_round = debate_round + 1
    status = "debating"
    if new_round >= DEBATE_SPEECHES:
        status = "questioning"

    entry = {"kind": "argument", "agent_id": role, "text": speech, "debate_round": new_round}
    return {
        "messages": {**state["messages"], role: updated_history},
        "transcript": _append_transcript(state, entry),
        "pending_response": speech,
        "pending_agent_id": role,
        "debate_round": new_round,
        "round": state.get("round", 0) + 1,
        "status": status,
    }


def counsel_respond(state: GameState) -> dict:
    event = state["incoming_event"]
    role = event["agent_id"]
    history = state["messages"].get(role, [])
    question = event["text"]

    reply = counsel_speak(
        state["case"], role, mode="qa", conversation=history, player_text=question
    )

    updated_history = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": reply},
    ]
    entry = {
        "kind": "qa",
        "agent_id": role,
        "question": question,
        "text": reply,
        "player_id": event.get("player_id"),
    }
    return {
        "messages": {**state["messages"], role: updated_history},
        "transcript": _append_transcript(state, entry),
        "pending_response": reply,
        "pending_agent_id": role,
        "round": state.get("round", 0) + 1,
    }


def evidence_check(state: GameState) -> dict:
    revealed = list(state.get("evidence_revealed") or [])
    newly = None

    for item in state["case"]["evidence"]:
        if item["id"] in revealed:
            continue
        cond = item["reveal_condition"]
        if cond["type"] == "round_gte" and state["round"] >= int(cond["value"]):
            revealed.append(item["id"])
            newly = item
            break
        text = (state.get("incoming_event") or {}).get("text") or ""
        if cond["type"] == "keyword" and cond["value"].lower() in text.lower():
            revealed.append(item["id"])
            newly = item
            break

    update: dict = {"evidence_revealed": revealed}
    if newly:
        entry = {"kind": "evidence", "evidence_id": newly["id"], "text": newly["text"]}
        update["transcript"] = _append_transcript(state, entry)
    return update


def call_vote(state: GameState) -> dict:
    if state.get("status") == "finished":
        return {}
    return {"status": "voting"}


def cast_vote(state: GameState) -> dict:
    event = state["incoming_event"]
    player_id = event["player_id"]
    vote = event["vote"]
    votes = {**state.get("votes", {}), player_id: vote}
    expected = int(state.get("expected_players") or 1)

    update: dict = {"votes": votes, "status": "voting"}

    if len(votes) < expected:
        return update

    counts = Counter(votes.values())
    guilty = counts.get("guilty", 0)
    not_guilty = counts.get("not_guilty", 0)

    if guilty == not_guilty:
        majority = None
        correct = False
        reason = "tie"
    elif guilty > not_guilty:
        majority = "guilty"
        reason = "majority"
        correct = majority == state["case"]["verdict_truth"]
    else:
        majority = "not_guilty"
        reason = "majority"
        correct = majority == state["case"]["verdict_truth"]

    update.update({
        "status": "finished",
        "majority": majority,
        "correct": correct,
        "vote_reason": reason,
    })
    return update


def route_on_intent(state: GameState) -> str:
    intent = state.get("intent")
    if intent == "system_init":
        return END
    if intent in ("start_debate", "next_argument"):
        return "next_argument"
    if intent == "question":
        return "counsel_respond"
    if intent == "call_vote":
        return "call_vote"
    if intent == "cast_vote":
        return "cast_vote"
    return END
