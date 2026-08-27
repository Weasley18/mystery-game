"""
Data models for the courtroom mystery game.

TypedDicts are used for LangGraph state. Pydantic models are used for
FastAPI request/response bodies and WebSocket message validation.
"""

from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# LangGraph state (TypedDicts)
# ---------------------------------------------------------------------------

class Counsel(TypedDict):
    id: str
    name: str
    persona: str
    brief: list[str]  # private — never serialize to clients


class Evidence(TypedDict):
    id: str
    text: str
    reveal_condition: dict
    revealed: bool


class Case(TypedDict):
    scenario_id: str
    level: int
    title: str
    charge: str
    setting: str
    case_summary: str
    defendant: dict  # {id, name}
    verdict_truth: Literal["guilty", "not_guilty"]  # secret until reveal
    solution: str  # secret until reveal
    prosecution: Counsel
    defense: Counsel
    evidence: list[Evidence]


class GameState(TypedDict):
    game_id: str
    case: Case
    round: int
    debate_round: int  # number of counsel speeches delivered
    status: Literal["lobby", "debating", "questioning", "voting", "finished"]

    # per-agent conversation history (prosecution / defense)
    messages: dict[str, list[dict]]
    # chronological public transcript entries for reconnect
    transcript: list[dict]

    evidence_revealed: list[str]
    votes: dict[str, str]  # player_id -> guilty | not_guilty
    expected_players: int
    majority: Optional[str]
    correct: Optional[bool]
    vote_reason: Optional[str]  # e.g. "tie"

    incoming_event: Optional[dict]
    intent: Optional[
        Literal[
            "system_init",
            "start_debate",
            "next_argument",
            "question",
            "call_vote",
            "cast_vote",
        ]
    ]
    pending_response: Optional[str]
    pending_agent_id: Optional[str]


# ---------------------------------------------------------------------------
# API request/response bodies (Pydantic)
# ---------------------------------------------------------------------------

_SCENARIO_ID_RE = r"^[a-z0-9_]+$"
MAX_PLAYER_NAME_LEN = 40
MAX_QUESTION_LEN = 500
MAX_EXPECTED_PLAYERS = 12


class CreateGameRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, max_length=64, pattern=_SCENARIO_ID_RE)
    expected_players: int = Field(default=1, ge=1, le=MAX_EXPECTED_PLAYERS)


class CreateGameResponse(BaseModel):
    game_id: str
    scenario_id: str
    level: int
    title: str
    charge: str
    setting: str
    case_summary: str
    defendant: dict
    prosecution: dict  # id, name, persona only
    defense: dict


class JoinGameRequest(BaseModel):
    player_name: str = Field(..., min_length=1, max_length=MAX_PLAYER_NAME_LEN)

    @field_validator("player_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("player_name is required")
        return name


class JoinGameResponse(BaseModel):
    player_id: str
    game_id: str


class ScenarioSummary(BaseModel):
    id: str
    level: int
    title: str
    setting: str
    case_summary: str


class WSIncoming(BaseModel):
    """Shape of every message a client sends over the WebSocket."""
    type: Literal[
        "start_debate",
        "next_argument",
        "question",
        "call_vote",
        "cast_vote",
    ]
    agent_id: Optional[str] = None  # prosecution | defense for question
    text: Optional[str] = Field(default=None, max_length=MAX_QUESTION_LEN)
    vote: Optional[Literal["guilty", "not_guilty"]] = None

    @model_validator(mode="after")
    def require_fields_by_type(self) -> "WSIncoming":
        if self.type == "question":
            if self.agent_id not in ("prosecution", "defense"):
                raise ValueError("agent_id must be 'prosecution' or 'defense'")
            if not self.text or not str(self.text).strip():
                raise ValueError("text is required for question")
            self.text = str(self.text).strip()
        elif self.type == "cast_vote":
            if self.vote not in ("guilty", "not_guilty"):
                raise ValueError("vote must be 'guilty' or 'not_guilty'")
        return self


class WSOutgoing(BaseModel):
    """Shape of every message the server pushes to clients."""
    type: Literal[
        "argument",
        "agent_reply",
        "evidence_revealed",
        "vote_progress",
        "verdict",
        "history",
        "status",
        "error",
    ]
    payload: dict
