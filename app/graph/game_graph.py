"""
Courtroom per-turn StateGraph with SQLite checkpointer.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.models import GameState
from app.graph.nodes import (
    classify_intent,
    next_argument,
    counsel_respond,
    evidence_check,
    call_vote,
    cast_vote,
    route_on_intent,
)

# Keep connection open for process lifetime (from_conn_string is a context manager).
_conn: sqlite3.Connection | None = None
_checkpointer: SqliteSaver | None = None


def get_checkpointer() -> SqliteSaver:
    global _conn, _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    db_path = os.getenv("CHECKPOINT_DB", "data/checkpoints.db")
    if db_path == ":memory:":
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(db_path, check_same_thread=False)
    _checkpointer = SqliteSaver(_conn)
    _checkpointer.setup()
    return _checkpointer


def build_turn_graph(checkpointer=None):
    graph = StateGraph(GameState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("next_argument", next_argument)
    graph.add_node("counsel_respond", counsel_respond)
    graph.add_node("evidence_check", evidence_check)
    graph.add_node("call_vote", call_vote)
    graph.add_node("cast_vote", cast_vote)

    graph.set_entry_point("classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        route_on_intent,
        {
            "next_argument": "next_argument",
            "counsel_respond": "counsel_respond",
            "call_vote": "call_vote",
            "cast_vote": "cast_vote",
            END: END,
        },
    )

    graph.add_edge("next_argument", END)
    graph.add_edge("counsel_respond", "evidence_check")
    graph.add_edge("evidence_check", END)
    graph.add_edge("call_vote", END)
    graph.add_edge("cast_vote", END)

    return graph.compile(checkpointer=checkpointer or get_checkpointer())


turn_graph = build_turn_graph()
