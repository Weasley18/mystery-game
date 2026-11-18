export default function QuickOptions({
  open,
  onClose,
  screen,
  status,
  expectedPlayers,
  setExpectedPlayers,
  joinGameId,
  setJoinGameId,
  questionsAsked,
  maxQuestions,
  busy,
  onOpenCourt,
  onJoin,
  onLeave,
  onSignOut,
  onNextArgument,
  onCallVote,
  scenarioLocked,
}) {
  if (!open) return null;

  const inCourt = screen === "play";
  const atHome = screen === "home";

  return (
    <>
      <button type="button" className="sidebar-backdrop" aria-label="Close menu" onClick={onClose} />
      <aside className="quick-options" aria-label="Quick options">
        <div className="quick-head">
          <span className="lbl">Quick options</span>
          <button type="button" className="ghost menu-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {inCourt && (
          <div className="quick-block">
            <span className="lbl">Session</span>
            <p className="quick-phase">{String(status || "lobby").replace(/_/g, " ")}</p>
            <p className="quick-qcount">
              Questions {questionsAsked}/{maxQuestions}
            </p>
            {(status === "lobby" || status === "debating") && (
              <button type="button" className="primary" onClick={onNextArgument}>
                Hear next argument
              </button>
            )}
            {status === "questioning" && (
              <button type="button" className="secondary" onClick={onCallVote}>
                Call the vote
              </button>
            )}
            <button type="button" className="ghost" onClick={onLeave}>
              Leave chamber
            </button>
          </div>
        )}

        {atHome && (
          <div className="quick-block">
            <span className="lbl">Open court</span>
            <label>
              Expected jurors
              <input
                type="number"
                min={1}
                value={expectedPlayers}
                onChange={(e) => setExpectedPlayers(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="primary"
              disabled={busy || scenarioLocked}
              onClick={onOpenCourt}
            >
              {busy ? "Convening…" : "Open court"}
            </button>
            <div className="or">or join</div>
            <label>
              Docket id
              <input
                value={joinGameId}
                onChange={(e) => setJoinGameId(e.target.value)}
                placeholder="abc123def0"
              />
            </label>
            <button
              type="button"
              className="secondary"
              disabled={busy || !joinGameId.trim()}
              onClick={onJoin}
            >
              Join gallery
            </button>
          </div>
        )}

        {screen === "lobby" && (
          <div className="quick-block">
            <button type="button" className="ghost" onClick={onLeave}>
              Leave chamber
            </button>
          </div>
        )}

        <div className="quick-block quick-foot">
          <button type="button" className="secondary" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </aside>
    </>
  );
}
