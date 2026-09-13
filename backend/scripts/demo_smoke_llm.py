"""After the truststore fix, re-run the two TRANSACTION claims + the five
demo claims and print:

  * verdict + profile
  * whether the 'explain' trace step got a real LLM run (Rationale written)
    or the deterministic fallback
  * the full rationale text (so we can eyeball 'LLM voice' vs 'template voice')
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx

BASE = "http://127.0.0.1:8765"

CLAIMS_TX = [
    ("TX1", "A payment of 120,000 SAR is pending from Ahmed — verify before releasing"),
    ("TX2", "Omar wants to pay 500 SAR for groceries"),
]
CLAIMS_DEMO = [
    ("D1_CONSISTENT",   "Ahmed was on shift at Budapest HQ today from 09:00 to 17:00."),
    ("D2_REASSIGN",     "Sara has been reassigned to the Budapest office this week and is there today."),
    ("D3_CONTRADICTED", "Omar clocked in at Riyadh Site A this morning at 08:00."),
    ("D4_INSUFFICIENT", "Layla was on site at Cairo Site B this morning"),
    ("D5_IDENTITY",     "A customer is requesting a password reset for Ahmed's account — confirm their identity"),
]


def fire(tag, text):
    r = httpx.post(f"{BASE}/claims", json={"text": text}, timeout=90.0)
    body = r.json()
    rec = body.get("evidence_record") or {}
    trace = body.get("agent_trace") or []
    explain_step = next((s for s in trace if s.get("action") == "explain"), None)
    result_after_explain = next(
        (trace[i + 1] for i, s in enumerate(trace) if s.get("action") == "explain" and i + 1 < len(trace)),
        None,
    )
    print("=" * 78)
    print(f"[{tag}]  {text}")
    print(f"  → {rec.get('profile')} · {rec.get('verdict')} ({rec.get('confidence')})")
    if explain_step:
        print(f"  explain step: {explain_step['detail']}")
    if result_after_explain and result_after_explain.get("action") == "result":
        print(f"  after-explain: {result_after_explain['detail']}")
    print()
    print("  RATIONALE:")
    for line in (rec.get("rationale") or "").splitlines() or ["(empty)"]:
        print(f"    {line}")
    print()


def main() -> int:
    print(">>> The two TRANSACTION claims that previously showed the fallback:")
    for tag, text in CLAIMS_TX:
        fire(tag, text)
        time.sleep(1)

    print(">>> Five demo claims:")
    for tag, text in CLAIMS_DEMO:
        fire(tag, text)
        time.sleep(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
