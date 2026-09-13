"""Fire the same site-less free-text claim 3 times in a row and inspect
each run for LLM-vs-fallback behaviour, trace step count, and any
[llm] error lines emitted by uvicorn.
"""

from __future__ import annotations

import httpx
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLAIM = "Omar wants to pay 500 SAR for groceries"
BASE = "http://127.0.0.1:8765"


def fire(i):
    t0 = time.time()
    r = httpx.post(f"{BASE}/claims", json={"text": CLAIM}, timeout=90.0)
    dt = time.time() - t0
    d = r.json()
    rec = d.get("evidence_record") or {}
    trace = d.get("agent_trace") or []

    # Find the explain step and the step immediately after it
    llm_ran = False
    fallback_seen = False
    llm_failure_seen = False
    for i2, s in enumerate(trace):
        detail = s.get("detail", "")
        if "No LLM available" in detail:
            fallback_seen = True
        if "LLM rationale failed" in detail:
            llm_failure_seen = True
        if s.get("action") == "explain" and "Asking LLM" in detail:
            # look at the next step
            if i2 + 1 < len(trace) and "Rationale written" in trace[i2 + 1].get("detail", ""):
                llm_ran = True
        if s.get("action") == "explain" and "Re-asking LLM" in detail:
            if i2 + 1 < len(trace) and "Rationale written" in trace[i2 + 1].get("detail", ""):
                llm_ran = True

    # Look at rationale — LLM rationales tend to vary; fallback is templated
    rat = (rec.get("rationale") or "")[:120]
    print(f"--- run #{i}  elapsed={dt:.1f}s  trace_steps={len(trace)} ---")
    print(f"  verdict          : {rec.get('verdict')} ({rec.get('confidence')})")
    print(f"  LLM ran cleanly  : {llm_ran}")
    print(f"  fallback msg seen: {fallback_seen}")
    print(f"  LLM-fail msg seen: {llm_failure_seen}")
    print(f"  rationale[:120]  : {rat}")


def main():
    # Wait for server up
    for _ in range(20):
        try:
            httpx.get(f"{BASE}/health", timeout=1.0).raise_for_status(); break
        except Exception:
            time.sleep(0.3)
    print(f"claim: {CLAIM!r}\n")
    for i in range(1, 4):
        fire(i)
        # No sleep — rapid-fire, so we see burst-load behaviour like a live demo


if __name__ == "__main__":
    main()
