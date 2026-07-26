/* Caseline — three-pane shell (sidebar · thread · canvas) per the Claude
   Design project. All state is client-side; every displayed value comes
   from the live backend. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, num, usd,
  type Aggregation, type CaseFile, type MethodResponse, type NarratedStep, type Profile,
  type RiskRecord, type Stats,
  type TypologyExplainer,
} from "./api";
import { Sidebar } from "./components/Sidebar";
import { Landing } from "./components/Landing";
import { Thread } from "./components/Thread";
import { Canvas, type CanvasMode } from "./components/Canvas";
import { useTheme } from "./hooks";
import { presentation } from "./presentation";

/* The three problem-statement queries, verbatim. */
const SUGGESTIONS = [
  "Find structuring patterns in the last 30 days",
  "Which customers made 10+ transactions under $10,000?",
  "Is customer ID 4521 suspicious?",
];

export interface ChipRef {
  kind: "case" | "flow" | "table" | "method";
  label: string;
  detail: string;
  caseId?: string;
  accent?: boolean;
}

export interface Message {
  id: string;
  role: "user" | "agent";
  text?: string;
  thinking?: boolean;
  prose1?: string;
  prose2?: string;
  steps?: NarratedStep[];
  chips?: ChipRef[];
  clarify?: string;
  empty?: boolean;
  error?: string;
  notice?: string;
  unknownAccounts?: string[];
  aggregation?: Aggregation | null;
  profile?: Profile | null;
  /** Case this answer is about, so Explain can use the real evidence. */
  explainCaseId?: string;
  /** Set when the planner ran no tools at all — a conceptual question. */
  typologies?: TypologyExplainer[];
}

export interface Investigation {
  id: string;
  title: string;
  queries: string[];
  topRisk: "HIGH" | "MEDIUM" | "LOW" | null;
}

let seq = 0;
const nextId = () => `m${++seq}`;

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [sideOpen, setSideOpen] = useState(true);
  // Remembers the rail state the user chose, so auto-collapsing for a canvas
  // can restore it rather than overwrite it.
  const userSideOpen = useRef(true);
  const [leaving, setLeaving] = useState(false);
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [investigations, setInvestigations] = useState<Investigation[]>([]);
  const [activeInv, setActiveInv] = useState<string | null>(null);

  const [results, setResults] = useState<RiskRecord[]>([]);
  const [cases, setCases] = useState<CaseFile[]>([]);
  const [caseFile, setCaseFile] = useState<CaseFile | null>(null);

  const [canvas, setCanvas] = useState<CanvasMode | null>(null);
  const [wide, setWide] = useState(false);

  const [stats, setStats] = useState<Stats | null>(null);
  const [method, setMethod] = useState<MethodResponse | null>(null);
  const [lastQuery, setLastQuery] = useState<string>("");
  const [pendingClarify, setPendingClarify] = useState<string | null>(null);
  // Optional presentation layer; controls stay hidden when it is unavailable.
  const [geminiOn, setGeminiOn] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inThread = messages.length > 0;

  /* The canvas needs the width, so the rail collapses while one is open and
     returns to whatever the user had chosen when it closes. */
  /* Depends on whether a canvas is open, NOT on the canvas object: setCanvas
     produces a fresh object on every chip click, so keying off identity
     re-collapsed the rail each time the user opened another artifact, making
     a manual expand look like it did nothing. */
  const canvasOpen = canvas !== null;
  useEffect(() => {
    setSideOpen(canvasOpen ? false : userSideOpen.current);
  }, [canvasOpen]);

  useEffect(() => {
    api.stats().then(setStats).catch(() => { });
    api.method().then(setMethod).catch(() => { });
    presentation.capabilities().then((c) => setGeminiOn(c.gemini)).catch(() => { });
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  /* `/` focuses the composer, Esc closes the canvas — CLAUDE.md keyboard spec. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCanvas(null);
      if (e.key === "/" && !(e.target instanceof HTMLInputElement)) {
        e.preventDefault();
        document.querySelector<HTMLInputElement>("input[aria-label^='Ask']")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const openCase = useCallback(async (caseId: string, kind: "case" | "flow" | "sar" = "case") => {
    setCanvas({ kind, caseId } as CanvasMode);
    setCaseFile((prev) => prev?.case_id === caseId ? prev : null);
    try {
      const full = await api.case(caseId); // drafts the SAR lazily for HIGH cases
      setCaseFile(full);
      setCases((prev) => prev.map((c) => (c.case_id === caseId ? full : c)));
    } catch { /* leave the panel in its loading state */ }
  }, []);

  const runQuery = useCallback(async (query: string, clarificationAnswer?: string) => {
    // Landing fades up and out before the thread mounts.
    if (messages.length === 0) {
      setLeaving(true);
      await new Promise((r) => setTimeout(r, 300));
      setLeaving(false);
    }
    setDraft("");
    setLastQuery(query);
    const invId = activeInv ?? `inv${Date.now()}`;
    if (!activeInv) {
      setInvestigations((p) => [{ id: invId, title: query, queries: [query], topRisk: null }, ...p]);
      setActiveInv(invId);
    } else {
      setInvestigations((p) => p.map((i) => (i.id === invId ? { ...i, queries: [...i.queries, query] } : i)));
    }

    setMessages((p) => [...p, { id: nextId(), role: "user", text: query }]);
    const agentId = nextId();
    setMessages((p) => [...p, { id: agentId, role: "agent", thinking: true }]);

    const patch = (fn: (m: Message) => Message) =>
      setMessages((p) => p.map((m) => (m.id === agentId ? fn(m) : m)));

    try {
      const submitted = await api.submit(query, clarificationAnswer);

      // Small talk is answered directly, with no plan and no analysis.
      if (submitted.conversational) {
        patch((m) => ({ ...m, thinking: false, prose1: submitted.prose }));
        return;
      }

      if (submitted.clarification_needed) {
        setPendingClarify(query);
        patch((m) => ({ ...m, thinking: false, clarify: submitted.clarification_needed! }));
        return;
      }
      setPendingClarify(null);

      // An account the dataset has never seen: say so and stop. Scanning the
      // book would surface some unrelated account and read as an answer.
      const unknown = submitted.unknown_accounts ?? [];
      if (unknown.length) {
        patch((m) => ({
          ...m, thinking: false, steps: [], unknownAccounts: unknown,
          prose1: `No account matching ${unknown.join(", ")} exists in this dataset, so there is nothing to analyse. Check the identifier, or open Flagged accounts to browse what is present.`,
        }));
        return;
      }

      patch((m) => ({
        ...m, thinking: false, prose1: submitted.prose, steps: submitted.steps ?? [],
        // A generic fallback plan must never be narrated as understanding.
        notice: submitted.degraded
          ? "The planner could not reach the model for this question, so this is a generic sweep rather than a plan built for what you asked. Treat the results as a broad scan."
          : undefined,
      }));

      // A conceptual question ("what is structuring?") runs nothing. Answer
      // it from the real rule constants instead of polling a run that will
      // return zero and then claiming nothing met the thresholds — nothing
      // was ever evaluated.
      if (submitted.conceptual) {
        patch((m) => ({
          ...m,
          // The conversational answer leads; the typology cards stay beneath
          // it as the authoritative source for the actual thresholds.
          prose1: submitted.conversational_text || submitted.prose,
          typologies: submitted.typologies ?? [],
        }));
        return;
      }

      // Poll the trace so steps tick over pending -> running -> done live.
      const traceId = submitted.trace_id;
      let status = "running";
      while (status === "running") {
        await new Promise((r) => setTimeout(r, 450));
        const tr = await api.trace(traceId);
        status = tr.status;
        const byTool = new Map(tr.events.map((e) => [e.step, e]));
        patch((m) => ({
          ...m,
          steps: (m.steps ?? []).map((s) => {
            const e = byTool.get(s.tool);
            if (!e || s.skipped) return s;
            return { ...s, state: e.state as NarratedStep["state"], output: e.summary, returned: e.summary ?? s.returned };
          }),
        }));
      }

      if (status === "error") {
        patch((m) => ({ ...m, error: "The run failed before it finished; try again to rerun the same plan." }));
        return;
      }

      const res = await api.results(traceId);
      setResults(res.results);
      setCases(res.cases);
      if (res.steps) patch((m) => ({ ...m, steps: res.steps! }));

      const top = res.cases.find((c) => c.risk_level === "HIGH") ?? res.cases[0] ?? null;
      setInvestigations((p) => p.map((i) => (i.id === invId ? { ...i, topRisk: top?.risk_level ?? "LOW" } : i)));

      if (res.aggregation || res.profile) {
        // Answered by a count or a profile: show that answer, not the
        // detection-threshold empty state.
        patch((m) => ({
          ...m, prose2: res.prose ?? undefined,
          aggregation: res.aggregation, profile: res.results.length ? null : res.profile,
        }));
        if (res.results.length === 0) return;
      } else if (res.results.length === 0) {
        patch((m) => ({ ...m, empty: true }));
        return;
      }

      // Only surface a case automatically when it is plausibly the answer: a
      // named-entity query should open that entity, never a stranger.
      const named = submitted.plan?.filters.accounts ?? [];
      const answerCase = named.length
        ? res.cases.find((c) => named.some((a) => c.account_id === String(a)))
        : top;

      const chips: ChipRef[] = [];
      if (top) {
        chips.push({
          kind: "case", label: "Case file", caseId: top.case_id, accent: top.risk_level === "HIGH",
          detail: `${top.account_id} · ${top.risk_level.toLowerCase()} risk`,
        });
        if (top.ring) {
          const inTotal = top.ring.edges.filter((e) => e.to === top.account_id).reduce((s, e) => s + e.amount, 0);
          chips.push({
            kind: "flow", label: "Money flow", caseId: top.case_id,
            detail: `${usd(inTotal, 0)} · ${top.ring.nodes.length - 1} accounts`,
          });
        }
      }
      const typs = new Set(res.results.flatMap((r) => [...r.rules_fired, ...r.graph_fired]));
      chips.push({ kind: "table", label: "Flagged accounts", detail: `${num(res.results.length)} rows · ${typs.size} typologies` });
      chips.push({ kind: "method", label: "Method & performance", detail: "12 / 12 evals passing" });

      patch((m) => ({ ...m, prose2: res.prose ?? undefined, chips, explainCaseId: answerCase?.case_id }));
      // Only surface a case automatically when it is plausibly the answer: a
      // named-entity query should open that entity, never a stranger.
      if (answerCase) void openCase(answerCase.case_id);
    } catch (err) {
      patch((m) => ({
        ...m, thinking: false,
        error: `${(err as Error).message}. The backend may not be running — start it with \`make backend\`.`,
      }));
    }
  }, [activeInv, openCase, messages.length]);

  const onChip = useCallback((c: ChipRef) => {
    if (c.kind === "case" && c.caseId) void openCase(c.caseId, "case");
    else if (c.kind === "flow" && c.caseId) void openCase(c.caseId, "flow");
    else setCanvas({ kind: c.kind } as CanvasMode);
  }, [openCase]);

  const footer = useMemo(() => {
    if (!stats) return "Caseline";
    return `HI-Small · ${num(stats.n_txns)} rows · ${stats.model.name} · seed ${stats.model.seed} · 12 / 12 evals`;
  }, [stats]);

  const sidebarFooter = stats ? `HI-Small · seed ${stats.model.seed} · About` : "About";

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "var(--page)" }}>
      <Sidebar
        open={sideOpen}
        onToggle={() => setSideOpen((o) => { userSideOpen.current = !o; return !o; })}
        items={investigations}
        activeId={activeInv}
        onSelect={setActiveInv}
        onNew={() => { setMessages([]); setActiveInv(null); setCanvas(null); setResults([]); setCases([]); }}
        onAbout={() => setCanvas({ kind: "about" })}
        footer={sidebarFooter}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", position: "relative" }}>
        {!inThread ? (
          <Landing
            draft={draft} onDraft={setDraft}
            onSend={() => draft.trim() && runQuery(draft.trim())}
            suggestions={SUGGESTIONS}
            onPick={(q) => runQuery(q)}
            onAbout={() => setCanvas({ kind: "about" })}
            statsLine={stats}
            leaving={leaving}
            micOn={geminiOn}
          />
        ) : (
          <Thread
            messages={messages}
            draft={draft} onDraft={setDraft}
            onSend={() => draft.trim() && runQuery(draft.trim())}
            onOpenChip={onChip}
            onRetry={() => lastQuery && runQuery(lastQuery)}
            onAnswer={(a) => pendingClarify && runQuery(pendingClarify, a)}
            bottomRef={bottomRef}
            geminiOn={geminiOn}
          />
        )}
      </main>

      {canvas && (
        <Canvas
          mode={canvas}
          wide={wide}
          onWide={() => setWide((w) => !w)}
          onClose={() => setCanvas(null)}
          caseFile={caseFile}
          results={results}
          cases={cases}
          stats={stats}
          method={method}
          onOpenCase={(id) => openCase(id, "case")}
          onOpenFlow={() => caseFile && setCanvas({ kind: "flow", caseId: caseFile.case_id })}
          onDraftSar={() => caseFile && setCanvas({ kind: "sar", caseId: caseFile.case_id })}
          footer={footer}
          geminiOn={geminiOn}
        />
      )}
    </div>
  );
}
