"""
WebSocket room manager with optional Redis pub/sub fan-out for multi-worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket

from app.redis_state import get_redis

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, WebSocket]] = {}
        self._subscriber_task: Optional[asyncio.Task] = None
        self._subscribed_games: set[str] = set()
        self._pubsub = None

    async def connect(self, game_id: str, player_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms.setdefault(game_id, {})[player_id] = ws
        await self._ensure_pubsub(game_id)

    def disconnect(self, game_id: str, player_id: str) -> None:
        room = self._rooms.get(game_id)
        if room and player_id in room:
            del room[player_id]
            if not room:
                del self._rooms[game_id]

    async def send_to_player(self, game_id: str, player_id: str, message: dict) -> None:
        ws = self._rooms.get(game_id, {}).get(player_id)
        if ws is not None:
            await ws.send_json(message)

    async def broadcast(
        self,
        game_id: str,
        message: dict,
        exclude: str | None = None,
        *,
        local_only: bool = False,
    ) -> None:
        # Always deliver locally first
        for player_id, ws in list(self._rooms.get(game_id, {}).items()):
            if player_id != exclude:
                try:
                    await ws.send_json(message)
                except Exception:
                    logger.exception("broadcast send failed")

        if local_only:
            return

        # Fan-out to other workers via Redis
        try:
            from app.redis_state import publish_ws_event

            envelope = {"_origin": id(self), "exclude": exclude, "message": message}
            await publish_ws_event(game_id, envelope)
        except Exception:
            logger.exception("redis publish failed")

    async def _ensure_pubsub(self, game_id: str) -> None:
        if game_id in self._subscribed_games:
            return
        self._subscribed_games.add(game_id)
        if self._subscriber_task is None or self._subscriber_task.done():
            self._subscriber_task = asyncio.create_task(self._pubsub_loop())

    async def _pubsub_loop(self) -> None:
        """Subscribe to game:*:ws and deliver to local sockets (skip echo)."""
        try:
            r = get_redis()
            pubsub = r.pubsub()
            await pubsub.psubscribe("game:*:ws")
            self._pubsub = pubsub
            async for raw in pubsub.listen():
                if raw is None or raw.get("type") not in ("pmessage", "message"):
                    continue
                try:
                    data = json.loads(raw["data"])
                except (TypeError, json.JSONDecodeError):
                    continue
                # Ignore our own publishes
                if data.get("_origin") == id(self):
                    continue
                channel = raw.get("channel") or ""
                # channel like game:{id}:ws
                parts = str(channel).split(":")
                if len(parts) < 3:
                    continue
                game_id = parts[1]
                message = data.get("message") or data
                exclude = data.get("exclude")
                await self.broadcast(game_id, message, exclude=exclude, local_only=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pubsub loop crashed")


manager = ConnectionManager()
