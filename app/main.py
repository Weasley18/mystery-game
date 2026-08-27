"""
FastAPI entrypoint — courtroom mystery game.

Routes:
    GET  /scenarios                list authored levels
    POST /games                    create a game from scenario_id
    POST /games/{game_id}/join     add a player
    GET  /games/{game_id}/state    public lobby / reconnect state
    WS   /ws/{game_id}/{player_id} live loop
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

# override=True so --reload picks up a restored OPENAI_API_KEY from .env
load_dotenv(override=True)


def _is_production() -> bool:
    env = (os.getenv("ENVIRONMENT") or "").strip().lower()
    if env in ("production", "prod"):
        return True
    if (os.getenv("RENDER") or "").strip().lower() in ("true", "1", "yes"):
        return True
    # Railway sets RAILWAY_ENVIRONMENT (e.g. production) on deployed services.
    return bool((os.getenv("RAILWAY_ENVIRONMENT") or "").strip())


def _cors_origins() -> list[str]:
    """
    CORS allowlist.
    - Unset CORS_ORIGINS → localhost defaults (local dev).
    - Empty string → no cross-origin (same-origin SPA deploy).
    - "*" → only outside production.
    - Comma list → explicit allowlist.
    """
    raw = os.getenv("CORS_ORIGINS")
    if raw is None:
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost",
            "http://127.0.0.1",
        ]
    stripped = raw.strip()
    if stripped == "*":
        if _is_production():
            return []
        return ["*"]
    return [o.strip() for o in stripped.split(",") if o.strip()]

from app.models import (
    CreateGameRequest,
    CreateGameResponse,
    JoinGameRequest,
    JoinGameResponse,
    GameState,
    WSIncoming,
    WSOutgoing,
)
from app.scenarios import list_scenarios, load_scenario, public_case_view
from app.public_views import build_public_state, history_payload
from app.graph.game_graph import turn_graph
from app.redis_state import (
    save_public_state,
    get_public_state,
    add_player,
    get_players,
    player_count,
    player_exists,
    check_rate_limit,
    check_ip_rate_limit,
    QUESTION_COOLDOWN_SECONDS,
    ARGUMENT_COOLDOWN_SECONDS,
    VOTE_COOLDOWN_SECONDS,
    MAX_PLAYERS_PER_GAME,
    MAX_EXPECTED_PLAYERS,
    IP_CREATE_LIMIT,
    IP_CREATE_WINDOW,
    IP_JOIN_LIMIT,
    IP_JOIN_WINDOW,
    IP_LLM_LIMIT,
    IP_LLM_WINDOW,
)
from app.ids import new_game_id, new_player_id
from app.ws_manager import manager
logger = logging.getLogger(__name__)
_prod = _is_production()
app = FastAPI(
    title="AI Courtroom Mystery",
    docs_url=None if _prod else "/docs",
    redoc_url=None if _prod else "/redoc",
    openapi_url=None if _prod else "/openapi.json",
)

_origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins if _origins else [],
    allow_credentials=bool(_origins) and "*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _client_ip(request: Request | WebSocket) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _ws_origin_allowed(ws: WebSocket) -> bool:
    """Reject browser WS from origins outside the CORS allowlist / same host."""
    origin = ws.headers.get("origin")
    if not origin:
        return True  # non-browser clients
    if "*" in _origins:
        return True
    if origin in _origins:
        return True
    host = ws.headers.get("host")
    if host:
        parsed = urlparse(origin)
        if parsed.netloc == host:
            return True
    return False


@app.get("/health")
async def health():
    return {"ok": True}


async def _sync_public_from_graph(game_id: str, result: dict) -> None:
    case = result["case"]
    pub = build_public_state(
        case,
        status=result.get("status", "lobby"),
        debate_round=result.get("debate_round", 0),
        evidence_revealed=result.get("evidence_revealed") or [],
        vote_count=len(result.get("votes") or {}),
        expected_voters=result.get("expected_players", 1),
        majority=result.get("majority"),
        correct=result.get("correct"),
        vote_reason=result.get("vote_reason"),
    )
    await save_public_state(game_id, pub)


@app.get("/scenarios")
async def scenarios():
    return list_scenarios()


@app.post("/games", response_model=CreateGameResponse)
async def create_game(req: CreateGameRequest, request: Request):
    ip = _client_ip(request)
    if not await check_ip_rate_limit(ip, "create", IP_CREATE_LIMIT, IP_CREATE_WINDOW):
        raise HTTPException(429, "rate_limited")

    try:
        case = load_scenario(req.scenario_id)
    except FileNotFoundError:
        raise HTTPException(404, "scenario not found")
    except ValueError as e:
        raise HTTPException(400, str(e))

    game_id = new_game_id()
    expected = min(max(1, req.expected_players), MAX_EXPECTED_PLAYERS)

    await save_public_state(
        game_id,
        build_public_state(
            case,
            status="lobby",
            expected_voters=expected,
        ),
    )

    initial_state: GameState = {
        "game_id": game_id,
        "case": case,
        "round": 0,
        "debate_round": 0,
        "status": "lobby",
        "messages": {},
        "transcript": [],
        "evidence_revealed": [],
        "votes": {},
        "expected_players": expected,
        "majority": None,
        "correct": None,
        "vote_reason": None,
        "incoming_event": {"type": "system_init"},
        "intent": None,
        "pending_response": None,
        "pending_agent_id": None,
    }
    config = {"configurable": {"thread_id": game_id}}
    turn_graph.invoke(initial_state, config=config)

    public = public_case_view(case)
    return CreateGameResponse(
        game_id=game_id,
        scenario_id=public["scenario_id"],
        level=public["level"],
        title=public["title"],
        charge=public["charge"],
        setting=public["setting"],
        case_summary=public["case_summary"],
        defendant=public["defendant"],
        prosecution=public["prosecution"],
        defense=public["defense"],
    )


@app.post("/games/{game_id}/join", response_model=JoinGameResponse)
async def join_game(game_id: str, req: JoinGameRequest, request: Request):
    ip = _client_ip(request)
    if not await check_ip_rate_limit(ip, "join", IP_JOIN_LIMIT, IP_JOIN_WINDOW):
        raise HTTPException(429, "rate_limited")

    if await get_public_state(game_id) is None:
        raise HTTPException(404, "game not found")

    count = await player_count(game_id)
    if count >= MAX_PLAYERS_PER_GAME:
        raise HTTPException(403, "game is full")

    player_id = new_player_id()
    await add_player(game_id, player_id, req.player_name)
    return JoinGameResponse(player_id=player_id, game_id=game_id)


@app.get("/games/{game_id}/state")
async def game_state(game_id: str):
    state = await get_public_state(game_id)
    if state is None:
        raise HTTPException(404, "game not found")
    players = await get_players(game_id)
    # Never leak secrets from redis public blob — build_public_state already safe
    for key in ("verdict_truth", "solution", "brief"):
        if state.get("status") != "finished":
            state.pop(key, None)
    return {**state, "players": list(players.values())}


@app.websocket("/ws/{game_id}/{player_id}")
async def game_socket(ws: WebSocket, game_id: str, player_id: str):
    if not _ws_origin_allowed(ws):
        await ws.close(code=1008)
        return

    pub = await get_public_state(game_id)
    if pub is None:
        await ws.accept()
        await ws.send_json(WSOutgoing(type="error", payload={"detail": "game not found"}).model_dump())
        await ws.close()
        return

    if not await player_exists(game_id, player_id):
        await ws.accept()
        await ws.send_json(WSOutgoing(type="error", payload={"detail": "unauthorized"}).model_dump())
        await ws.close()
        return

    await manager.connect(game_id, player_id, ws)
    config = {"configurable": {"thread_id": game_id}}
    client_ip = _client_ip(ws)

    # Reconnect: replay history from checkpoint
    try:
        snap = turn_graph.get_state(config)
        values = snap.values if snap else None
        if values and values.get("case"):
            await manager.send_to_player(
                game_id,
                player_id,
                WSOutgoing(type="history", payload=history_payload(values)).model_dump(),
            )
    except Exception:
        logger.exception("history replay failed game_id=%s", game_id)
        await manager.send_to_player(
            game_id,
            player_id,
            WSOutgoing(type="error", payload={"detail": "history_failed"}).model_dump(),
        )

    try:
        while True:
            try:
                raw = await ws.receive_json()
                incoming = WSIncoming.model_validate(raw)
                payload = incoming.model_dump()
                payload["player_id"] = player_id

                # Rate limits (per player + per IP for LLM-backed actions)
                if incoming.type == "question":
                    if not await check_ip_rate_limit(
                        client_ip, "llm", IP_LLM_LIMIT, IP_LLM_WINDOW
                    ):
                        await manager.send_to_player(
                            game_id,
                            player_id,
                            WSOutgoing(
                                type="error",
                                payload={"detail": "rate_limited"},
                            ).model_dump(),
                        )
                        continue
                    action = f"q:{incoming.agent_id}"
                    allowed = await check_rate_limit(
                        game_id, player_id, action, QUESTION_COOLDOWN_SECONDS
                    )
                    if not allowed:
                        await manager.send_to_player(
                            game_id,
                            player_id,
                            WSOutgoing(
                                type="error",
                                payload={"detail": "rate_limited", "cooldown": QUESTION_COOLDOWN_SECONDS},
                            ).model_dump(),
                        )
                        continue
                elif incoming.type in ("start_debate", "next_argument"):
                    if not await check_ip_rate_limit(
                        client_ip, "llm", IP_LLM_LIMIT, IP_LLM_WINDOW
                    ):
                        await manager.send_to_player(
                            game_id,
                            player_id,
                            WSOutgoing(
                                type="error",
                                payload={"detail": "rate_limited"},
                            ).model_dump(),
                        )
                        continue
                    allowed = await check_rate_limit(
                        game_id, player_id, "argument", ARGUMENT_COOLDOWN_SECONDS
                    )
                    if not allowed:
                        await manager.send_to_player(
                            game_id,
                            player_id,
                            WSOutgoing(
                                type="error",
                                payload={"detail": "rate_limited", "cooldown": ARGUMENT_COOLDOWN_SECONDS},
                            ).model_dump(),
                        )
                        continue
                elif incoming.type in ("cast_vote", "call_vote"):
                    allowed = await check_rate_limit(
                        game_id, player_id, incoming.type, VOTE_COOLDOWN_SECONDS
                    )
                    if not allowed:
                        await manager.send_to_player(
                            game_id,
                            player_id,
                            WSOutgoing(
                                type="error",
                                payload={"detail": "rate_limited"},
                            ).model_dump(),
                        )
                        continue

                # Refresh expected_players from live Redis join count when voting
                updates: dict = {"incoming_event": payload}
                if incoming.type == "cast_vote":
                    count = await player_count(game_id)
                    if count > 0:
                        updates["expected_players"] = count

                result = turn_graph.invoke(updates, config=config)
                await _sync_public_from_graph(game_id, result)

                if incoming.type in ("start_debate", "next_argument"):
                    if result.get("pending_response"):
                        await manager.broadcast(
                            game_id,
                            WSOutgoing(
                                type="argument",
                                payload={
                                    "agent_id": result.get("pending_agent_id"),
                                    "text": result.get("pending_response"),
                                    "debate_round": result.get("debate_round"),
                                    "status": result.get("status"),
                                },
                            ).model_dump(),
                        )
                    await manager.broadcast(
                        game_id,
                        WSOutgoing(
                            type="status",
                            payload={"status": result.get("status"), "debate_round": result.get("debate_round")},
                        ).model_dump(),
                    )

                elif incoming.type == "question":
                    await manager.send_to_player(
                        game_id,
                        player_id,
                        WSOutgoing(
                            type="agent_reply",
                            payload={
                                "agent_id": incoming.agent_id,
                                "text": result.get("pending_response"),
                            },
                        ).model_dump(),
                    )
                    # Broadcast Q&A so others see it
                    await manager.broadcast(
                        game_id,
                        WSOutgoing(
                            type="agent_reply",
                            payload={
                                "agent_id": incoming.agent_id,
                                "text": result.get("pending_response"),
                                "question": incoming.text,
                                "player_id": player_id,
                            },
                        ).model_dump(),
                        exclude=player_id,
                    )
                    revealed = result.get("evidence_revealed") or []
                    if revealed:
                        from app.public_views import evidence_public

                        await manager.broadcast(
                            game_id,
                            WSOutgoing(
                                type="evidence_revealed",
                                payload={"items": evidence_public(result["case"], revealed)},
                            ).model_dump(),
                        )

                elif incoming.type == "call_vote":
                    await manager.broadcast(
                        game_id,
                        WSOutgoing(
                            type="status",
                            payload={"status": result.get("status")},
                        ).model_dump(),
                    )

                elif incoming.type == "cast_vote":
                    await manager.broadcast(
                        game_id,
                        WSOutgoing(
                            type="vote_progress",
                            payload={
                                "vote_count": len(result.get("votes") or {}),
                                "expected_voters": result.get("expected_players"),
                                "status": result.get("status"),
                            },
                        ).model_dump(),
                    )
                    if result.get("status") == "finished":
                        await manager.broadcast(
                            game_id,
                            WSOutgoing(
                                type="verdict",
                                payload={
                                    "correct": result.get("correct"),
                                    "yes_or_no": "Yes" if result.get("correct") else "No",
                                    "majority": result.get("majority"),
                                    "vote_reason": result.get("vote_reason"),
                                    "verdict_truth": result["case"]["verdict_truth"],
                                    "solution": result["case"]["solution"],
                                    "votes": result.get("votes"),
                                },
                            ).model_dump(),
                        )

            except WebSocketDisconnect:
                raise
            except ValidationError as e:
                await manager.send_to_player(
                    game_id,
                    player_id,
                    WSOutgoing(
                        type="error",
                        payload={"detail": e.errors(include_context=False, include_url=False)},
                    ).model_dump(),
                )
            except Exception as e:
                logger.exception("ws handler error game_id=%s", game_id)
                msg = str(e).lower()
                detail = (
                    "llm_unavailable"
                    if "openai_api_key" in msg or "api key is missing" in msg
                    else "internal_error"
                )
                await manager.send_to_player(
                    game_id,
                    player_id,
                    WSOutgoing(type="error", payload={"detail": detail}).model_dump(),
                )

    except WebSocketDisconnect:
        manager.disconnect(game_id, player_id)


_dist = Path(os.getenv("FRONTEND_DIST") or "frontend/dist")
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
