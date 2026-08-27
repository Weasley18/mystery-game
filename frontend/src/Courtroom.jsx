const SPRITES = {
  prosecution: "/court/prosecutor-vale.png",
  defense: "/court/defense-okonkwo.png",
  judge: "/court/judge-sprite.png",
  defendant: "/court/defendant-evelyn.png",
};

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
      {bubble ? (
        <div className="speech">
          <span className="speech-who">{name || role}</span>
          <p>{bubble}</p>
        </div>
      ) : null}
      <div className="sprite-wrap">
        <img src={src} alt={name || role} />
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
  onBack,
  chatEndRef,
}) {
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
    return t.length > 280 ? `${t.slice(0, 277)}…` : t;
  };

  const lastSystem = [...messages]
    .reverse()
    .find((m) => m.role === "system" && !String(m.text).startsWith("Disconnected"));

  const phaseLabel =
    status === "debating" || status === "lobby"
      ? "Opening arguments"
      : status === "questioning"
        ? "Juror examination"
        : status === "voting"
          ? "The jury retires"
          : status === "finished"
            ? "Court adjourned"
            : "In session";

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

      <div className="jury-rail">
        <div className="rail-head">
          <div>
            <span className="lbl">Jury box</span>
            <h2>{phaseLabel}</h2>
          </div>
          <div className="rail-actions">
            {(status === "lobby" || status === "debating") && (
              <button className="primary" onClick={() => onSend({ type: "next_argument" })}>
                Hear next argument
              </button>
            )}
            {status === "questioning" && (
              <button className="secondary" onClick={() => onSend({ type: "call_vote" })}>
                Call the vote
              </button>
            )}
            <button className="ghost" onClick={onBack}>
              Leave chamber
            </button>
          </div>
        </div>

        <div className="rail-body">
          <div className="record-strip">
            {messages.length === 0 && (
              <p className="muted">The clerk waits. Ask the court to begin arguments.</p>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`slip ${m.role} ${m.agentId || ""}`}>
                <span className="who">
                  {m.role === "you"
                    ? "You (juror)"
                    : m.agentId === "prosecution"
                      ? lobby?.prosecution?.name || "Prosecution"
                      : m.agentId === "defense"
                        ? lobby?.defense?.name || "Defense"
                        : m.role}
                </span>
                <p>{m.text}</p>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>

          <aside className="exhibits-card">
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
        </div>

        {status === "questioning" && (
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              onAsk();
            }}
          >
            <p className="direct">Direct your question to</p>
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
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask from the jury box…"
            />
            <button className="primary" type="submit">
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
            <button className="secondary" onClick={() => onSend({ type: "cast_vote", vote: "not_guilty" })}>
              Not guilty
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
