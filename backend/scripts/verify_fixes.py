"""Verify the two fixes:
    #18 — empty text returns HTTP 400 (not 422/500) with a friendly message
    #22 — consent MISSING → zero CAMARA calls, zero friction actions,
          across all three profiles (PRESENCE / IDENTITY / TRANSACTION)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8765"

# CAMARA-call trace actions and friction-action signal names we should NOT
# see on a consent-denied path.
CAMARA_TRACE_MARKERS = ("Calling getConnectivityStatus", "Calling retrieveLocation",
                        "Calling checkDeviceSwap", "Calling checkTenure")
FORBIDDEN_EVIDENCE = ("friction_action",)  # on no-consent, must not appear


def pretty(label: str, obj) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(obj, indent=2, ensure_ascii=False)[:1500])


def test_empty_text() -> bool:
    print("\n=========== TEST #18 — empty text ===========")
    r = httpx.post(f"{BASE}/claims", json={"text": ""}, timeout=10)
    body = r.json()
    print(f"HTTP {r.status_code}")
    print(json.dumps(body, indent=2))
    ok_status = r.status_code == 400
    detail = body.get("detail") or {}
    detail_error = detail.get("error") if isinstance(detail, dict) else None
    ok_msg = detail_error == "no_claim_text"
    print(f"  PASS ✓" if ok_status and ok_msg else f"  FAIL ✗ (expected 400 with error=no_claim_text)")
    # Also: whitespace-only
    r2 = httpx.post(f"{BASE}/claims", json={"text": "   \n\t  "}, timeout=10)
    print(f"whitespace-only: HTTP {r2.status_code}")
    return ok_status and ok_msg and r2.status_code == 400


def test_consent_gate(profile: str) -> bool:
    print(f"\n=========== TEST #22 — consent MISSING, profile={profile} ===========")
    body = {
        "subject_id": "worker-001",
        "assertion_text": f"[test] no-consent under {profile}",
        "claimed_site": {"lat": 47.486276, "lon": 19.079156, "radius_m": 500, "label": "Budapest HQ"},
        "consent_status": "MISSING",
        "profile": profile,
    }
    r = httpx.post(f"{BASE}/claims", json=body, timeout=30)
    resp = r.json()
    print(f"HTTP {r.status_code}")
    record = resp.get("evidence_record") or {}
    trace = resp.get("agent_trace") or []
    verdict = record.get("verdict")
    signals = [e["signal_name"] for e in record.get("evidence", [])]

    camara_calls = [s for s in trace
                    if any(m in s.get("detail", "") for m in CAMARA_TRACE_MARKERS)]
    friction = [s for s in signals if s in FORBIDDEN_EVIDENCE]

    print(f"  verdict:                 {verdict}")
    print(f"  trace steps:             {len(trace)}")
    print(f"  evidence signals:        {signals}")
    print(f"  CAMARA-call trace steps: {len(camara_calls)}  (must be 0)")
    print(f"  friction_action items:   {len(friction)}  (must be 0)")

    ok_verdict = verdict == "NO_VERDICT_NO_CONSENT"
    ok_no_camara = len(camara_calls) == 0
    ok_no_friction = len(friction) == 0
    result = ok_verdict and ok_no_camara and ok_no_friction
    print(f"  RESULT: {'PASS ✓' if result else 'FAIL ✗'}")
    return result


def main() -> int:
    results = {
        "empty_text": test_empty_text(),
        "consent_PRESENCE": test_consent_gate("PRESENCE"),
        "consent_IDENTITY": test_consent_gate("IDENTITY"),
        "consent_TRANSACTION": test_consent_gate("TRANSACTION"),
    }
    print("\n\n=========== SUMMARY ===========")
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
