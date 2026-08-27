#!/usr/bin/env python3
"""
Async load test for the courtroom game API.

Requires Redis + a running API (real OpenAI).

Usage:
  uvicorn app.main:app --port 8000 &
  python scripts/load_test.py --games 10 --players 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx
import websockets

DEFAULT_HTTP = "http://127.0.0.1:8000"
DEFAULT_WS = "ws://127.0.0.1:8000"


async def play_one(http: str, ws_base: str, scenario_id: str, players: int, debate_n: int) -> dict:
    t0 = time.perf_counter()
    errors: list[str] = []
    async with httpx.AsyncClient(base_url=http, timeout=60.0) as client:
        create = await client.post(
            "/games",
            json={"scenario_id": scenario_id, "expected_players": players},
        )
        if create.status_code != 200:
            return {"ok": False, "error": f"create {create.status_code}", "secs": time.perf_counter() - t0}
        game_id = create.json()["game_id"]

        player_ids = []
        for i in range(players):
            join = await client.post(
                f"/games/{game_id}/join",
                json={"player_name": f"P{i}"},
            )
            if join.status_code != 200:
                return {"ok": False, "error": f"join {join.status_code}", "secs": time.perf_counter() - t0}
            player_ids.append(join.json()["player_id"])

    async def drive(player_id: str, is_host: bool) -> None:
        uri = f"{ws_base}/ws/{game_id}/{player_id}"
        async with websockets.connect(uri) as ws:
            # drain history
            try:
                await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                pass

            if is_host:
                for _ in range(debate_n):
                    await ws.send(json.dumps({"type": "next_argument"}))
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=10)
                    except asyncio.TimeoutError:
                        errors.append("debate timeout")
                    await asyncio.sleep(0.05)

                await ws.send(json.dumps({
                    "type": "question",
                    "agent_id": "prosecution",
                    "text": "What about the money trail?",
                }))
                try:
                    await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    errors.append("question timeout")

                await ws.send(json.dumps({"type": "call_vote"}))
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    pass

            await ws.send(json.dumps({"type": "cast_vote", "vote": "guilty"}))
            # drain until verdict or a few messages
            for _ in range(8):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if msg.get("type") == "verdict":
                        break
                except asyncio.TimeoutError:
                    break

    await asyncio.gather(
        *(drive(pid, i == 0) for i, pid in enumerate(player_ids))
    )
    return {
        "ok": len(errors) == 0,
        "error": "; ".join(errors) if errors else None,
        "secs": time.perf_counter() - t0,
        "game_id": game_id,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", default=DEFAULT_HTTP)
    parser.add_argument("--ws", default=DEFAULT_WS)
    parser.add_argument("--games", type=int, default=5)
    parser.add_argument("--players", type=int, default=2)
    parser.add_argument("--scenario", default="manor_poison")
    parser.add_argument("--debate", type=int, default=2, help="argument ticks per game (keep small for load)")
    args = parser.parse_args()

    # Override debate speeches via env is server-side; we just call next_argument N times.
    print(f"Running {args.games} concurrent games × {args.players} players …")
    t0 = time.perf_counter()
    results = await asyncio.gather(*[
        play_one(args.http, args.ws, args.scenario, args.players, args.debate)
        for _ in range(args.games)
    ])
    total = time.perf_counter() - t0
    ok = sum(1 for r in results if r["ok"])
    secs = [r["secs"] for r in results]
    print(f"completed={ok}/{args.games} wall={total:.2f}s")
    if secs:
        print(
            f"per_game_p50={statistics.median(secs):.2f}s "
            f"p95={sorted(secs)[max(0, int(len(secs)*0.95)-1)]:.2f}s "
            f"mean={statistics.mean(secs):.2f}s"
        )
    fails = [r for r in results if not r["ok"]]
    for f in fails[:5]:
        print("FAIL", f)
    throughput = ok / total if total else 0
    print(f"throughput≈{throughput:.2f} games/sec")


if __name__ == "__main__":
    asyncio.run(main())
