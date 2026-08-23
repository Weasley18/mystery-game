#!/usr/bin/env python3
"""
CLI WebSocket client for the courtroom game.

  python scripts/ws_client.py
  python scripts/ws_client.py --scenario station_sabotage

Commands:
  next                         start/continue debate argument
  q prosecution|defense text   ask a counsel a question
  vote guilty|not_guilty       cast your vote
  call                         call the vote (enter voting)
  raw {json}                   send raw JSON
  quit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
import websockets

DEFAULT_HTTP = "http://127.0.0.1:8000"
DEFAULT_WS = "ws://127.0.0.1:8000"


async def create_and_join(base: str, scenario_id: str, name: str, expected: int):
    async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:
        create = await client.post(
            "/games",
            json={"scenario_id": scenario_id, "expected_players": expected},
        )
        create.raise_for_status()
        game = create.json()
        join = await client.post(
            f"/games/{game['game_id']}/join",
            json={"player_name": name},
        )
        join.raise_for_status()
        player = join.json()
        return game["game_id"], player["player_id"], game


async def reader(ws):
    try:
        async for message in ws:
            try:
                data = json.loads(message)
                print(f"\n<< {json.dumps(data, indent=2)}\n> ", end="", flush=True)
            except json.JSONDecodeError:
                print(f"\n<< {message}\n> ", end="", flush=True)
    except websockets.ConnectionClosed:
        print("\n[connection closed]", flush=True)


async def interactive(ws_url: str) -> None:
    print(f"Connecting to {ws_url} ...")
    async with websockets.connect(ws_url) as ws:
        print("Commands: next | q prosecution|defense <text> | call | vote guilty|not_guilty | quit")
        read_task = asyncio.create_task(reader(ws))
        loop = asyncio.get_event_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                if line in ("quit", "exit"):
                    break
                if line == "next":
                    payload = {"type": "next_argument"}
                elif line == "call":
                    payload = {"type": "call_vote"}
                elif line.startswith("vote "):
                    vote = line.split(None, 1)[1].strip()
                    payload = {"type": "cast_vote", "vote": vote}
                elif line.startswith("q "):
                    parts = line.split(None, 2)
                    if len(parts) < 3:
                        print("usage: q prosecution|defense <text>")
                        continue
                    payload = {
                        "type": "question",
                        "agent_id": parts[1],
                        "text": parts[2],
                    }
                elif line.startswith("raw "):
                    payload = json.loads(line[4:])
                else:
                    print("unknown command")
                    continue
                await ws.send(json.dumps(payload))
                print(f">> {json.dumps(payload)}")
        finally:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", default=DEFAULT_HTTP)
    parser.add_argument("--ws", default=DEFAULT_WS)
    parser.add_argument("--scenario", default="manor_poison")
    parser.add_argument("--name", default="Juror")
    parser.add_argument("--expected", type=int, default=1)
    parser.add_argument("--game-id")
    parser.add_argument("--player-id")
    args = parser.parse_args()

    if args.game_id and args.player_id:
        game_id, player_id = args.game_id, args.player_id
    else:
        print("Creating game…")
        game_id, player_id, game = await create_and_join(
            args.http, args.scenario, args.name, args.expected
        )
        print(f"game_id={game_id} player_id={player_id}")
        print(f"{game['title']} — {game['charge']}")
        print(f"prosecution: {game['prosecution']['name']}")
        print(f"defense: {game['defense']['name']}")

    await interactive(f"{args.ws}/ws/{game_id}/{player_id}")


if __name__ == "__main__":
    asyncio.run(main())
