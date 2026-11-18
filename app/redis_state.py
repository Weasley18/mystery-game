"""
Redis-backed helpers: public lobby state, players, TTL, and WS rate limits.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import Optional

import redis.asyncio as redis

_redis: Optional[redis.Redis] = None

GAME_TTL_SECONDS = int(os.getenv("GAME_TTL_SECONDS", "86400"))
QUESTION_COOLDOWN_SECONDS = int(os.getenv("QUESTION_COOLDOWN_SECONDS", "8"))
ARGUMENT_COOLDOWN_SECONDS = int(os.getenv("ARGUMENT_COOLDOWN_SECONDS", "3"))
VOTE_COOLDOWN_SECONDS = int(os.getenv("VOTE_COOLDOWN_SECONDS", "1"))

MAX_PLAYERS_PER_GAME = int(os.getenv("MAX_PLAYERS_PER_GAME", "12"))
MAX_EXPECTED_PLAYERS = int(os.getenv("MAX_EXPECTED_PLAYERS", "12"))

# IP buckets: max actions per window (seconds). <= 0 disables that bucket.
IP_CREATE_LIMIT = int(os.getenv("IP_CREATE_LIMIT", "10"))
IP_CREATE_WINDOW = int(os.getenv("IP_CREATE_WINDOW", "60"))
IP_JOIN_LIMIT = int(os.getenv("IP_JOIN_LIMIT", "30"))
IP_JOIN_WINDOW = int(os.getenv("IP_JOIN_WINDOW", "60"))
IP_LLM_LIMIT = int(os.getenv("IP_LLM_LIMIT", "40"))
IP_LLM_WINDOW = int(os.getenv("IP_LLM_WINDOW", "60"))


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


def _player_users_key(game_id: str) -> str:
    return f"game:{game_id}:player_users"


def _chat_key(game_id: str) -> str:
    return f"game:{game_id}:chat"


def _rate_key(game_id: str, player_id: str, action: str) -> str:
    return f"game:{game_id}:rate:{player_id}:{action}"


def _ip_rate_key(bucket: str, ip: str) -> str:
    return f"rate:ip:{bucket}:{ip}"


MAX_TEAM_CHAT_MESSAGES = int(os.getenv("MAX_TEAM_CHAT_MESSAGES", "100"))


async def save_public_state(game_id: str, public_state: dict) -> None:
    r = get_redis()
    key = _game_key(game_id)
    await r.set(key, json.dumps(public_state), ex=GAME_TTL_SECONDS)


async def get_public_state(game_id: str) -> Optional[dict]:
    r = get_redis()
    raw = await r.get(_game_key(game_id))
    return json.loads(raw) if raw else None


async def add_player(
    game_id: str,
    player_id: str,
    player_name: str,
    user_id: str | None = None,
) -> None:
    r = get_redis()
    key = _players_key(game_id)
    await r.hset(key, player_id, player_name)
    await r.expire(key, GAME_TTL_SECONDS)
    if user_id:
        ukey = _player_users_key(game_id)
        await r.hset(ukey, player_id, user_id)
        await r.expire(ukey, GAME_TTL_SECONDS)
    # refresh public TTL too
    pub = _game_key(game_id)
    if await r.exists(pub):
        await r.expire(pub, GAME_TTL_SECONDS)


async def get_players(game_id: str) -> dict[str, str]:
    r = get_redis()
    return await r.hgetall(_players_key(game_id))


async def get_player_user_ids(game_id: str) -> list[str]:
    """Unique account ids seated in this gallery (may be empty for legacy seats)."""
    r = get_redis()
    mapping = await r.hgetall(_player_users_key(game_id))
    seen: list[str] = []
    for uid in mapping.values():
        if uid and uid not in seen:
            seen.append(uid)
    return seen


async def get_player_name(game_id: str, player_id: str) -> Optional[str]:
    r = get_redis()
    return await r.hget(_players_key(game_id), player_id)


async def player_count(game_id: str) -> int:
    r = get_redis()
    return int(await r.hlen(_players_key(game_id)))


async def player_exists(game_id: str, player_id: str) -> bool:
    r = get_redis()
    return bool(await r.hexists(_players_key(game_id), player_id))


async def append_team_chat(game_id: str, message: dict) -> dict:
    """Append a team chat message; returns the stored message with id/ts."""
    r = get_redis()
    key = _chat_key(game_id)
    stored = {
        "id": message.get("id") or secrets.token_hex(8),
        "player_id": message["player_id"],
        "player_name": message.get("player_name") or "Juror",
        "text": message["text"],
        "ts": message.get("ts") or time.time(),
    }
    await r.rpush(key, json.dumps(stored))
    await r.ltrim(key, -MAX_TEAM_CHAT_MESSAGES, -1)
    await r.expire(key, GAME_TTL_SECONDS)
    return stored


async def get_team_chat(game_id: str) -> list[dict]:
    r = get_redis()
    raw_list = await r.lrange(_chat_key(game_id), 0, -1)
    out: list[dict] = []
    for raw in raw_list:
        try:
            out.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
    return out


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


async def check_ip_rate_limit(ip: str, bucket: str, limit: int, window: int) -> bool:
    """
    Fixed-window counter per IP. Return True if allowed.
    limit <= 0 disables. Empty/unknown ip uses bucket "unknown".
    """
    if limit <= 0:
        return True
    safe_ip = (ip or "unknown").strip() or "unknown"
    r = get_redis()
    key = _ip_rate_key(bucket, safe_ip)
    n = await r.incr(key)
    if n == 1:
        await r.expire(key, max(1, window))
    return n <= limit


async def publish_ws_event(game_id: str, message: dict) -> None:
    r = get_redis()
    await r.publish(f"game:{game_id}:ws", json.dumps(message))
