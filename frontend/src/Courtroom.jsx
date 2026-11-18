import { useMemo, useState } from "react";

const SPRITES = {
  prosecution: "/court/prosecutor-vale.png",
  defense: "/court/defense-okonkwo.png",
  judge: "/court/judge-sprite.png",
  defendant: "/court/defendant-evelyn.png",
};

const STATEMENTS_PER_PAGE = 4;

function Actor({ className, src, name, role, speaking, bubble, onSelect, selectable }) {
  return (
    <div
      className={`actor ${className} ${speaking ? "speaking" : ""} ${selectable ? "selectable" : ""}`}
      onClick={selectable ? onSelect : undefined}
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      onKeyDown={
        selectable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") onSelect?.();
            }
          : undefined
      }
    >
      <div className="sprite-wrap">
        {bubble ? (
          <div className="speech">
            <span className="speech-who">{name || role}</span>
            <p>{bubble}</p>
          </div>
        ) : null}
        <div className="sprite-figure">
          <img src={src} alt={name || role} />
        </div>
      </div>
      <div className="nametag">
        <span>{role}</span>
        <strong>{name}</strong>
      </div>
    </div>
  );
}

export default function Courtroom({
  lobby,
  status,
  messages,
  evidence,
  agentId,
  setAgentId,
  question,
  setQuestion,
  onAsk,
  onSend,
  chatEndRef,
  teamChat,
  chatDraft,
  setChatDraft,
  onSendChat,
  questionsAsked,
  maxQuestions,
  playerName,
}) {
  const [page, setPage] = useState(0);

  const lastCounsel = [...messages]
    .reverse()
    .find((m) => (m.role === "argument" || m.role === "qa") && m.agentId);
  const speaking = lastCounsel?.agentId;
  const bubbleFor = (id) => {
    if (lastCounsel?.agentId !== id) return "";
    let t = lastCounsel.text || "";
    if (lastCounsel.role === "qa") {
      const idx = t.indexOf("\nA:");
      if (idx >= 0) t = t.slice(idx + 3).trim();
    }
    return t;
  };

  const lastSystem = [...messages]
    .reverse()
    .find((m) => m.role === "system" && !String(m.text).startsWith("Disconnected"));

  const statements = useMemo(
    () => messages.filter((m) => m.role === "argument" || m.role === "qa" || m.role === "you"),
    [messages]
  );

  const pageCount = Math.max(1, Math.ceil(statements.length / STATEMENTS_PER_PAGE));
  const safePage = Math.min(page, pageCount - 1);
  const pageItems = statements.slice(
    safePage * STATEMENTS_PER_PAGE,
    safePage * STATEMENTS_PER_PAGE + STATEMENTS_PER_PAGE
  );

  const questionsLeft = Math.max(0, maxQuestions - questionsAsked);
  const atQuestionCap = questionsAsked >= maxQuestions;

  return (
    <section className="courtroom">
      <div className="court-bg" aria-hidden="true" />
      <div className="court-vignette" aria-hidden="true" />

      <div className="gallery">
        <div className="bench">
          <Actor
            className="judge"
            src={SPRITES.judge}
            role="The Court"
            name="The Bench"
            speaking={false}
            bubble={
              status === "voting"
                ? "Members of the jury, you may now vote."
                : lastSystem &&
                    speaking !== "prosecution" &&
                    speaking !== "defense"
                  ? lastSystem.text
                  : ""
            }
          />
        </div>

        <Actor
          className="defendant"
          src={SPRITES.defendant}
          role="Defendant"
          name={lobby?.defendant?.name || "Accused"}
        />

        <Actor
          className={`prosecution ${agentId === "prosecution" && status === "questioning" ? "chosen" : ""}`}
          src={SPRITES.prosecution}
          role="Prosecution"
          name={lobby?.prosecution?.name || "State"}
          speaking={speaking === "prosecution"}
          bubble={bubbleFor("prosecution")}
          selectable={status === "questioning"}
          onSelect={() => setAgentId("prosecution")}
        />

        <Actor
          className={`defense ${agentId === "defense" && status === "questioning" ? "chosen" : ""}`}
          src={SPRITES.defense}
          role="Defense"
          name={lobby?.defense?.name || "Counsel"}
          speaking={speaking === "defense"}
          bubble={bubbleFor("defense")}
          selectable={status === "questioning"}
          onSelect={() => setAgentId("defense")}
        />
      </div>

      <div className="court-floor">
        <div className="statements-panel">
          <div className="panel-head">
            <div>
              <span className="lbl">Statements</span>
              <h2>Counsel record</h2>
            </div>
            <div className="page-ctrl">
              <button
                type="button"
                className="ghost"
                disabled={safePage <= 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                ←
              </button>
              <span>
                {safePage + 1}/{pageCount}
              </span>
              <button
                type="button"
                className="ghost"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              >
                →
              </button>
            </div>
          </div>
          <div className="record-strip statements-strip">
            {pageItems.length === 0 && (
              <p className="muted">The clerk waits. Ask the court to begin arguments.</p>
            )}
            {pageItems.map((m) => (
              <div key={m.id} className={`slip ${m.role} ${m.agentId || ""}`}>
                <span className="who">
                  {m.role === "you"
                    ? `${playerName || "You"} (juror)`
                    : m.agentId === "prosecution"
                      ? lobby?.prosecution?.name || "Prosecution"
                      : m.agentId === "defense"
                        ? lobby?.defense?.name || "Defense"
                        : m.role}
                </span>
                <p>{m.text}</p>
              </div>
            ))}
          </div>
        </div>

        <aside className="exhibits-card floor-exhibits">
          <span className="lbl">Exhibits</span>
          <ul>
            {evidence.length === 0 && <li className="muted">None entered.</li>}
            {evidence.map((item, i) => (
              <li key={item.id}>
                <span>Exh {String.fromCharCode(65 + i)}</span>
                {item.text}
              </li>
            ))}
          </ul>
        </aside>

        <div className="floor-actions">
          {status === "questioning" && (
            <form
              className="composer floor-composer"
              onSubmit={(e) => {
                e.preventDefault();
                if (!atQuestionCap) onAsk();
              }}
            >
              <div className="composer-meta">
                <p className="direct">Ask counsel · {questionsLeft} left</p>
                <div className="direct-toggle">
                  <button
                    type="button"
                    className={agentId === "prosecution" ? "on prosecution" : ""}
                    onClick={() => setAgentId("prosecution")}
                  >
                    Prosecution
                  </button>
                  <button
                    type="button"
                    className={agentId === "defense" ? "on defense" : ""}
                    onClick={() => setAgentId("defense")}
                  >
                    Defense
                  </button>
                </div>
              </div>
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={atQuestionCap ? "Question limit reached" : "Type your question…"}
                disabled={atQuestionCap}
              />
              <button className="primary" type="submit" disabled={atQuestionCap || !question.trim()}>
                Ask
              </button>
            </form>
          )}

          {status === "voting" && (
            <div className="deliberation-bar">
              <span className="lbl">Your verdict</span>
              <button className="primary" onClick={() => onSend({ type: "cast_vote", vote: "guilty" })}>
                Guilty
              </button>
              <button
                className="secondary"
                onClick={() => onSend({ type: "cast_vote", vote: "not_guilty" })}
              >
                Not guilty
              </button>
            </div>
          )}

          {(status === "lobby" || status === "debating") && (
            <div className="deliberation-bar">
              <span className="lbl">Opening</span>
              <button className="primary" onClick={() => onSend({ type: "next_argument" })}>
                Hear next argument
              </button>
            </div>
          )}
        </div>

        <aside className="team-chat">
          <div className="panel-head">
            <span className="lbl">Team chat</span>
            <strong>Jury</strong>
          </div>
          <div className="chat-log">
            {teamChat.length === 0 && <p className="muted">Private notes for the gallery.</p>}
            {teamChat.map((m) => (
              <div key={m.id} className="chat-line">
                <span className="who">{m.player_name || "Juror"}</span>
                <p>{m.text}</p>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
          <form
            className="chat-compose"
            onSubmit={(e) => {
              e.preventDefault();
              onSendChat();
            }}
          >
            <input
              value={chatDraft}
              onChange={(e) => setChatDraft(e.target.value)}
              placeholder="Message the jury…"
              maxLength={280}
            />
            <button type="submit" className="secondary" disabled={!chatDraft.trim()}>
              Send
            </button>
          </form>
        </aside>
      </div>
    </section>
  );
}
