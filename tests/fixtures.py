"""Hand-built courtroom GameState fixtures."""

from app.scenarios import load_scenario
from app.models import GameState


def make_case(scenario_id: str = "manor_poison"):
    return load_scenario(scenario_id)


def make_game_state(**overrides) -> GameState:
    case = make_case()
    state: GameState = {
        "game_id": "testgame01",
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
        "incoming_event": None,
        "intent": None,
        "pending_response": None,
        "pending_agent_id": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state
