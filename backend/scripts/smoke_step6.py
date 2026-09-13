"""Step 6 smoke — hit every endpoint on the running uvicorn."""

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

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8765"


def pretty(label: str, data) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:2500])


def wait_for_up(timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit("server did not come up on 8765")


def main() -> int:
    wait_for_up()

    with httpx.Client(base_url=BASE, timeout=60.0) as c:
        pretty("GET /health", c.get("/health").json())

        pretty("GET /catalog", c.get("/catalog").json())

        pretty("GET /demo/scenarios", c.get("/demo/scenarios").json())

        # POST /demo/seed (already auto-seeded on startup; reseed to prove).
        pretty("POST /demo/seed", c.post("/demo/seed").json())

        # Fire each of the 4 pre-built scenarios.
        scenarios = c.get("/demo/scenarios").json()["scenarios"]
        submitted_ids: list[str] = []
        for s in scenarios:
            print(f"\n=== scenario {s['id']} ===")
            resp = c.post("/claims", json={"text": s["text"]})
            print(f"HTTP {resp.status_code}")
            body = resp.json()
            r = body["evidence_record"] or {}
            print(f"  verdict:    {r.get('verdict')} ({r.get('confidence')})")
            print(f"  profile:    {r.get('profile')}")
            print(f"  rationale:  {(r.get('rationale') or '')[:220]}")
            if r.get("liveness"):
                print(f"  liveness:   {r['liveness'].get('outcome')} (stub)")
            submitted_ids.append(body["claim"]["id"])

        # GET /claims/{id} for the last submitted claim.
        if submitted_ids:
            r = c.get(f"/claims/{submitted_ids[-1]}").json()
            pretty("GET /claims/{last_id}", {
                "claim_id": r["claim"]["id"],
                "subject_id": r["claim"]["subject_id"],
                "verdict": r["evidence_record"]["verdict"],
                "evidence_signals": [e["signal_name"] for e in r["evidence_record"]["evidence"]],
            })

        # GET /claims listing (excluding seed) — should show the 4 fresh POSTs.
        listing = c.get("/claims", params={"limit": 20}).json()
        print(f"\n--- GET /claims (non-seed) ---")
        print(f"count: {listing['count']}")
        for item in listing["items"][:8]:
            claim = item["claim"]
            record = item["evidence_record"] or {}
            print(
                f"  {claim['created_at'][:19]}  "
                f"{claim['subject_id']:12s} "
                f"{(claim['claimed_site']['label'] or '?'):32s} -> "
                f"{record.get('verdict','(none)'):22s} ({record.get('confidence','')})"
            )

        # POST /claims with a structured (bypass-LLM) payload.
        structured = c.post("/claims", json={
            "subject_id": "worker-001",
            "assertion_text": "Structured claim: Ahmed at Budapest HQ",
            "claimed_site": {"lat": 47.486276, "lon": 19.079156, "radius_m": 500, "label": "Budapest HQ"},
            "profile": "PRESENCE",
        }).json()
        pretty("POST /claims (structured)", {
            "verdict": structured["evidence_record"]["verdict"],
            "confidence": structured["evidence_record"]["confidence"],
            "profile": structured["evidence_record"]["profile"],
        })

        # 404
        r = c.get("/claims/does_not_exist")
        print(f"\nGET /claims/does_not_exist -> HTTP {r.status_code} (expected 404)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
