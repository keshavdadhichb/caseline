# Caseline

**Query-driven AI agent that detects money-laundering patterns in transaction
data — and shows its reasoning.**

> Work in progress — hackathon build in flight. Hero GIF, results table and
> full docs land before the deadline.

Ask Caseline a question in plain English — *"Find structuring patterns in the
last 30 days"* — and it parses intent, builds a dynamic execution plan,
invokes only the tools the query needs (and shows you what it skipped), then
returns explained, risk-scored case files with a drafted SAR narrative.

## Stack

- `backend/` — Python + FastAPI + pandas + scikit-learn + networkx. All
  detection math is deterministic; the LLM (Anthropic API) is used only for
  query planning and SAR drafting.
- `frontend/` — React + Vite + Tailwind. One screen: query bar, results
  table, live execution trace.
- `data/` — normalized 200k-row sample of the IBM AML dataset (see
  [DATA.md](DATA.md)) + one documented synthetic smurfing ring.
- `evals/` — 12-query eval suite + naive-baseline comparison.

## Run it

```bash
make setup      # python venv + npm install
make data       # build the committed sample from data/raw (optional — sample is committed)
make backend    # http://localhost:8000
make frontend   # http://localhost:5173
make eval       # eval suite
```

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (planner + SAR
drafting; cached plans keep the demo working offline).

## Typologies

<!-- TEAMMATE: four typology definitions + graph patterns -->

## Dataset & citations

See [DATA.md](DATA.md). <!-- TEAMMATE: citations + licenses -->

## Disclosure

- **AI tooling:** built with Claude Code (Anthropic).
  - **Anthropic API** (`claude-sonnet-4-6`) — query planning and SAR
    narrative drafting. Required.
  - **Google Gemini API** — presentation layer only: the "Explain simply"
    panel, its illustration (`gemini-3.1-flash-image`), and speech in/out.
    Optional; the app runs fully without a key and every such feature falls
    back to a deterministic equivalent. Gemini never plans a query, never
    drafts a SAR, and never sees or influences a risk score.
  - **All detection logic is deterministic Python.** No LLM computes,
    adjusts or ranks a risk score.
- **Synthetic data:** one injected, clearly-marked smurfing ring — see
  [DATA.md](DATA.md).

## Team

<!-- TEAMMATE: contribution split -->
