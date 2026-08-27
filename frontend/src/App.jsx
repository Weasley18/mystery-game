import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import Courtroom from "./Courtroom.jsx";

const API_HTTP =
  import.meta.env.VITE_API_HTTP ??
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
const API_WS =
  import.meta.env.VITE_API_WS ??
  (import.meta.env.DEV
    ? "ws://127.0.0.1:8000"
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`);

const STORAGE_KEY = "courtroom_session";

/** Map API / WS error payloads to a single juror-facing warning. */
function formatWarning(detail, cooldown) {
  const raw = detail;
  let code = raw;
  if (raw && typeof raw === "object") {
    if (typeof raw.detail === "string") code = raw.detail;
    else if (Array.isArray(raw.detail) || Array.isArray(raw)) code = "validation_error";
    else code = JSON.stringify(raw);
  }
  code = String(code ?? "unknown").trim();

  // Strip JSON quotes / FastAPI detail wrappers: {"detail":"rate_limited"}
  const unquoted = code.replace(/^"+|"+$/g, "");
  try {
    const parsed = JSON.parse(code);
    if (typeof parsed === "string") code = parsed;
    else if (parsed && typeof parsed.detail === "string") code = parsed.detail;
    else if (Array.isArray(parsed)) code = "validation_error";
  } catch {
    code = unquoted;
  }

  const wait =
    typeof cooldown === "number" && cooldown > 0
      ? ` Please wait about ${cooldown} second${cooldown === 1 ? "" : "s"}.`
      : "";

  const messages = {
    llm_unavailable:
      "Counsel cannot speak until the court has a valid OpenAI API key in .env. Add the key, then restart the server.",
    internal_error:
      "The court ran into an unexpected problem. Please try that action again in a moment.",
    history_failed:
      "The court could not restore the trial transcript. Refresh or rejoin the session.",
    rate_limited: `The court asks you to slow down.${wait || " Please wait a few seconds before trying again."}`,
    unauthorized:
      "Your seat in this gallery is no longer valid. Rejoin the docket from the home screen.",
    "game not found":
      "This docket could not be found. Check the session id, or open a new court.",
    validation_error:
      "That request was incomplete or invalid. Check your question or vote and try again.",
    game_full: "This gallery is full. Join another docket or open a new court.",
    "scenario not found": "That case file is not on the docket. Choose another scenario.",
    "WebSocket error":
      "The live connection to the court faltered. Check your network, then re-enter the courtroom.",
    Disconnected:
      "You were disconnected from the courtroom. Re-enter when you are ready to continue.",
    "Failed to fetch":
      "Could not reach the court server. Check that the API is running, then try again.",
    NetworkError:
      "Could not reach the court server. Check your connection and try again.",
  };

  const lower = String(code).toLowerCase();
  for (const [key, msg] of Object.entries(messages)) {
    if (lower === key.toLowerCase() || lower.includes(key.toLowerCase())) {
      return msg;
    }
  }

  // HTTP-ish bodies or unknown codes → generic, never dump raw JSON
  if (
    lower.startsWith("{") ||
    lower.startsWith("[") ||
    lower.includes("traceback") ||
    lower.includes("exception")
  ) {
    return "Something went wrong in the courtroom. Please try again.";
  }

  // Short human-ish strings from the API can pass through; otherwise generic
  if (code.length <= 120 && !/[_-]{2,}/.test(code) && /[a-zA-Z]/.test(code) && !/^\w+$/.test(code)) {
    return code;
  }

  return "Something went wrong in the courtroom. Please try again.";
}

async function readHttpWarning(res) {
  let body = "";
  try {
    body = await res.text();
  } catch {
    body = "";
  }
  let detail = body;
  try {
    const parsed = JSON.parse(body);
    detail = parsed.detail ?? parsed;
  } catch {
    /* keep raw text */
  }
  if (res.status === 429) return formatWarning("rate_limited");
  if (res.status === 404) {
    return formatWarning(
      typeof detail === "string" && detail.toLowerCase().includes("scenario")
        ? "scenario not found"
        : "game not found"
    );
  }
  if (res.status === 403) return formatWarning("game_full");
  return formatWarning(detail);
}

function loadSession() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

function saveSession(data) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function App() {
  const [screen, setScreen] = useState("home");
  const [scenarios, setScenarios] = useState([]);
  const [scenarioId, setScenarioId] = useState("manor_poison");
  const [playerName, setPlayerName] = useState("Juror");
  const [joinGameId, setJoinGameId] = useState("");
  const [expectedPlayers, setExpectedPlayers] = useState(1);
  const [gameId, setGameId] = useState(null);
  const [playerId, setPlayerId] = useState(null);
  const [lobby, setLobby] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const [agentId, setAgentId] = useState("prosecution");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [verdict, setVerdict] = useState(null);
  const [status, setStatus] = useState("lobby");
  const wsRef = useRef(null);
  const chatEndRef = useRef(null);

  const pushMessage = useCallback((msg) => {
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), ...msg }]);
  }, []);

  // Load scenarios + optional reconnect session
  useEffect(() => {
    fetch(`${API_HTTP}/scenarios`)
      .then((r) => r.json())
      .then((list) => {
        setScenarios(list);
        if (list[0]) setScenarioId(list[0].id);
      })
      .catch((e) => setError(formatWarning(e.message || e || "Failed to load scenarios")));

    const sess = loadSession();
    if (sess?.gameId && sess?.playerId) {
      setGameId(sess.gameId);
      setPlayerId(sess.playerId);
      setPlayerName(sess.playerName || "Juror");
      setScreen("lobby");
    }
  }, []);

  // Lobby polling
  useEffect(() => {
    if (!gameId || (screen !== "lobby" && screen !== "play")) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API_HTTP}/games/${gameId}/state`);
        if (!res.ok) throw new Error(await readHttpWarning(res));
        const data = await res.json();
        if (!cancelled) {
          setLobby(data);
          setStatus(data.status || "lobby");
          if (data.evidence_revealed) setEvidence(data.evidence_revealed);
          if (data.status === "finished" && data.correct != null) {
            setVerdict({
              correct: data.correct,
              yes_or_no: data.correct ? "Yes" : "No",
              majority: data.majority,
              solution: data.solution,
              verdict_truth: data.verdict_truth,
              vote_reason: data.vote_reason,
            });
          }
        }
      } catch (e) {
        if (!cancelled) setError(formatWarning(e.message || e));
      }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [gameId, screen]);

  // WebSocket
  useEffect(() => {
    if (screen !== "play" || !gameId || !playerId) return;
    const ws = new WebSocket(`${API_WS}/ws/${gameId}/${playerId}`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch {
        pushMessage({ role: "system", text: ev.data });
        return;
      }
      if (data.type === "history") {
        const t = data.payload?.transcript || [];
        setMessages(
          t.map((entry) => ({
            id: crypto.randomUUID(),
            role: entry.kind === "argument" ? "argument" : entry.kind === "evidence" ? "system" : "qa",
            agentId: entry.agent_id,
            text:
              entry.kind === "qa"
                ? `Q: ${entry.question}\nA: ${entry.text}`
                : entry.text,
          }))
        );
        if (data.payload?.evidence_revealed) setEvidence(data.payload.evidence_revealed);
        if (data.payload?.status) setStatus(data.payload.status);
        if (data.payload?.correct != null) {
          setVerdict({
            correct: data.payload.correct,
            yes_or_no: data.payload.correct ? "Yes" : "No",
            solution: data.payload.solution,
            verdict_truth: data.payload.verdict_truth,
            majority: data.payload.majority,
          });
        }
      } else if (data.type === "argument") {
        setStatus(data.payload?.status || "debating");
        pushMessage({
          role: "argument",
          agentId: data.payload?.agent_id,
          text: data.payload?.text,
        });
      } else if (data.type === "agent_reply") {
        const q = data.payload?.question;
        pushMessage({
          role: "qa",
          agentId: data.payload?.agent_id,
          text: q ? `Q: ${q}\nA: ${data.payload?.text}` : data.payload?.text,
        });
      } else if (data.type === "evidence_revealed") {
        const items = data.payload?.items || [];
        setEvidence(items);
        pushMessage({
          role: "system",
          text: `Evidence: ${items.map((i) => i.text).join(" | ")}`,
        });
      } else if (data.type === "status") {
        setStatus(data.payload?.status || status);
      } else if (data.type === "vote_progress") {
        setStatus(data.payload?.status || "voting");
        pushMessage({
          role: "system",
          text: `Votes ${data.payload?.vote_count}/${data.payload?.expected_voters}`,
        });
      } else if (data.type === "verdict") {
        setStatus("finished");
        setVerdict(data.payload);
        pushMessage({
          role: "system",
          text: `Verdict check: ${data.payload?.yes_or_no} (majority=${data.payload?.majority}, truth=${data.payload?.verdict_truth})`,
        });
      } else if (data.type === "error") {
        pushMessage({
          role: "error",
          text: formatWarning(data.payload?.detail ?? data.payload, data.payload?.cooldown),
        });
      }
    };

    ws.onerror = () =>
      pushMessage({
        role: "error",
        text: formatWarning("WebSocket error"),
      });
    ws.onclose = () =>
      pushMessage({
        role: "system",
        text: "Connection closed. Re-enter the courtroom if you need to continue.",
      });

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [screen, gameId, playerId, pushMessage, status]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function createGame() {
    setBusy(true);
    setError(null);
    try {
      const create = await fetch(`${API_HTTP}/games`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario_id: scenarioId,
          expected_players: Number(expectedPlayers) || 1,
        }),
      });
      if (!create.ok) throw new Error(await readHttpWarning(create));
      const game = await create.json();
      const join = await fetch(`${API_HTTP}/games/${game.game_id}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player_name: playerName }),
      });
      if (!join.ok) throw new Error(await readHttpWarning(join));
      const player = await join.json();
      setGameId(game.game_id);
      setPlayerId(player.player_id);
      saveSession({ gameId: game.game_id, playerId: player.player_id, playerName });
      setLobby({ ...game, status: "lobby", players: [playerName] });
      setScreen("lobby");
    } catch (e) {
      setError(formatWarning(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function joinExisting() {
    setBusy(true);
    setError(null);
    try {
      const gid = joinGameId.trim();
      const join = await fetch(`${API_HTTP}/games/${gid}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player_name: playerName }),
      });
      if (!join.ok) throw new Error(await readHttpWarning(join));
      const player = await join.json();
      setGameId(gid);
      setPlayerId(player.player_id);
      saveSession({ gameId: gid, playerId: player.player_id, playerName });
      setScreen("lobby");
    } catch (e) {
      setError(formatWarning(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  function send(payload) {
    if (!wsRef.current || wsRef.current.readyState !== 1) return;
    wsRef.current.send(JSON.stringify(payload));
  }

  function sendQuestion() {
    const text = question.trim();
    if (!text) return;
    send({ type: "question", agent_id: agentId, text });
    pushMessage({ role: "you", agentId, text });
    setQuestion("");
  }

  const selected = scenarios.find((s) => s.id === scenarioId);
  const phase =
    status === "debating" || status === "lobby"
      ? "DEBATE"
      : status === "questioning"
        ? "EXAMINATION"
        : status === "voting"
          ? "JURY"
          : status === "finished"
            ? "SEALED"
            : String(status || "DOCKET").toUpperCase();

  return (
    <div className={`app ${screen === "play" ? "in-session" : "antechamber"}`}>
      <div className="scanlines" aria-hidden="true" />
      <header className="topbar">
        <div className="brand">
          <span className="seal" aria-hidden="true" />
          <h1>THE DOCKET</h1>
        </div>
        {screen !== "home" ? (
          <div className="case-chip">
            <span className="lbl">Case</span>
            <strong>{lobby?.title || selected?.title || "Session"}</strong>
            <em>{phase}</em>
          </div>
        ) : (
          <p className="kicker">All rise.</p>
        )}
        <div className="juror-meta">
          <span className="lbl">Juror</span>
          <strong>{playerName}</strong>
          {gameId && <code>{gameId}</code>}
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      {screen === "home" && (
        <section className="docket">
          <div className="hero">
            <p className="eyebrow">You sit in the jury box</p>
            <h2>Two advocates. One sealed truth.</h2>
            <p>
              Watch counsel argue on the floor, then question them from your seat.
              Vote guilty or not guilty. The court answers only <em>Yes</em> or <em>No</em>.
            </p>
          </div>
          <div className="case-grid">
            {Array.isArray(scenarios) &&
              scenarios.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`case-card ${s.id === scenarioId ? "selected" : ""}`}
                onClick={() => setScenarioId(s.id)}
              >
                <span className="lvl">Level {s.level}</span>
                <h3>{s.title}</h3>
                <p>{s.case_summary}</p>
                <small>{s.setting}</small>
              </button>
            ))}
          </div>
          <div className="session-card">
            <h3>Open a session</h3>
            <label>
              Juror name
              <input value={playerName} onChange={(e) => setPlayerName(e.target.value)} />
            </label>
            <label>
              Expected jurors
              <input
                type="number"
                min={1}
                value={expectedPlayers}
                onChange={(e) => setExpectedPlayers(e.target.value)}
              />
            </label>
            <button className="primary" disabled={busy} onClick={createGame}>
              {busy ? "Convening…" : "Open court"}
            </button>
            <div className="or">or join an existing gallery</div>
            <label>
              Docket id
              <input
                value={joinGameId}
                onChange={(e) => setJoinGameId(e.target.value)}
                placeholder="abc123def0"
              />
            </label>
            <button className="secondary" disabled={busy || !joinGameId.trim()} onClick={joinExisting}>
              Join gallery
            </button>
          </div>
        </section>
      )}

      {screen === "lobby" && lobby && (
        <section className="lobby-grid">
          <article className="file">
            <p className="eyebrow">The charge</p>
            <h2>{lobby.title}</h2>
            <p className="lead">{lobby.charge}</p>
            <p>{lobby.case_summary}</p>
            <p>
              Defendant <strong>{lobby.defendant?.name}</strong>
            </p>
            <img className="lobby-sprite accused" src="/court/defendant-evelyn.png" alt="" />
            <p className="hint">Share docket <code>{gameId}</code> · {lobby.status}</p>
            <button className="primary" onClick={() => setScreen("play")}>
              Enter courtroom
            </button>
            <button
              className="secondary"
              onClick={() => {
                sessionStorage.removeItem(STORAGE_KEY);
                setScreen("home");
                setGameId(null);
                setPlayerId(null);
              }}
            >
              Leave
            </button>
          </article>
          <div className="counsel-pair">
            <article className="counsel prosecution">
              <img className="lobby-sprite" src="/court/prosecutor-vale.png" alt="" />
              <span className="lbl">Prosecution</span>
              <h3>{lobby.prosecution?.name}</h3>
              <p>{lobby.prosecution?.persona}</p>
            </article>
            <article className="counsel defense">
              <img className="lobby-sprite" src="/court/defense-okonkwo.png" alt="" />
              <span className="lbl">Defense</span>
              <h3>{lobby.defense?.name}</h3>
              <p>{lobby.defense?.persona}</p>
            </article>
            <article className="file jurors">
              <h3>Gallery</h3>
              <ul>
                {(lobby.players || []).map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </article>
          </div>
        </section>
      )}

      {screen === "play" && (
        <Courtroom
          lobby={lobby}
          status={status}
          messages={messages}
          evidence={evidence}
          agentId={agentId}
          setAgentId={setAgentId}
          question={question}
          setQuestion={setQuestion}
          onAsk={sendQuestion}
          onSend={send}
          onBack={() => setScreen("lobby")}
          chatEndRef={chatEndRef}
        />
      )}

      {verdict && (
        <div className="verdict-veil">
          <article className="verdict-sheet">
            <span className="lbl">The tribunal has reached a</span>
            <h2>Verdict</h2>
            <p className={`mega ${verdict.correct ? "yes" : "no"}`}>{verdict.yes_or_no}</p>
            <p className="sub">
              {verdict.correct ? "Liability confirmed" : "The gallery missed the sealed truth"}
            </p>
            <div className="reason">
              <div className="reason-head">
                <span>Majority rule</span>
                <strong>
                  {verdict.majority || "tie"}
                  {verdict.vote_reason === "tie" ? " · hung jury" : ""}
                </strong>
              </div>
              <p>{verdict.solution}</p>
            </div>
            <button
              className="secondary"
              onClick={() => {
                setVerdict(null);
                setScreen("home");
                sessionStorage.removeItem(STORAGE_KEY);
                setGameId(null);
                setPlayerId(null);
              }}
            >
              Seal docket & proceed
            </button>
          </article>
        </div>
      )}
    </div>
  );
}

export default App;
