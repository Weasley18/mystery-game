const MANOR_PORTRAITS = {
  prosecution: "/court/prosecutor-vale.png",
  defense: "/court/defense-okonkwo.png",
  defendant: "/court/defendant-evelyn.png",
};

export default function Portrait({ className, role, name, scenarioId }) {
  const src = role === "judge"
    ? "/court/judge-sprite.png"
    : scenarioId === "manor_poison" ? MANOR_PORTRAITS[role] : null;
  const initials = name?.trim().split(/\s+/).slice(-2).map((word) => word[0]).join("");

  return (
    <div className={`portrait-frame ${className}`} aria-hidden="true">
      {src ? <img src={src} alt="" width="1024" height="1536" /> : (
        <span className="portrait-initials">{initials}</span>
      )}
    </div>
  );
}
