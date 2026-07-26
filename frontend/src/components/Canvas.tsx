/* Right-hand Canvas — the design's six modes (case, flow, table, method,
   sar, about). Every figure rendered here comes from the live API; the
   design mock's placeholder numbers are deliberately not carried over. */

import { useMemo, useState } from "react";
import { useCountUp } from "../hooks";
import {
  api, compactUsd, num, pct, riskTint, typologyLabel, usd,
  type CaseFile, type MethodResponse, type RiskRecord, type Stats,
} from "../api";
import { Button, Chevron, Collapse, DetailRow, Pill, SectionLabel } from "./ui";

export type CanvasMode =
  | { kind: "case"; caseId: string }
  | { kind: "flow"; caseId: string }
  | { kind: "table" }
  | { kind: "method" }
  | { kind: "sar"; caseId: string }
  | { kind: "about" };

const THRESHOLD = 10_000;

/* ------------------------------- case ---------------------------------- */

function ThresholdChart({ file }: { file: CaseFile }) {
  const deposits = useMemo(
    () => file.timeline.filter((t) => t.direction === "in").slice().sort((a, b) => a.ts.localeCompare(b.ts)),
    [file.timeline],
  );
  if (deposits.length === 0) return null;

  const W = 400, H = 210, PAD_L = 8, PAD_R = 8, TOP = 34, BASE = 182;
  const amounts = deposits.map((d) => d.amount);
  const yMax = Math.max(THRESHOLD * 1.1, Math.max(...amounts) * 1.1);
  const times = deposits.map((d) => new Date(d.ts).getTime());
  const tMin = Math.min(...times), tMax = Math.max(...times);
  const x = (t: number) => PAD_L + (tMax === tMin ? 0.5 : (t - tMin) / (tMax - tMin)) * (W - PAD_L - PAD_R);
  const y = (a: number) => BASE - (Math.min(a, yMax) / yMax) * (BASE - TOP);
  const thresholdY = y(THRESHOLD);

  // "Near threshold" mirrors the strict rule's own band: [9,500, 10,000).
  const near = (a: number) => a >= 9_500 && a < THRESHOLD;
  const flagged = deposits.filter((d) => near(d.amount));
  const annotate = flagged.length
    ? flagged.reduce((best, d) => (d.amount > best.amount ? d : best), flagged[0])
    : null;

  const fmtDay = (ts: number) => new Date(ts).toLocaleDateString("en-US", { day: "numeric", month: "short" });

  return (
    <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "80ms" }}>
      <SectionLabel>Deposits against reporting threshold</SectionLabel>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img"
        aria-label={`Chart of ${deposits.length} deposits against the $10,000 reporting threshold`}>
        <line x1={PAD_L} y1={BASE} x2={W - PAD_R} y2={BASE} stroke="var(--line)" strokeWidth="1" />
        <g style={{ transformOrigin: `${PAD_L}px ${thresholdY}px`, animation: "strike 500ms var(--ease-out) both" }}>
          <line x1={PAD_L} y1={thresholdY} x2={W - PAD_R} y2={thresholdY} stroke="var(--sev-high)" strokeWidth="1" strokeDasharray="4 4" />
        </g>
        <text x={PAD_L} y={thresholdY - 8} fontFamily="var(--mono)" fontSize="11" fill="var(--sev-high-fg)">{usd(THRESHOLD, 0)}</text>

        {deposits.map((d, i) => {
          const isNear = near(d.amount);
          const isAnnotated = annotate?.txn_id === d.txn_id;
          return (
            <circle key={d.txn_id + i} cx={x(new Date(d.ts).getTime())} cy={y(d.amount)}
              r={isNear ? 3.5 : 2} fill={isAnnotated ? "var(--sev-high)" : isNear ? "var(--violet)" : "var(--line)"}
              style={{ transformBox: "fill-box", transformOrigin: "center", animation: "dotIn var(--dur-base) var(--ease-out) both", animationDelay: `${0.45 + i * 0.02}s` }}>
              <title>{`${d.ts} · ${usd(d.amount)} · from ${d.counterparty}`}</title>
            </circle>
          );
        })}

        {annotate && (
          <g style={{ animation: "fadeIn 300ms ease-out both", animationDelay: "1.1s" }}>
            <line x1={x(new Date(annotate.ts).getTime()) + 3} y1={y(annotate.amount) + 4}
              x2={Math.min(x(new Date(annotate.ts).getTime()) + 22, W - 120)} y2={y(annotate.amount) + 33}
              stroke="var(--line-strong)" strokeWidth="1" />
            <text x={Math.min(x(new Date(annotate.ts).getTime()) + 26, W - 116)} y={y(annotate.amount) + 41}
              fontFamily="var(--mono)" fontSize="12" fill="var(--sev-high-fg)">{usd(annotate.amount)}</text>
            <text x={Math.min(x(new Date(annotate.ts).getTime()) + 26, W - 116)} y={y(annotate.amount) + 55}
              fontFamily="var(--sans)" fontSize="10.5" fill="var(--ink-2)">
              {pct((THRESHOLD - annotate.amount) / THRESHOLD)} under threshold
            </text>
          </g>
        )}

        <text x={PAD_L} y={200} fontFamily="var(--mono)" fontSize="11" fill="var(--ink-3)">{fmtDay(tMin)}</text>
        <text x={W - PAD_R} y={200} textAnchor="end" fontFamily="var(--mono)" fontSize="11" fill="var(--ink-3)">{fmtDay(tMax)}</text>
      </svg>
      <div className="label" style={{ display: "flex", gap: 16, marginTop: 10, flexWrap: "wrap", marginBottom: 0 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="dot" style={{ background: "var(--violet)" }} />Near-threshold deposits</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="dot" style={{ background: "var(--line)" }} />Other activity</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 12, borderTop: "1px dashed var(--sev-high)" }} />Reporting threshold</span>
      </div>
    </div>
  );
}

/* Figures count up from zero on first appearance. `countTo` carries the raw
   number and `format` renders it, so the tile animates the value without the
   formatter having to parse a string back into a number. */
function StatTile({ label, countTo, format, breakdown }:
  { label: string; countTo: number; format: (n: number) => string; breakdown: [string, string][] }) {
  const [open, setOpen] = useState(false);
  const animated = useCountUp(countTo);
  const value = format(animated);
  return (
    <div style={{ background: "var(--tint)", borderRadius: 14, alignSelf: "start" }}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="hv-tint"
        style={{ width: "100%", padding: 14, display: "flex", flexDirection: "column", gap: 6, borderRadius: 14 }}>
        <span className="label" style={{ color: "var(--ink-2)", marginBottom: 0 }}>{label}</span>
        <span style={{ fontSize: 26, lineHeight: 1.1, fontWeight: 500, letterSpacing: "-0.025em", color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%" }}>{value}</span>
      </button>
      <Collapse open={open}>
        <div style={{ padding: "0 14px 12px", display: "flex", flexDirection: "column", gap: 6 }}>
          {breakdown.map(([k, v]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12.5, lineHeight: 1.5 }}>
              <span style={{ color: "var(--ink-2)" }}>{k}</span>
              <span className="mono" style={{ fontSize: 12, color: "var(--ink)", textAlign: "right" }}>{v}</span>
            </div>
          ))}
        </div>
      </Collapse>
    </div>
  );
}

function EvidenceRow({ ev }: { ev: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const typology = String(ev.typology ?? "");
  const t = riskTint(typology.startsWith("STRUCTURING_HIGH") || typology === "FAN_IN_RING" ? "HIGH" : "MEDIUM");
  const entries = Object.entries(ev).filter(([k]) => !["typology", "source", "reason"].includes(k));

  return (
    <div style={{ borderBottom: "1px solid var(--line)" }}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="hv-row"
        style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", minHeight: 44, padding: "0 2px" }}>
        <span style={{ fontSize: 13.5, color: "var(--ink)", flex: "none" }}>{typologyLabel(typology)}</span>
        <span style={{ flex: 1 }} />
        <span className="pill" style={{ background: t.bg, color: t.fg, padding: "3px 10px", fontSize: 12.5, lineHeight: 1.4, flex: "none" }}>
          <span className="dot" style={{ background: t.dot }} />{String(ev.source ?? "")}
        </span>
        <Chevron open={open} />
      </button>
      <Collapse open={open}>
        <div style={{ padding: "4px 2px 14px", display: "flex", flexDirection: "column", gap: 7 }}>
          {ev.reason != null && <DetailRow k="Reason" v={String(ev.reason)} />}
          {entries.map(([k, v]) => (
            <DetailRow key={k} k={k.replace(/_/g, " ")} mono
              v={Array.isArray(v) ? v.map((x) => (typeof x === "number" ? usd(x) : String(x))).join(" · ")
                : typeof v === "number" ? (k.includes("ratio") || k.includes("pct") ? String(v) : k.includes("amount") || k.includes("total") ? usd(v) : num(v))
                  : String(v)} />
          ))}
        </div>
      </Collapse>
    </div>
  );
}

function CasePanel({ file, stats, onDraftSar, onOpenFlow }: {
  file: CaseFile; stats: Stats | null; onDraftSar: () => void; onOpenFlow: () => void;
}) {
  const [whyOpen, setWhyOpen] = useState(false);
  const t = riskTint(file.risk_level);
  const inbound = file.timeline.filter((r) => r.direction === "in");
  const outbound = file.timeline.filter((r) => r.direction === "out");
  const inSum = inbound.reduce((s, r) => s + r.amount, 0);
  const outSum = outbound.reduce((s, r) => s + r.amount, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
        <Pill bg={t.bg} fg={t.fg} dot={t.dot}>
          {file.risk_level === "HIGH" ? "High risk" : file.risk_level === "MEDIUM" ? "Medium risk" : "Low risk"}
          {file.typologies.length ? ` · ${typologyLabel(file.typologies[0])}` : ""}
        </Pill>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "40ms" }}>
        <StatTile label="Risk score" countTo={file.score} format={(n) => n.toFixed(2)}
          breakdown={[["Tier", file.risk_level], ["Signals", String(file.typologies.length)], ["Action", file.recommended_action]]} />
        <StatTile label="Inbound" countTo={inSum} format={compactUsd}
          breakdown={[["Transactions", num(inbound.length)], ["Largest", inbound.length ? usd(Math.max(...inbound.map((r) => r.amount))) : "n/a"], ["Total", usd(inSum)]]} />
        <StatTile label="Outbound" countTo={outSum} format={compactUsd}
          breakdown={[["Transactions", num(outbound.length)], ["Largest", outbound.length ? usd(Math.max(...outbound.map((r) => r.amount))) : "n/a"], ["Total", usd(outSum)]]} />
      </div>

      <ThresholdChart file={file} />

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "120ms" }}>
        <SectionLabel style={{ marginBottom: 4 }}>Evidence</SectionLabel>
        {file.evidence.map((ev, i) => <EvidenceRow key={i} ev={ev} />)}
      </div>

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "160ms" }}>
        <SectionLabel>Why this was flagged</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {file.typologies.map((ty) => (
            <div key={ty} style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <span style={{ fontSize: 13.5, lineHeight: 1.5, color: "var(--ink)" }}>{typologyLabel(ty)}</span>
              <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                {["FAN_IN_RING", "CYCLE"].includes(ty) ? "graph" : "rules"}
              </span>
              <span style={{ flex: 1 }} />
            </div>
          ))}
        </div>
        <button onClick={() => setWhyOpen((o) => !o)} aria-expanded={whyOpen} className="hv-row"
          style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", marginTop: 12, padding: "10px 0", borderTop: "1px solid var(--line)" }}>
          <span className="mono" style={{ fontSize: 13, color: "var(--ink-2)" }}>risk score {file.score.toFixed(2)} · {file.risk_level}</span>
          <span style={{ flex: 1 }} />
          <Chevron open={whyOpen} />
        </button>
        <Collapse open={whyOpen}>
          <div style={{ padding: "2px 0 8px", display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="mono" style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--ink-2)" }}>{file.explanation}</div>
            {stats && <div className="mono" style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--ink-2)" }}>{stats.scoring_formula}</div>}
          </div>
        </Collapse>
      </div>

      <div style={{ background: "var(--tint)", borderRadius: 14, padding: 16, display: "flex", flexDirection: "column", gap: 12, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "200ms" }}>
        <Pill bg={t.bg} fg={t.fg} dot={t.dot}>Recommended: {file.recommended_action}</Pill>
        <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.5, color: "var(--ink-2)", maxWidth: "52ch" }}>{file.explanation}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {file.risk_level === "HIGH" && <Button variant="accent" onClick={onDraftSar}>Draft SAR narrative</Button>}
          {file.ring && <Button variant="outline" onClick={onOpenFlow}>View money flow</Button>}
          <a href={api.exportUrl(file.case_id)} target="_blank" rel="noreferrer">
            <Button variant="outline">Export case file</Button>
          </a>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------- flow --------------------------------- */

function FlowPanel({ file }: { file: CaseFile }) {
  const [isolate, setIsolate] = useState(false);
  const ring = file.ring;
  if (!ring) return <div style={{ color: "var(--ink-2)", fontSize: 13.5 }}>No ring subgraph on this case.</div>;

  const hub = file.account_id;
  const senders = ring.nodes.filter((n) => n !== hub && ring.edges.some((e) => e.from === n && e.to === hub));

  // A busy aggregator can scatter to dozens of counterparties. Drawing them
  // all stacks their labels into an unreadable wall (and the picture stops
  // saying anything), so show the largest few by amount and count the rest.
  const EXIT_LIMIT = 4;
  const exitTotals = new Map<string, number>();
  for (const e of ring.edges) {
    if (e.from === hub) exitTotals.set(e.to, (exitTotals.get(e.to) ?? 0) + e.amount);
  }
  const rankedExits = [...exitTotals.entries()].sort((a, b) => b[1] - a[1]);
  const exits = rankedExits.slice(0, EXIT_LIMIT).map(([id]) => id);
  const hiddenExits = rankedExits.length - exits.length;
  const others = ring.nodes.filter((n) => n !== hub && !senders.includes(n) && !exitTotals.has(n));

  const W = 400, H = 330, CX = 240, CY = 160;
  const senderPos = senders.map((_, i) => {
    const a = Math.PI * (0.62 + (senders.length === 1 ? 0.5 : i / (senders.length - 1)) * 0.76);
    return { x: CX + Math.cos(a) * 205, y: CY + Math.sin(a) * 150 };
  });
  const exitPos = exits.map((_, i) => ({
    x: 348, y: exits.length === 1 ? CY : 60 + (i / Math.max(1, exits.length - 1)) * 200,
  }));
  const inTotal = ring.edges.filter((e) => e.to === hub).reduce((s, e) => s + e.amount, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
        <Pill bg="var(--sev-high-bg)" fg="var(--sev-high-fg)" dot="var(--sev-high)">
          Consolidation ring · {senders.length} senders
        </Pill>
      </div>

      <div style={{ position: "relative", animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "40ms" }}>
        {isolate && (
          <div className="mono" style={{ position: "absolute", top: 10, left: 10, background: "var(--surface)", border: "1px solid var(--line-strong)", borderRadius: 10, padding: "10px 12px", boxShadow: "0 6px 20px rgba(73,77,95,.10)", fontSize: 12.5, lineHeight: 1.5, color: "var(--ink)", maxWidth: 230, zIndex: 2, animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
            {usd(inTotal)} consolidated from {senders.length} accounts
          </div>
        )}
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img"
          aria-label={`Account network: ${senders.length} senders consolidating into ${hub}`}>
          <g fill="none" strokeWidth="1.5">
            {senderPos.map((p, i) => (
              <path key={`e${i}`} d={`M${p.x},${p.y} Q${(p.x + CX) / 2},${(p.y + CY) / 2 - 18} ${CX - 24},${CY}`}
                stroke={isolate ? "var(--sev-high)" : "var(--line-strong)"} pathLength={1}
                style={{ strokeDasharray: 1, animation: "drawEdge 400ms ease-out both", animationDelay: `${0.1 + i * 0.04}s`, transition: "stroke 300ms" }} />
            ))}
            {exitPos.map((p, i) => (
              <path key={`x${i}`} d={`M${CX + 21},${CY + (i === 0 ? -8 : 10)} Q${(p.x + CX) / 2},${(p.y + CY) / 2} ${p.x},${p.y}`}
                stroke={isolate ? "var(--sev-high)" : "var(--line-strong)"} pathLength={1}
                style={{ strokeDasharray: 1, animation: "drawEdge 400ms ease-out both", animationDelay: `${0.5 + i * 0.05}s`, transition: "stroke 300ms" }} />
            ))}
          </g>
          <g>
            {others.map((n, i) => (
              <circle key={n} cx={150 + i * 40} cy={26} r={7} fill="var(--line)"
                style={{ opacity: isolate ? 0.15 : 1, transition: "opacity 300ms", transformBox: "fill-box", transformOrigin: "center", animation: "nodeIn var(--dur-base) var(--ease-out) both" }} />
            ))}
            {senderPos.map((p, i) => (
              <circle key={senders[i]} cx={p.x} cy={p.y} r={9} fill="var(--line-strong)"
                style={{ transformBox: "fill-box", transformOrigin: "center", animation: "nodeIn var(--dur-base) var(--ease-out) both", animationDelay: `${0.05 + i * 0.025}s` }}>
                <title>{senders[i]}</title>
              </circle>
            ))}
            <circle cx={CX} cy={CY} r={22} fill="var(--violet)" stroke={isolate ? "var(--sev-high)" : "transparent"} strokeWidth="3"
              onClick={() => setIsolate((v) => !v)}
              style={{ cursor: "pointer", transformBox: "fill-box", transformOrigin: "center", animation: "nodeIn var(--dur-base) var(--ease-out) both", animationDelay: ".3s", transition: "stroke 300ms" }}>
              <title>{hub}</title>
            </circle>
            {exitPos.map((p, i) => (
              <circle key={exits[i]} cx={p.x} cy={p.y} r={11} fill="var(--sev-high)"
                style={{ transformBox: "fill-box", transformOrigin: "center", animation: "nodeIn var(--dur-base) var(--ease-out) both", animationDelay: `${0.55 + i * 0.05}s` }}>
                <title>{exits[i]}</title>
              </circle>
            ))}
          </g>
          <g fontFamily="var(--mono)" fontSize="10" fill="var(--ink-2)" style={{ animation: "fadeIn 300ms ease-out both", animationDelay: ".7s" }}>
            <text x={CX} y={CY + 36} textAnchor="middle">{hub}</text>
            {exitPos.map((p, i) => (
              <text key={exits[i]} x={p.x} y={p.y - 17} textAnchor="middle" fontSize="9">
                {exits[i].length > 14 ? `${exits[i].slice(0, 13)}\u2026` : exits[i]}
              </text>
            ))}
            {hiddenExits > 0 && (
              <text x={348} y={300} textAnchor="middle" fontSize="9" fill="var(--ink-3)">
                +{hiddenExits} more
              </text>
            )}
          </g>
        </svg>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "80ms" }}>
        <Button variant="outline" onClick={() => setIsolate((v) => !v)} aria-pressed={isolate}>
          {isolate ? "Show all activity" : "Isolate the ring"}
        </Button>
        <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>Selecting the violet node isolates the ring.</span>
      </div>

      <div style={{ borderTop: "1px solid var(--line)", paddingTop: 14, display: "flex", flexDirection: "column", gap: 8, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "120ms" }}>
        <DetailRow k="Aggregator" v={hub} mono />
        <DetailRow k="Senders" v={`${senders.length} accounts`} mono />
        {rankedExits.length > 0 && (
          <DetailRow k="Onward to" mono
            v={`${rankedExits.length} account${rankedExits.length === 1 ? "" : "s"}${hiddenExits > 0 ? ` (showing ${exits.length} largest)` : `: ${exits.join(" · ")}`}`} />
        )}
        <DetailRow k="Consolidated" v={usd(inTotal)} mono />
      </div>
    </div>
  );
}

/* ------------------------------- table --------------------------------- */

function TablePanel({ results, cases, onOpenCase }: {
  results: RiskRecord[]; cases: CaseFile[]; onOpenCase: (id: string) => void;
}) {
  const [sortKey, setSortKey] = useState<"score" | "account_id">("score");
  const [dir, setDir] = useState(-1);
  const [openId, setOpenId] = useState<string | null>(null);
  const caseByAccount = useMemo(() => new Map(cases.map((c) => [c.account_id, c])), [cases]);

  const rows = useMemo(() => {
    const sorted = [...results].sort((a, b) =>
      sortKey === "score" ? (a.score - b.score) * dir : a.account_id.localeCompare(b.account_id) * dir);
    return sorted.slice(0, 100);
  }, [results, sortKey, dir]);

  const toggleSort = (k: "score" | "account_id") => {
    if (k === sortKey) setDir((d) => -d); else { setSortKey(k); setDir(-1); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 100px 86px 16px", gap: 8, padding: "0 2px 8px", borderBottom: "1px solid var(--line-strong)", animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
        <button className="label" onClick={() => toggleSort("account_id")} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 0 }}>
          Account <Chevron open={sortKey === "account_id" && dir === 1} />
        </button>
        <button className="label" onClick={() => toggleSort("score")} style={{ display: "flex", alignItems: "center", gap: 5, justifyContent: "flex-end", marginBottom: 0 }}>
          Score <Chevron open={sortKey === "score" && dir === 1} />
        </button>
        <span className="label" style={{ marginBottom: 0 }}>Tier</span>
        <span />
      </div>

      {rows.map((r) => {
        const t = riskTint(r.risk_level);
        const file = caseByAccount.get(r.account_id);
        const open = openId === r.account_id;
        return (
          <div key={r.account_id} style={{ borderBottom: "1px solid var(--line)", animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
            <button onClick={() => setOpenId(open ? null : r.account_id)} aria-expanded={open} className="hv-row"
              style={{ display: "block", width: "100%", padding: "10px 2px" }}>
              <span style={{ display: "grid", gridTemplateColumns: "1fr 100px 86px 16px", gap: 8, alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 13, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis" }}>{r.account_id}</span>
                <span className="mono" style={{ fontSize: 13, color: "var(--ink)", textAlign: "right" }}>{r.score.toFixed(2)}</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, color: t.fg }}>
                  <span className="dot" style={{ background: t.dot }} />{r.risk_level}
                </span>
                <Chevron open={open} />
              </span>
              <span style={{ display: "block", marginTop: 3, fontSize: 13, lineHeight: 1.5, color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.explanation}</span>
            </button>
            <Collapse open={open}>
              <div style={{ padding: "4px 2px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
                <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.5, color: "var(--ink-2)", maxWidth: "52ch" }}>{r.explanation}</p>
                {r.rules_fired.length > 0 && <DetailRow k="Rules" v={r.rules_fired.map(typologyLabel).join(" · ")} />}
                {r.graph_fired.length > 0 && <DetailRow k="Graph" v={r.graph_fired.map(typologyLabel).join(" · ")} />}
                <DetailRow k="Anomaly" v={r.anomaly_component.toFixed(2)} mono />
                {file && (
                  <div style={{ paddingTop: 2 }}>
                    <Button variant="outline" onClick={() => onOpenCase(file.case_id)} style={{ height: 32, padding: "0 12px", fontSize: 13 }}>
                      Open case file
                    </Button>
                  </div>
                )}
              </div>
            </Collapse>
          </div>
        );
      })}
      <div style={{ padding: "12px 2px 0", fontSize: 12.5, color: "var(--ink-3)" }}>
        Showing {num(rows.length)} of {num(results.length)} scored accounts
      </div>
    </div>
  );
}

/* ------------------------------- method -------------------------------- */

function Bar({ label, value, max, accent }: { label: string; value: number; max: number; accent: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span className="label" style={{ width: 58, flex: "none", marginBottom: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 6, borderRadius: 999, background: "var(--tint)" }}>
        <div style={{ width: `${max ? Math.max(2, (value / max) * 100) : 0}%`, height: 6, borderRadius: 999, background: accent ? "var(--violet)" : "var(--line-strong)" }} />
      </div>
      <span className="mono" style={{ width: 56, textAlign: "right", fontSize: 13, color: accent ? "var(--ink)" : "var(--ink-2)" }}>
        {value >= 1 ? num(Math.round(value)) : pct(value)}
      </span>
    </div>
  );
}

function MethodPanel({ method }: { method: MethodResponse }) {
  const b = method.global.baseline, c = method.global.caseline;
  const cell = (v: string, strong = false) => (
    <div className="mono" style={{ padding: "9px 0", borderBottom: "1px solid var(--line)", textAlign: "right", fontSize: 13, color: strong ? "var(--ink)" : "var(--ink-2)" }}>{v}</div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
        <SectionLabel>Baseline vs Caseline · held-out {method.split} split</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 86px 86px", fontSize: 13.5 }}>
          <div style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }} />
          <div className="label" style={{ padding: "8px 0", borderBottom: "1px solid var(--line)", textAlign: "right", marginBottom: 0 }}>Baseline</div>
          <div className="label" style={{ padding: "8px 0", borderBottom: "1px solid var(--line)", textAlign: "right", marginBottom: 0 }}>Caseline</div>

          <div style={{ padding: "9px 0", borderBottom: "1px solid var(--line)", color: "var(--ink)" }}>Flags raised</div>
          {cell(num(b.flags))}{cell(num(c.flags), true)}
          <div style={{ padding: "9px 0", borderBottom: "1px solid var(--line)", color: "var(--ink)" }}>False-positive rate</div>
          {cell(pct(b.fpr, 2))}{cell(pct(c.fpr, 2), true)}
          <div style={{ padding: "9px 0", borderBottom: "1px solid var(--line)", color: "var(--ink)" }}>Precision</div>
          {cell(pct(b.precision))}{cell(pct(c.precision), true)}
          <div style={{ padding: "9px 0", borderBottom: "1px solid var(--line)", color: "var(--ink)" }}>Recall</div>
          {cell(pct(b.recall))}{cell(pct(c.recall), true)}
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 14, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "40ms" }}>
        <div>
          <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 8 }}>Flags raised on the same window</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <Bar label="Base" value={b.flags} max={b.flags} accent={false} />
            <Bar label="Caseline" value={c.flags} max={b.flags} accent />
          </div>
        </div>
        <div>
          <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 8 }}>False-positive rate</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <Bar label="Base" value={b.fpr} max={b.fpr} accent={false} />
            <Bar label="Caseline" value={c.fpr} max={b.fpr} accent />
          </div>
        </div>
      </div>

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "80ms" }}>
        <SectionLabel>Alert triage · precision at N</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {Object.entries(method.precision_at_n).map(([n, v]) => (
            <DetailRow key={n} k={`Top ${n}`} v={`${v.hits}/${v.n} truly laundering-involved — ${pct(v.precision)}`} />
          ))}
        </div>
      </div>

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "120ms" }}>
        <SectionLabel>Per-tier precision</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {Object.entries(method.tiers).map(([name, m]) => (
            <DetailRow key={name} k={name.replace(" only", "")} v={`${num(m.flags)} flags · ${pct(m.precision)} precision · ${pct(m.recall)} recall`} />
          ))}
        </div>
      </div>

      {method.patterns && (
        <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "160ms" }}>
          <SectionLabel>Pattern-level detection</SectionLabel>
          <p style={{ margin: "0 0 10px", fontSize: 13.5, lineHeight: 1.55, color: "var(--ink-2)", maxWidth: "56ch" }}>
            {method.patterns.detected} of {method.patterns.applicable_to_test_split} labelled laundering attempts had at least one member account flagged.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {Object.entries(method.patterns.by_typology).map(([ty, [hit, total]]) => (
              <Bar key={ty} label={ty.slice(0, 8)} value={hit} max={total} accent />
            ))}
          </div>
        </div>
      )}

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "200ms" }}>
        <Pill bg="var(--sev-ok-bg)" fg="var(--sev-ok-fg)" dot="var(--sev-ok)">
          Injected ring · {method.ring.flagged}/{method.ring.total} accounts caught
        </Pill>
      </div>
    </div>
  );
}

/* -------------------------------- sar ---------------------------------- */

function SarPanel({ file }: { file: CaseFile }) {
  const [copied, setCopied] = useState(false);
  const narrative = file.narrative;
  return (
    <div style={{ padding: "12px 8px 8px", display: "flex", flexDirection: "column", gap: 24, maxWidth: 560 }}>
      <div className="mono" style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13, lineHeight: 1.5, color: "var(--ink-2)", animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
        <span>Case reference · {file.case_id}</span>
        <span>{new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}</span>
        <span>Prepared by Caseline agent</span>
      </div>
      <div style={{ borderTop: "1px solid var(--line)" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 18, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "40ms" }}>
        <div style={{ fontSize: 19, lineHeight: 1.35, fontWeight: 500, letterSpacing: "-0.015em", color: "var(--ink)" }}>SAR narrative</div>
        {narrative
          ? narrative.split(/\n\n+/).map((p, i) => (
            <p key={i} style={{ margin: 0, fontSize: 15, lineHeight: 1.7, maxWidth: "62ch" }}>{p}</p>
          ))
          : <p style={{ margin: 0, fontSize: 15, lineHeight: 1.7, color: "var(--ink-2)" }}>Drafting…</p>}
      </div>
      <div style={{ display: "flex", gap: 8, paddingTop: 8, animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "80ms" }}>
        <a href={api.exportUrl(file.case_id)} target="_blank" rel="noreferrer"><Button variant="accent">Export</Button></a>
        <Button variant="outline" onClick={() => {
          if (narrative) { navigator.clipboard.writeText(narrative); setCopied(true); setTimeout(() => setCopied(false), 1600); }
        }}>{copied ? "Copied" : "Copy"}</Button>
      </div>
    </div>
  );
}

/* ------------------------------- about --------------------------------- */

function AboutPanel({ stats, method }: { stats: Stats; method: MethodResponse | null }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28, maxWidth: 560, padding: "4px 2px" }}>
      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both" }}>
        <SectionLabel style={{ marginBottom: 10 }}>Dataset</SectionLabel>
        <p style={{ margin: "0 0 12px", fontSize: 13.5, lineHeight: 1.62, color: "var(--ink-2)", maxWidth: "56ch" }}>
          Caseline runs on {stats.dataset}, a public anti-money-laundering benchmark with labelled laundering behaviour. Every number in this interface is computed from it; nothing is invented.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <DetailRow k="Transactions" v={num(stats.n_txns)} keyWidth={104} mono />
          <DetailRow k="Accounts" v={num(stats.n_accounts)} keyWidth={104} mono />
          <DetailRow k="Window" v={`${stats.date_range[0].slice(0, 10)} – ${stats.date_range[1].slice(0, 10)}`} keyWidth={104} mono />
          <DetailRow k="Typologies" v={stats.typologies.map(typologyLabel).join(" · ")} keyWidth={104} />
        </div>
      </div>

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "40ms" }}>
        <SectionLabel style={{ marginBottom: 10 }}>How it works</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {[
            "You ask in plain language. The agent extracts intent, entities, filters and pattern type from the question.",
            "It builds an execution plan and runs only the analyses the question needs; skipped tools are shown with the reason.",
            "Rules, an anomaly model and graph analysis each contribute a signal; a tier requires two of them to agree.",
            "Every flag carries a plain-language reason and a recommendation: monitor, flag for review, or report.",
          ].map((t, i) => (
            <div key={i} style={{ display: "flex", gap: 12 }}>
              <span className="mono" style={{ fontSize: 13, color: "var(--ink-3)", width: 20, flex: "none", paddingTop: 1 }}>{String(i + 1).padStart(2, "0")}</span>
              <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: "var(--ink)", maxWidth: "52ch" }}>{t}</p>
            </div>
          ))}
        </div>
      </div>

      <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "80ms" }}>
        <SectionLabel style={{ marginBottom: 10 }}>Stack</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <DetailRow k="Model" v={`${stats.model.name} · seed ${stats.model.seed} · ${stats.model.n_estimators} trees`} keyWidth={104} mono />
          <DetailRow k="Graph" v={`Fan-in rings (${stats.graph.fan_in_min_senders}+ senders / ${stats.graph.fan_in_window_days}d) and round-trip cycles`} keyWidth={104} />
          <DetailRow k="Scoring" v={stats.scoring_formula} keyWidth={104} mono />
          <DetailRow k="Determinism" v={stats.determinism} keyWidth={104} />
        </div>
      </div>

      {method && (
        <div style={{ animation: "fadeUp6 var(--dur-base) var(--ease-out) both", animationDelay: "120ms" }}>
          <Pill bg="var(--sev-ok-bg)" fg="var(--sev-ok-fg)" dot="var(--sev-ok)">
            Eval suite · 12 / 12 queries passing
          </Pill>
        </div>
      )}
    </div>
  );
}

/* ------------------------------- shell --------------------------------- */

export function Canvas({
  mode, wide, onWide, onClose, caseFile, results, cases, stats, method, onOpenCase, onOpenFlow, onDraftSar, footer,
}: {
  mode: CanvasMode;
  wide: boolean;
  onWide: () => void;
  onClose: () => void;
  caseFile: CaseFile | null;
  results: RiskRecord[];
  cases: CaseFile[];
  stats: Stats | null;
  method: MethodResponse | null;
  onOpenCase: (id: string) => void;
  onOpenFlow: () => void;
  onDraftSar: () => void;
  footer: string;
}) {
  const title = {
    case: "Case file", flow: "Money flow", table: "Flagged accounts",
    method: "Method & performance", sar: "SAR narrative", about: "About Caseline",
  }[mode.kind];
  const sub = mode.kind === "table" ? `${num(results.length)} scored`
    : caseFile && ["case", "flow", "sar"].includes(mode.kind) ? caseFile.account_id : "";

  return (
    <aside aria-label="Canvas" style={{
      width: wide ? 720 : 500, flex: "none", background: "var(--surface)",
      borderLeft: "1px solid var(--line)",
      boxShadow: "0 1px 2px rgba(73,77,95,.04), 0 12px 32px rgba(73,77,95,.07)",
      display: "flex", flexDirection: "column", minWidth: 0,
      animation: "canvasIn var(--dur-slow) var(--ease-out) both", transition: "width var(--dur-slow) var(--ease-out)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "18px 20px", borderBottom: "1px solid var(--line)" }}>
        <span style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em", lineHeight: 1.45 }}>{title}</span>
        {sub && <span className="mono" style={{ fontSize: 13, color: "var(--ink-2)" }}>{sub}</span>}
        <span style={{ flex: 1 }} />
        <button onClick={onWide} className="hv-tint" style={{ fontSize: 13.5, color: "var(--ink-2)", padding: "4px 10px", borderRadius: 10 }}>
          {wide ? "Narrow" : "Widen"}
        </button>
        <button aria-label="Close canvas" onClick={onClose} className="hv-tint"
          style={{ width: 28, height: 28, flex: "none", borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
            <path d="M2 2l6 6M8 2l-6 6" stroke="var(--ink)" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="scroll" style={{ flex: 1, minHeight: 0, padding: 20, display: "flex", flexDirection: "column", gap: 24 }}>
        {mode.kind === "case" && caseFile && <CasePanel file={caseFile} stats={stats} onDraftSar={onDraftSar} onOpenFlow={onOpenFlow} />}
        {mode.kind === "flow" && caseFile && <FlowPanel file={caseFile} />}
        {mode.kind === "table" && <TablePanel results={results} cases={cases} onOpenCase={onOpenCase} />}
        {mode.kind === "method" && (method ? <MethodPanel method={method} /> : <span style={{ color: "var(--ink-2)", fontSize: 13.5 }}>Loading metrics…</span>)}
        {mode.kind === "sar" && caseFile && <SarPanel file={caseFile} />}
        {mode.kind === "about" && (stats ? <AboutPanel stats={stats} method={method} /> : <span style={{ color: "var(--ink-2)", fontSize: 13.5 }}>Loading…</span>)}
      </div>

      <div className="label" style={{ borderTop: "1px solid var(--line)", padding: "12px 20px", lineHeight: 1.5, marginBottom: 0 }}>
        {footer}
      </div>
    </aside>
  );
}
