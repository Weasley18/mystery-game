"""Integration: debate → question → votes → Yes/No."""

from app.graph.game_graph import build_turn_graph
from app.graph.nodes import DEBATE_SPEECHES
from tests.fixtures import make_case


def test_full_courtroom_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DEBATE_SPEECHES", "2")

    # Rebuild graph with fresh checkpointer for this test DB
    import importlib
    import app.graph.game_graph as gg
    import app.graph.nodes as nodes

    importlib.reload(nodes)
    gg._checkpointer = None
    gg._conn = None
    graph = gg.build_turn_graph()

    case = make_case()
    game_id = "intcourt01"
    config = {"configurable": {"thread_id": game_id}}

    initial = {
        "game_id": game_id,
        "case": case,
        "round": 0,
        "debate_round": 0,
        "status": "lobby",
        "messages": {},
        "transcript": [],
        "evidence_revealed": [],
        "votes": {},
        "expected_players": 2,
        "majority": None,
        "correct": None,
        "vote_reason": None,
        "incoming_event": {"type": "system_init"},
        "intent": None,
        "pending_response": None,
        "pending_agent_id": None,
    }
    graph.invoke(initial, config=config)

    # Force 2 speeches then questioning via env DEBATE_SPEECHES=2
    for _ in range(2):
        result = graph.invoke(
            {"incoming_event": {"type": "next_argument", "player_id": "p1"}},
            config=config,
        )
    assert result["status"] == "questioning"
    assert result["debate_round"] == 2

    result = graph.invoke(
        {
            "incoming_event": {
                "type": "question",
                "agent_id": "defense",
                "text": "Where was the defendant?",
                "player_id": "p1",
            }
        },
        config=config,
    )
    assert len(result["messages"]["defense"]) >= 2
    assert result["pending_response"]
    assert any(m["role"] == "user" and "defendant" in m["content"] for m in result["messages"]["defense"])

    graph.invoke({"incoming_event": {"type": "call_vote", "player_id": "p1"}}, config=config)

    graph.invoke(
        {"incoming_event": {"type": "cast_vote", "player_id": "p1", "vote": "guilty"}},
        config=config,
    )
    final = graph.invoke(
        {"incoming_event": {"type": "cast_vote", "player_id": "p2", "vote": "guilty"}},
        config=config,
    )
    assert final["status"] == "finished"
    assert final["correct"] is True
