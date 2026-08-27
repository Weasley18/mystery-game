"""
End-to-end WebSocket tests for courtroom flow.

Requires Redis. LLM calls are stubbed in conftest.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def _redis_available() -> bool:
    try:
        import redis

        r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        return bool(r.ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_available(), reason="Redis required")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "e2e.db"))
    monkeypatch.setenv("DEBATE_SPEECHES", "2")
    monkeypatch.setenv("ARGUMENT_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("QUESTION_COOLDOWN_SECONDS", "30")
    monkeypatch.setenv("VOTE_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("IP_CREATE_LIMIT", "0")
    monkeypatch.setenv("IP_JOIN_LIMIT", "0")
    monkeypatch.setenv("IP_LLM_LIMIT", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RENDER", raising=False)

    import importlib
    import app.graph.game_graph as gg
    import app.graph.nodes as nodes
    import app.redis_state as rs

    importlib.reload(nodes)
    gg._checkpointer = None
    gg._conn = None
    importlib.reload(gg)
    rs._redis = None
    importlib.reload(rs)

    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    rs._redis = None


def _recv_until(ws, wanted_types, limit=12):
    """Receive until we have seen all wanted message types (or hit limit)."""
    seen = {}
    wanted = set(wanted_types)
    for _ in range(limit):
        msg = ws.receive_json()
        seen[msg["type"]] = msg
        if wanted.issubset(seen):
            break
    return seen


def test_scenarios_list(client):
    res = client.get("/scenarios")
    assert res.status_code == 200
    ids = {s["id"] for s in res.json()}
    assert "manor_poison" in ids
    assert "station_sabotage" in ids


def test_create_join_history_argument_vote(client):
    create = client.post(
        "/games",
        json={"scenario_id": "manor_poison", "expected_players": 1},
    )
    assert create.status_code == 200
    body = create.json()
    assert "verdict_truth" not in body
    assert "brief" not in body["prosecution"]
    game_id = body["game_id"]

    j1 = client.post(f"/games/{game_id}/join", json={"player_name": "A"})
    p1 = j1.json()["player_id"]

    state = client.get(f"/games/{game_id}/state")
    assert state.status_code == 200
    body_state = state.json()
    assert "verdict_truth" not in body_state
    assert "player_ids" not in body_state
    assert "players" in body_state

    # Unknown player_id must not get a live seat
    with client.websocket_connect(f"/ws/{game_id}/notarealseat") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["payload"]["detail"] == "unauthorized"

    with client.websocket_connect(f"/ws/{game_id}/{p1}") as ws:
        hist = ws.receive_json()
        assert hist["type"] == "history"

        ws.send_json({"type": "next_argument"})
        seen = _recv_until(ws, {"argument", "status"})
        assert "argument" in seen

        ws.send_json({"type": "question"})  # malformed
        err = ws.receive_json()
        assert err["type"] == "error"

        ws.send_json({
            "type": "question",
            "agent_id": "prosecution",
            "text": "What about the money?",
        })
        # agent_reply to self + possible evidence_revealed broadcast
        seen = _recv_until(ws, {"agent_reply"}, limit=6)
        assert seen["agent_reply"]["payload"]["text"]
        # drain any trailing evidence_revealed
        # (already consumed if present in the loop above)

        # second question quickly should rate-limit
        ws.send_json({
            "type": "question",
            "agent_id": "prosecution",
            "text": "Again?",
        })
        limited = None
        for _ in range(4):
            msg = ws.receive_json()
            if msg["type"] == "error":
                limited = msg
                break
        assert limited is not None
        assert limited["payload"]["detail"] == "rate_limited"

        # second debate speech → questioning
        ws.send_json({"type": "next_argument"})
        _recv_until(ws, {"argument", "status"})

        ws.send_json({"type": "call_vote"})
        _recv_until(ws, {"status"})

        ws.send_json({"type": "cast_vote", "vote": "guilty"})
        seen = _recv_until(ws, {"verdict"})
        assert seen["verdict"]["payload"]["yes_or_no"] in ("Yes", "No")
        assert "solution" in seen["verdict"]["payload"]
