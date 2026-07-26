"""evals/smoke.py — end-to-end smoke test through the SAME path the browser
uses (the Vite proxy on :5173 by default, so it exercises the proxy config
too, not just the backend directly).

Checks every screen the UI can render has real data behind it:
  · panels that load on mount (/api/stats, /api/method)
  · each of the 3 problem-statement queries: plan -> trace -> results
  · the plan genuinely differs per query (the agentic claim)
  · a case file, its threshold-chart data, its ring subgraph
  · the SAR narrative and the export document

Run:  make verify           (against the dev proxy, :5173)
      make verify-backend   (against the API directly, :8000)
"""

from __future__ import annotations

import sys
import time

import httpx

CANONICAL = [
    "Find structuring patterns in the last 30 days",
    "Which customers made 10+ transactions under $10,000?",
    "Is customer ID 4521 suspicious?",
]

passed = 0
failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    global passed
    if ok:
        passed += 1
        print(f"  \033[32mPASS\033[0m {label}" + (f" — {detail}" if detail else ""))
    else:
        failed.append(label)
        print(f"  \033[31mFAIL\033[0m {label}" + (f" — {detail}" if detail else ""))
    return ok


def run(base: str) -> int:
    c = httpx.Client(timeout=120.0, base_url=base)
    print(f"\nCaseline end-to-end smoke — {base}\n" + "=" * 60)

    print("\n[1] Panels that load on mount")
    try:
        s = c.get("/api/health").json()
        check("GET /api/health", s.get("status") == "ok")
    except Exception as exc:
        check("GET /api/health", False, f"{exc} — is the backend running? (`make backend`)")
        return 1

    st = c.get("/api/stats").json()
    check("GET /api/stats", st["n_txns"] > 0, f"{st['n_txns']:,} txns · {st['n_accounts']:,} accounts")
    check("stats carries the real scoring formula", "0.45" in st["scoring_formula"] and "0.35" in st["scoring_formula"])
    m = c.get("/api/method").json()
    b, cl = m["global"]["baseline"], m["global"]["caseline"]
    check("GET /api/method", b["flags"] > 0 and cl["flags"] > 0,
          f"baseline {b['flags']:,} flags / {b['fpr']:.2%} FPR vs Caseline {cl['flags']:,} / {cl['fpr']:.2%}")
    check("Caseline beats baseline on false positives", cl["fpr"] < b["fpr"])
    check("Precision@50 present", m["precision_at_n"]["50"]["precision"] > 0,
          f"{m['precision_at_n']['50']['precision']:.0%}")

    plans: dict[str, tuple[frozenset, frozenset]] = {}
    ring_case = None

    for q in CANONICAL:
        print(f"\n[2] Query: {q!r}")
        r = c.post("/api/query", json={"query": q}).json()

        if r.get("clarification_needed"):
            check("planner asked for clarification", True, r["clarification_needed"][:60])
            continue

        steps = r.get("steps") or []
        ran = frozenset(s["tool"] for s in steps if not s["skipped"])
        skipped = frozenset(s["tool"] for s in steps if s["skipped"])
        plans[q] = (ran, skipped)

        check("plan returned with steps", len(steps) > 0, f"{len(ran)} ran, {len(skipped)} skipped")
        check("prose narration present", bool(r.get("prose")), (r.get("prose") or "")[:70] + "…")
        check("every step has Chose/Because/Returned",
              all(s["chose"] and s["because"] is not None and s["returned"] for s in steps))

        tid = r["trace_id"]
        t0 = time.time()
        status = "running"
        while status == "running" and time.time() - t0 < 90:
            time.sleep(0.3)
            status = c.get(f"/api/query/{tid}/trace").json()["status"]
        elapsed = time.time() - t0
        check("run completed", status == "done", f"{elapsed:.1f}s")
        check("under the 10s query budget", elapsed < 10.0, f"{elapsed:.1f}s")

        res = c.get(f"/api/query/{tid}/results").json()
        # A query answers EITHER with risk records or with a factual answer
        # (a count or a dataset profile). Query 2 is a counting question, so
        # demanding risk records for it would be demanding the wrong answer.
        answered = (
            len(res["results"]) > 0
            or res.get("aggregation") is not None
            or res.get("profile") is not None
        )
        detail = (f"{len(res['results'])} scored, {len(res['cases'])} cases"
                  if res["results"] else
                  f"count answer: {(res.get('aggregation') or {}).get('matched', 'profile')}")
        check("query produced an answer", answered, detail)
        check("result prose present", bool(res.get("prose")), (res.get("prose") or "")[:70] + "…")
        check("every result explains itself", all(x["explanation"] for x in res["results"]))
        check("every case has a recommended action",
              all(x["recommended_action"] in {"monitor", "flag for review", "report"} for x in res["cases"]))

        top = next((x for x in res["cases"] if x["risk_level"] == "HIGH"), None)
        if top and top["ring"]:
            ring_case = top

    print("\n[3] Agentic behaviour — plans must differ by query")
    q1, q2, q3 = CANONICAL
    if q1 in plans and q2 in plans:
        check("query 2 skips anomaly_model", "anomaly_model" in plans[q2][1])
        check("query 2 skips graph_analysis", "graph_analysis" in plans[q2][1])
    if q3 in plans and q1 in plans:
        check("entity query runs strictly more tools than query 1",
              plans[q3][0] > plans[q1][0], f"{len(plans[q3][0])} vs {len(plans[q1][0])} tools")

    print("\n[4] The injected ring — the headline catch")
    if ring_case is None:
        check("ring case surfaced at HIGH", False, "no HIGH case with a ring subgraph found")
    else:
        check("aggregator flagged HIGH", ring_case["account_id"] == "4521",
              f"{ring_case['account_id']} score={ring_case['score']}")
        check("ring subgraph present", len(ring_case["ring"]["nodes"]) == 10,
              f"{len(ring_case['ring']['nodes'])} nodes")
        check("recommended action is report", ring_case["recommended_action"] == "report")
        deposits = [t for t in ring_case["timeline"] if t["direction"] == "in"]
        check("threshold chart has inbound data", len(deposits) > 0, f"{len(deposits)} inbound rows")

        full = c.get(f"/api/case/{ring_case['case_id']}").json()
        words = len((full.get("narrative") or "").split())
        check("SAR narrative drafted", words > 0, f"{words} words")
        check("SAR is 150-250 words", 150 <= words <= 250, f"{words}")
        check("SAR names the account", ring_case["account_id"] in (full.get("narrative") or ""))

        exp = c.get(f"/api/case/{ring_case['case_id']}/export")
        check("export returns a document", exp.status_code == 200, f"{len(exp.content):,} bytes")
        # The export is a real PDF (xhtml2pdf), not HTML, so its bytes are a
        # compressed content stream — text-searching exp.text for "SAR
        # narrative" the way the old HTML export allowed no longer means
        # anything. Checking the magic bytes and content-type instead; the
        # narrative and formula's actual correctness is already asserted
        # against the source JSON above ("SAR narrative drafted", "SAR is
        # 150-250 words", "SAR names the account"), which is what the PDF is
        # rendered from.
        check("export is a valid PDF", exp.content[:5] == b"%PDF-" and
              "application/pdf" in exp.headers.get("content-type", ""))

    print("\n" + "=" * 60)
    if failed:
        print(f"\033[31m{len(failed)} FAILED\033[0m, {passed} passed\n")
        for f in failed:
            print(f"  · {f}")
        return 1
    print(f"\033[32mAll {passed} checks passed.\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"))
