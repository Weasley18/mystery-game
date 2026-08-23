# AI courtroom mystery

FastAPI + Redis + LangGraph. Two counsel agents (**prosecution** vs **defense**)
argue an authored case; jurors ask questions, then vote **Guilty / Not guilty**.
The server answers **Yes** or **No** depending on whether the majority matches
the hidden `verdict_truth`.

## Loop

1. Pick a **level** (`GET /scenarios`) and create/join a game.
2. **Debate** — `next_argument` alternates prosecution / defense speeches.
3. **Questioning** — ask either counsel; evidence may unlock.
4. **Call the vote** → everyone `cast_vote`.
5. When all jurors have voted → **verdict** (`Yes` / `No` + solution).

## Layout

```
content/scenarios/     Authored JSON levels (3 shipped)
app/
  main.py              Routes + WS loop (history, rate limits)
  models.py            Courtroom GameState + WS schemas
  scenarios.py         Loader / public views
  public_views.py      Serialization that never leaks secrets early
  llm.py               OpenAI / Ollama routing
  redis_state.py       Lobby state, TTL, rate limits, pub/sub publish
  ws_manager.py        Local sockets + Redis pub/sub fan-out
  agents/counsel_agent.py
  graph/               Debate / Q&A / vote nodes + SQLite checkpointer
scripts/ws_client.py
scripts/load_test.py
frontend/              Vite + React courtroom UI
tests/
```

## Run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set OPENAI_API_KEY (required)
brew services start redis && redis-cli ping

# Optional: ollama serve && ollama pull llama3.2  (defense; OpenAI is used if Ollama is down)
uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

CLI: `python scripts/ws_client.py --scenario manor_poison`

Load test (API + Redis + live models — this spends tokens):

```bash
python scripts/load_test.py --games 10 --players 2 --debate 2
```

## Model routing

| Role | Default |
|------|---------|
| Prosecution | OpenAI (`OPENAI_SUSPECT_MODEL`) |
| Defense | Ollama → OpenAI fallback |

## Phases 4–6 features

- **Reconnect**: WS sends `history` on connect; lobby polls public state; `sessionStorage` in UI.
- **Vote completion**: finishes when `len(votes) >=` live Redis player count; ties → **No** (`vote_reason: tie`).
- **TTL**: Redis keys expire (`GAME_TTL_SECONDS`, default 24h).
- **Rate limits**: per-player question cooldown (default 8s) and argument/vote cooldowns.
- **Security**: injection block in counsel prompts + output filter; public APIs strip `verdict_truth` / `brief` / `solution` until finished.
- **SQLite checkpointer**: `CHECKPOINT_DB=data/checkpoints.db`
- **Redis pub/sub** broadcasts for multi-worker WS fan-out.

## Tests

```bash
pytest -q
```

## Levels shipped

| id | Level | Truth |
|----|-------|-------|
| `manor_poison` | 1 | guilty |
| `station_sabotage` | 2 | not_guilty |
| `gallery_theft` | 3 | guilty |
