/* Small primitives repeated throughout the design. Each renders exactly the
   markup/measurements the design specifies, so screens compose from these
   instead of duplicating inline styles. */

import type { CSSProperties, ReactNode } from "react";

export function Chevron({ open, size = 8 }: { open: boolean; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 8 8" fill="none" aria-hidden="true"
      style={{ flex: "none", transform: `rotate(${open ? 90 : 0}deg)`, transition: "transform var(--dur-fast) var(--ease-out)" }}>
      <path d="M2.5 1 6 4 2.5 7" stroke="var(--ink-3)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Grid-template-rows 0fr -> 1fr expansion (never max-height). Opening runs
 *  at --dur-base, closing at --dur-fast, and revealed children fade up 6px
 *  on a 40ms stagger. Both behaviours live in index.css so the timing is
 *  declared once. */
export function Collapse({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div className="collapse" data-open={open}>
      <div><div className="stagger">{children}</div></div>
    </div>
  );
}

export function Pill({ bg, fg, dot, children }: { bg: string; fg: string; dot: string; children: ReactNode }) {
  return (
    <span className="pill" style={{ background: bg, color: fg }}>
      <span className="dot" style={{ background: dot }} />
      {children}
    </span>
  );
}

/** Uppercase key + value row, the design's standard detail line. */
export function DetailRow({ k, v, keyWidth = 96, mono = false }:
  { k: string; v: ReactNode; keyWidth?: number; mono?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 12, fontSize: 13.5, lineHeight: 1.5 }}>
      <span className="label" style={{ width: keyWidth, flex: "none", paddingTop: 3 }}>{k}</span>
      <span style={{
        color: mono ? "var(--ink)" : "var(--ink-2)",
        fontFamily: mono ? "var(--mono)" : undefined,
        fontSize: mono ? 13 : undefined,
        maxWidth: "56ch", minWidth: 0, overflowWrap: "anywhere",
      }}>{v}</span>
    </div>
  );
}

export function SectionLabel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div className="label" style={{ marginBottom: 12, ...style }}>{children}</div>;
}

export function Button({ variant = "ghost", onClick, children, style, ...rest }: {
  variant?: "accent" | "ghost" | "outline";
  onClick?: () => void;
  children: ReactNode;
  style?: CSSProperties;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "style" | "onClick">) {
  const base: CSSProperties = {
    height: 38, padding: "0 16px", borderRadius: 10,
    fontSize: 13.5, fontWeight: 500, display: "inline-flex", alignItems: "center",
  };
  const variants: Record<string, CSSProperties> = {
    accent: { background: "var(--violet)", color: "var(--on-violet)" },
    outline: { border: "1px solid var(--line)", background: "transparent", color: "var(--ink)" },
    ghost: { color: "var(--ink-2)" },
  };
  return (
    <button
      className={variant === "accent" ? "hv-accent" : "hv-tint"}
      onClick={onClick}
      style={{ ...base, ...variants[variant], ...style }}
      {...rest}
    >
      {children}
    </button>
  );
}

export function SendIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" style={{ color: "var(--on-violet)" }}>
      <path d="M2.5 7h9M8.5 3.5 12 7l-3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Skeleton({ w, h = 13, style }: { w: number | string; h?: number; style?: CSSProperties }) {
  return (
    <span style={{
      display: "inline-block", width: w, height: h, borderRadius: 6,
      background: "var(--tint)", ...style,
    }} />
  );
}
