import { Landmark, BookOpen, Paperclip, MessagesSquare, Play, Vote } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import PagedReader from "./PagedReader.jsx";

const SPRITES = {
  prosecution: "/court/prosecutor-vale.png",
  defense: "/court/defense-okonkwo.png",
  judge: "/court/judge-sprite.png",
  defendant: "/court/defendant-evelyn.png",
};

const VIEWS = [
  { id: "scene", label: "Court", Icon: Landmark },
  { id: "record", label: "Record", Icon: BookOpen },
  { id: "exhibits", label: "Exhibits", Icon: Paperclip },
  { id: "chat", label: "Chat", Icon: MessagesSquare },
];

const compactLayout = window.matchMedia("(max-width: 820px)");
function watchLayout(callback) {
  compactLayout.addEventListener("change", callback);
  return () => compactLayout.removeEventListener("change", callback);
}

function Actor({ id, name, role, speaking, onSelect, selected }) {
  const content = (
    <>
      <span className="portrait">
        <img src={SPRITES[id]} alt="" width="1024" height="1536" />
      </span>
      <span className="nametag">
        <span className="lbl">{role}</span>
        <strong>{name}</strong>
      </span>
    </>
  );
  const className = `actor ${id}${speaking ? " speaking" : ""}`;

  return onSelect ? (
    <button type="button" className={className} aria-pressed={selected} onClick={onSelect}>
      {content}
    </button>
  ) : (
    <div className={className}>{content}</div>
  );
}

export default function Courtroom({
  lobby,
  connection,
  error,
  status,
  messages,
  evidence,
  agentId,
  setAgentId,
  question,
  setQuestion,
  onAsk,
  onSend,
  teamChat,
  chatDraft,
  setChatDraft,
  onSendChat,
  questionsAsked,
  maxQuestions,
  playerName,
}) {
  const [view, setView] = useState("scene");
  const compact = useSyncExternalStore(watchLayout, () => compactLayout.matches);
  const panel = view === "scene" ? "record" : view;
  const statements = messages.filter((m) => m.role === "argument" || m.role === "qa" || m.role === "you");
  const lastCounsel = statements.findLast((m) => m.role !== "you" && m.agentId);
  const lastVoteUpdate = messages.findLast((m) => m.role === "system" && m.text.startsWith("Votes "));
  const questionsLeft = Math.max(0, maxQuestions - questionsAsked);
  const connected = connection === "connected";
  const opening = status === "lobby" || status === "debating";
  const questioning = status === "questioning";
  const counselNames = {
    prosecution: lobby?.prosecution?.name || "Prosecution",
    defense: lobby?.defense?.name || "Defense",
  };
  const judgeSpeaking = status === "voting" || (!lastCounsel && opening);
  const speaker = judgeSpeaking ? "judge" : lastCounsel?.agentId;
  const statement = lastCounsel?.text || "";
  const answerStart = lastCounsel?.role === "qa" ? statement.indexOf("\nA:") : -1;
  const dialogue = judgeSpeaking
    ? status === "voting"
      ? "Members of the jury, you may now vote."
      : "Court is in session. Counsel may begin."
    : answerStart >= 0 ? statement.slice(answerStart + 3).trim() : statement;

  return (
    <main className="courtroom" data-view={view}>
      <h1 className="sr-only">Courtroom</h1>
      <section className="gallery" aria-label="Courtroom scene">
        <Actor id="judge" name="The Bench" role="Judge" speaking={judgeSpeaking} />
        <Actor
          id="prosecution"
          name={counselNames.prosecution}
          role="Prosecution"
          speaking={speaker === "prosecution"}
          selected={agentId === "prosecution"}
          onSelect={questioning ? () => setAgentId("prosecution") : undefined}
        />
        <Actor id="defendant" name={lobby?.defendant?.name || "Accused"} role="Defendant" />
        <Actor
          id="defense"
          name={counselNames.defense}
          role="Defense"
          speaking={speaker === "defense"}
          selected={agentId === "defense"}
          onSelect={questioning ? () => setAgentId("defense") : undefined}
        />
        <div className={`scene-dialogue ${speaker || ""}`} aria-live="polite">
          {dialogue && (
            <div className="speech">
              <PagedReader title={judgeSpeaking ? "The Bench" : counselNames[speaker]} revision={dialogue}>
                <p>{dialogue}</p>
              </PagedReader>
            </div>
          )}
        </div>
      </section>

      <section className="floor-actions" aria-label="Court actions">
        {!connected && (
          <p className="banner" role="status">
            {connection === "disconnected"
              ? "Connection closed. Reload the page to rejoin the courtroom."
              : "Connecting to the courtroom…"}
          </p>
        )}
        {error && <p className="banner error" role="alert">{error}</p>}

        {opening && (
          <div className="deliberation-bar">
            <div>
              <h2>Hear the arguments</h2>

            </div>
            <button className="primary" disabled={!connected} onClick={() => onSend({ type: "next_argument" })}>
              <Play aria-hidden="true" />Hear next argument
            </button>
          </div>
        )}

        {questioning && (
          <>
            <div className="deliberation-bar">
              <div>
                <h2>Question counsel</h2>
                <p className="hint">{questionsLeft} {questionsLeft === 1 ? "question" : "questions"} remaining</p>
              </div>
              <button className="secondary" disabled={!connected} onClick={() => onSend({ type: "call_vote" })}>
                <Vote aria-hidden="true" />Call the vote
              </button>
            </div>
            <form className="floor-composer" onSubmit={(event) => { event.preventDefault(); onAsk(); }}>
              <select className="counsel-select" aria-label="Choose counsel" value={agentId}
                onChange={event => setAgentId(event.target.value)}>
                <option value="prosecution">Prosecution</option>
                <option value="defense">Defense</option>
              </select>
              <div className="direct-toggle" role="group" aria-label="Choose counsel">
                <button type="button" aria-pressed={agentId === "prosecution"} onClick={() => setAgentId("prosecution")}>
                  Prosecution
                </button>
                <button type="button" aria-pressed={agentId === "defense"} onClick={() => setAgentId("defense")}>
                  Defense
                </button>
              </div>
              <div className="inline-form">
                <input
                  aria-label="Your question"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder={questionsLeft ? "Type your question…" : "Question limit reached"}
                  maxLength={500}
                  disabled={!questionsLeft}
                />
                <button className="primary" disabled={!connected || !questionsLeft || !question.trim()}>
                  Ask
                </button>
              </div>
            </form>
          </>
        )}

        {status === "voting" && (
          <div className="deliberation-bar">
            <div>
              <h2>Cast your vote</h2>
              {lastVoteUpdate && <p className="hint" role="status">{lastVoteUpdate.text}</p>}
            </div>
            <div className="vote-buttons">
              <button className="primary" disabled={!connected} onClick={() => onSend({ type: "cast_vote", vote: "guilty" })}>
                Guilty
              </button>
              <button className="secondary" disabled={!connected} onClick={() => onSend({ type: "cast_vote", vote: "not_guilty" })}>
                Not guilty
              </button>
            </div>
          </div>
        )}
      </section>

      <nav className="court-nav" aria-label="Courtroom views">
        {VIEWS.map(({ id, label, Icon }) => (
          <button type="button" key={id} className={`view-${id}`} aria-pressed={(compact ? view : panel) === id}
            onClick={() => setView(id)}>
            <Icon aria-hidden="true" />{label}{id === "exhibits" && evidence.length > 0 ? ` (${evidence.length})` : ""}
          </button>
        ))}
      </nav>
      <aside className="court-floor" aria-label="Jury materials">
        {panel === "record" && (
          <PagedReader title="Counsel record" revision={statements.at(-1)?.id}>
            {!statements.length && <p className="muted">Arguments and answers will appear here.</p>}
            {statements.slice().reverse().map(m => (
              <article key={m.id} className={`slip ${m.role} ${m.agentId || ""}`}>
                <h3 className="who">{m.role === "you" ? playerName : counselNames[m.agentId] || "Counsel"}</h3>
                <p>{m.text}</p>
              </article>
            ))}
          </PagedReader>
        )}
        {panel === "exhibits" && (
          <PagedReader title="Exhibits" revision={evidence.length}>
            {!evidence.length && <p className="muted">No exhibits entered.</p>}
            {evidence.map((item, i) => (
              <article className="slip" key={item.id}><h3 className="who">Exhibit {String.fromCharCode(65+i)}</h3><p>{item.text}</p></article>
            ))}
          </PagedReader>
        )}
        {panel === "chat" && (
          <>
            <PagedReader title="Jury chat" revision={teamChat.at(-1)?.id}>
              {!teamChat.length && <p className="muted">No messages yet.</p>}
              {teamChat.slice().reverse().map(m => (
                <article key={m.id} className="chat-line"><strong className="who">{m.player_name || "Juror"}</strong><p>{m.text}</p></article>
              ))}
            </PagedReader>
            <form className="inline-form" onSubmit={(event) => { event.preventDefault(); onSendChat(); }}>
              <input
                aria-label="Message the jury"
                value={chatDraft}
                onChange={(event) => setChatDraft(event.target.value)}
                placeholder="Message the jury…"
                maxLength={280}
              />
              <button type="submit" className="secondary" disabled={!connected || !chatDraft.trim()}>
                Send
              </button>
            </form>
          </>
        )}
      </aside>
    </main>
  );
}
