"""Small ID helpers (kept separate so create-game need not import the old GM agent)."""

import uuid


def new_game_id() -> str:
    return uuid.uuid4().hex[:10]


def new_player_id() -> str:
    """Full 32-char hex — unguessable seat token (not shared in lobby state)."""
    return uuid.uuid4().hex
