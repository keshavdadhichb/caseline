/* Left rail — investigations list, collapsible to an icon strip, exactly as
   the design's <nav data-screen-label="Sidebar"> specifies. */

import type { Investigation } from "../App";

export function Sidebar({
  open, onToggle, items, activeId, onSelect, onNew, onAbout, footer,
}: {
  open: boolean;
  onToggle: () => void;
  items: Investigation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onAbout: () => void;
  footer: string;
}) {
  const dotFor = (inv: Investigation) =>
    inv.topRisk === "HIGH" ? "var(--risk-high-dot)"
      : inv.topRisk === "MEDIUM" ? "var(--border-strong)"
        : inv.topRisk === "LOW" ? "var(--risk-ok-dot)"
          : "var(--accent)";

  return (
    <nav
      aria-label="Investigations"
      className="scroll"
      style={{
        width: open ? 260 : 56, flex: "none", background: "var(--side-bg)",
        borderRight: "1px solid var(--side-border)", display: "flex", flexDirection: "column",
        padding: "20px 12px 16px", overflowX: "hidden",
        transition: "width 300ms var(--ease)",
      }}
    >
      {!open && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
          <span className="dot" style={{ background: "var(--accent)", margin: "6px 0 10px" }} />
          <button aria-label="New investigation" onClick={onNew} className="hv-violet"
            style={{ width: 32, height: 32, flex: "none", borderRadius: 10, border: "1px solid var(--border)", background: "var(--surface)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
              <path d="M5 1v8M1 5h8" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <button aria-label="Expand sidebar" onClick={onToggle} className="hv-white"
            style={{ width: 32, height: 32, flex: "none", borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true">
              <path d="M2.5 1 6 4 2.5 7" stroke="var(--faint)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, paddingTop: 8 }}>
            {items.map((it) => (
              <button key={it.id} aria-label={it.title} onClick={onToggle} className="hv-white"
                style={{ width: 22, height: 22, flex: "none", borderRadius: 999, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <span className="dot" style={{ background: dotFor(it) }} />
              </button>
            ))}
          </div>
        </div>
      )}

      {open && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "0 10px 24px" }}>
            <span style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em", color: "var(--ink)", whiteSpace: "nowrap" }}>Caseline</span>
            <span className="dot" style={{ background: "var(--accent)", display: "inline-block", marginTop: 3 }} />
            <span style={{ flex: 1 }} />
            <button aria-label="Collapse sidebar" onClick={onToggle} className="hv-white"
              style={{ width: 24, height: 24, flex: "none", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <svg width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true" style={{ transform: "rotate(180deg)" }}>
                <path d="M2.5 1 6 4 2.5 7" stroke="var(--faint)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>

          <button onClick={onNew} className="hv-violet"
            style={{ display: "flex", alignItems: "center", gap: 8, height: 36, padding: "0 10px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--surface)", fontSize: 13.5, color: "var(--ink)", whiteSpace: "nowrap" }}>
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
              <path d="M5 1v8M1 5h8" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <span>New investigation</span>
          </button>

          <div className="label" style={{ padding: "24px 10px 8px" }}>Investigations</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {items.length === 0 && (
              <div style={{ padding: "5px 10px", fontSize: 13.5, lineHeight: 1.5, color: "var(--faint)" }}>
                Nothing yet — ask a question to begin.
              </div>
            )}
            {items.map((it) => {
              const active = it.id === activeId;
              return (
                <div key={it.id}>
                  <button onClick={() => onSelect(it.id)} aria-expanded={active}
                    style={{
                      display: "flex", alignItems: "center", gap: 8, width: "100%", height: 36,
                      padding: "0 10px", borderRadius: 10, fontSize: 13.5,
                      background: active ? "var(--surface)" : "transparent",
                      color: active ? "var(--ink)" : "var(--muted)",
                      fontWeight: active ? 500 : 400, transition: "background 120ms",
                    }}>
                    <span className="dot" style={{ background: dotFor(it) }} />
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.title}</span>
                  </button>
                  <div className="collapse" style={{ gridTemplateRows: active ? "1fr" : "0fr" }}>
                    <div>
                      <div style={{ padding: "2px 0 6px" }}>
                        {it.queries.map((q, i) => (
                          <div key={i} style={{ padding: "5px 10px 5px 24px", fontSize: 13.5, lineHeight: 1.5, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{q}</div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <button onClick={onAbout} className="label"
            style={{ marginTop: "auto", padding: "16px 10px 0", lineHeight: 1.6, whiteSpace: "nowrap", transition: "color 120ms" }}>
            {footer}
          </button>
        </>
      )}
    </nav>
  );
}
