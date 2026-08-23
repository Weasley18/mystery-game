"""
Redis-backed helpers: public lobby state, players, TTL, and WS rate limits.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import redis.asyncio as redis

_redis: Optional[redis.Redis] = None

GAME_TTL_SECONDS = int(os.getenv("GAME_TTL_SECONDS", "86400"))
QUESTION_COOLDOWN_SECONDS = int(os.getenv("QUESTION_COOLDOWN_SECONDS", "8"))
ARGUMENT_COOLDOWN_SECONDS = int(os.getenv("ARGUMENT_COOLDOWN_SECONDS", "3"))
VOTE_COOLDOWN_SECONDS = int(os.getenv("VOTE_COOLDOWN_SECONDS", "1"))


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis = redis.from_url(url, decode_responses=True)
    return _redis


def _game_key(game_id: str) -> str:
    return f"game:{game_id}:public"


def _players_key(game_id: str) -> str:
    return f"game:{game_id}:players"


def _rate_key(game_id: str, player_id: str, action: str) -> str:
    return f"game:{game_id}:rate:{player_id}:{action}"


async def save_public_state(game_id: str, public_state: dict) -> None:
    r = get_redis()
    key = _game_key(game_id)
    await r.set(key, json.dumps(public_state), ex=GAME_TTL_SECONDS)


async def get_public_state(game_id: str) -> Optional[dict]:
    r = get_redis()
    raw = await r.get(_game_key(game_id))
    return json.loads(raw) if raw else None


async def add_player(game_id: str, player_id: str, player_name: str) -> None:
    r = get_redis()
    key = _players_key(game_id)
    await r.hset(key, player_id, player_name)
    await r.expire(key, GAME_TTL_SECONDS)
    # refresh public TTL too
    pub = _game_key(game_id)
    if await r.exists(pub):
        await r.expire(pub, GAME_TTL_SECONDS)


async def get_players(game_id: str) -> dict[str, str]:
    r = get_redis()
    return await r.hgetall(_players_key(game_id))


async def player_count(game_id: str) -> int:
    r = get_redis()
    return int(await r.hlen(_players_key(game_id)))


async def check_rate_limit(
    game_id: str,
    player_id: str,
    action: str,
    cooldown: int,
) -> bool:
    """
    Return True if allowed, False if rate-limited.
    Sets a short-lived key on success. cooldown <= 0 disables limiting.
    """
    if cooldown <= 0:
        return True
    r = get_redis()
    key = _rate_key(game_id, player_id, action)
    # SET NX EX — only one action per cooldown window
    ok = await r.set(key, "1", nx=True, ex=max(1, cooldown))
    return bool(ok)


async def publish_ws_event(game_id: str, message: dict) -> None:
    r = get_redis()
    await r.publish(f"game:{game_id}:ws", json.dumps(message))
