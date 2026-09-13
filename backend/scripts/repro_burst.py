"""Fire the same site-less free-text claim 8 times sequentially with no
delay — the goal is to burst-hit Groq's shared 8000 TPM cap and trigger
the failure the user observed.

Each request fires 3 LLM calls (normalise x2 + rationale) because the
claim mentions no site.  gpt-oss-20b's reasoning tokens can push each
call to ~700 total tokens, so ~2100 tokens per request. 8 requests =
~16800 tokens attempted inside the 8000/min bucket — should trigger a 429
on run #3 or #4 if the shared-TPM theory holds.
"""

from __future__ import annotations
import httpx, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLAIM = "Omar wants to pay 500 SAR for groceries"
BASE = "http://127.0.0.1:8765"

print(f"claim: {CLAIM!r}\n")
for i in range(1, 9):
    t0 = time.time()
    r = httpx.post(f"{BASE}/claims", json={"text": CLAIM}, timeout=90.0)
    dt = time.time() - t0
    d = r.json()
    rec = d.get("evidence_record") or {}
    trace = d.get("agent_trace") or []
    fallback = any("No LLM available" in s.get("detail", "") for s in trace)
    llm_fail = any("LLM rationale failed" in s.get("detail", "") for s in trace)
    steps = len(trace)
    rat = (rec.get("rationale") or "")[:90]
    print(f"run {i}  {dt:5.1f}s  verdict={rec.get('verdict','?'):<12}  "
          f"steps={steps:2d}  fallback={fallback}  llm_fail={llm_fail}  "
          f"rat[:90]={rat!r}")
