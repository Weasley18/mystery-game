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
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("AUTH_SECRET", "e2e-test-secret")
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
    import app.accounts as accounts
    import app.graph.game_graph as gg
    import app.graph.nodes as nodes
    import app.redis_state as rs

    accounts.reset_conn_for_tests()
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
    accounts.reset_conn_for_tests()


def _auth_headers(client, username="juror_a", password="secret12"):
    signup = client.post(
        "/auth/signup",
        json={"username": username, "password": password},
    )
    if signup.status_code == 409:
        login = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        token = login.json()["token"]
    else:
        assert signup.status_code == 200, signup.text
        token = signup.json()["token"]
    return {"Authorization": f"Bearer {token}"}


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


def test_auth_signup_login(client):
    bad = client.post("/auth/login", json={"username": "x", "password": "nope123"})
    assert bad.status_code == 401

    headers = _auth_headers(client, "roll_call")
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "roll_call"
    assert me.json()["max_unlocked_level"] == 1


def test_scenarios_list(client):
    res = client.get("/scenarios")
    assert res.status_code == 200
    ids = {s["id"] for s in res.json()}
    assert "manor_poison" in ids
    assert "station_sabotage" in ids
    # Without auth, only level 1 unlocked
    by_id = {s["id"]: s for s in res.json()}
    assert by_id["manor_poison"]["unlocked"] is True
    assert by_id["station_sabotage"]["unlocked"] is False


def test_level_lock_on_create(client):
    headers = _auth_headers(client, "locked_out")
    res = client.post(
        "/games",
        headers=headers,
        json={"scenario_id": "station_sabotage", "expected_players": 1},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "level_locked"

    ok = client.post(
        "/games",
        headers=headers,
        json={"scenario_id": "manor_poison", "expected_players": 1},
    )
    assert ok.status_code == 200


def test_create_join_history_argument_vote(client):
    headers = _auth_headers(client, "gallery_one")
    create = client.post(
        "/games",
        headers=headers,
        json={"scenario_id": "manor_poison", "expected_players": 1},
    )
    assert create.status_code == 200
    body = create.json()
    assert "verdict_truth" not in body
    assert "brief" not in body["prosecution"]
    game_id = body["game_id"]

    j1 = client.post(
        f"/games/{game_id}/join",
        headers=headers,
        json={},
    )
    assert j1.status_code == 200
    p1 = j1.json()["player_id"]
    assert j1.json()["player_name"] == "gallery_one"

    state = client.get(f"/games/{game_id}/state")
    assert state.status_code == 200
    body_state = state.json()
    assert "verdict_truth" not in body_state
    assert "player_ids" not in body_state
    assert "players" in body_state
    assert body_state.get("max_questions") == 7

    # Unknown player_id must not get a live seat
    with client.websocket_connect(f"/ws/{game_id}/notarealseat") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["payload"]["detail"] == "unauthorized"

    with client.websocket_connect(f"/ws/{game_id}/{p1}") as ws:
        hist = ws.receive_json()
        assert hist["type"] == "history"
        assert "team_chat" in hist["payload"]

        ws.send_json({"type": "team_chat", "text": "Stick together."})
        chat = None
        for _ in range(4):
            msg = ws.receive_json()
            if msg["type"] == "team_chat":
                chat = msg
                break
        assert chat is not None
        assert chat["payload"]["text"] == "Stick together."
        assert chat["payload"]["player_name"] == "gallery_one"

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
        seen = _recv_until(ws, {"agent_reply"}, limit=6)
        assert seen["agent_reply"]["payload"]["text"]
        assert seen["agent_reply"]["payload"]["questions_asked"] == 1

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

        # manor_poison truth is guilty — correct vote credits level 1
        if seen["verdict"]["payload"]["correct"]:
            assert seen["verdict"]["payload"]["next_level_unlocked"] is True
            scenarios = client.get("/scenarios", headers=headers).json()
            by_id = {s["id"]: s for s in scenarios}
            assert by_id["station_sabotage"]["unlocked"] is True


def test_question_limit(tmp_path, monkeypatch):
    """Separate client with zero question cooldown so seven asks can run back-to-back."""
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "qcap.db"))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "qcap_accounts.db"))
    monkeypatch.setenv("AUTH_SECRET", "e2e-qcap-secret")
    monkeypatch.setenv("DEBATE_SPEECHES", "2")
    monkeypatch.setenv("ARGUMENT_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("QUESTION_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("VOTE_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("IP_CREATE_LIMIT", "0")
    monkeypatch.setenv("IP_JOIN_LIMIT", "0")
    monkeypatch.setenv("IP_LLM_LIMIT", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RENDER", raising=False)

    import importlib
    import app.accounts as accounts
    import app.graph.game_graph as gg
    import app.graph.nodes as nodes
    import app.redis_state as rs

    accounts.reset_conn_for_tests()
    importlib.reload(nodes)
    gg._checkpointer = None
    gg._conn = None
    importlib.reload(gg)
    rs._redis = None
    importlib.reload(rs)
    import app.main as main

    importlib.reload(main)

    with TestClient(main.app) as client:
        headers = _auth_headers(client, "quizzer")
        create = client.post(
            "/games",
            headers=headers,
            json={"scenario_id": "manor_poison", "expected_players": 1},
        )
        game_id = create.json()["game_id"]
        p1 = client.post(
            f"/games/{game_id}/join", headers=headers, json={}
        ).json()["player_id"]

        with client.websocket_connect(f"/ws/{game_id}/{p1}") as ws:
            ws.receive_json()  # history
            for _ in range(2):
                ws.send_json({"type": "next_argument"})
                _recv_until(ws, {"argument", "status"}, limit=8)

            for i in range(7):
                ws.send_json({
                    "type": "question",
                    "agent_id": "prosecution",
                    "text": f"Question number {i + 1}?",
                })
                seen = _recv_until(ws, {"agent_reply"}, limit=8)
                assert "agent_reply" in seen, f"expected reply for question {i + 1}"

            ws.send_json({
                "type": "question",
                "agent_id": "defense",
                "text": "One more?",
            })
            limited = None
            for _ in range(6):
                msg = ws.receive_json()
                if msg["type"] == "error" and msg["payload"].get("detail") == "question_limit":
                    limited = msg
                    break
            assert limited is not None
            assert limited["payload"]["max_questions"] == 7

    rs._redis = None
    accounts.reset_conn_for_tests()
