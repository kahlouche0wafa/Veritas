"""Run the full 22-claim test matrix defined in the pre-demo prompt.
Emits a Markdown-table results report and a JSON dump of every response
(so we can inspect anything surprising afterwards without re-running).

Assumes the API is up on 127.0.0.1:8765 (any storage / camara backend).
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

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8765"
OUT_JSON = Path(__file__).resolve().parents[1] / "scripts" / "test_matrix_output.json"

TESTS = [
    # -- Presence (basic) --
    ("P1", "Ahmed worked at the Riyadh construction site from 8am to 5pm yesterday", "presence-basic"),
    ("P2", "Sara covered the night shift at the Budapest warehouse today", "presence-basic"),
    ("P3", "Omar was at Site B today", "presence-basic"),
    ("P4", "Worker 7831 was present at Site A, Riyadh, 08:00 to 17:00", "presence-basic"),

    # -- Presence (edge cases) --
    ("P5", "Ahmed was at the site", "vague-site"),
    ("P6", "Someone was at the Riyadh office", "no-subject"),
    ("P7", "+998877665544 was at Cairo Site B today", "unknown-phone"),
    ("P8", "Layla was on site at Cairo Site B this morning", "unreachable-device"),

    # -- Identity --
    ("I1", "Verify that this is the real account holder for phone +99999991000", "identity"),
    ("I2", "A customer is requesting a password reset for Ahmed's account — confirm their identity", "identity"),
    ("I3", "Someone is trying to access Sara's account from a new device", "identity"),
    ("I4", "Confirm the identity of the person using phone +99999991004", "identity-swap"),

    # -- Transaction --
    ("T1", "Authorize a 50,000 SAR wire transfer for Omar", "txn-med"),
    ("T2", "A payment of 120,000 SAR is pending from Ahmed — verify before releasing", "txn-high"),
    ("T3", "High-risk international transfer requested by Sara — check before approving", "txn-high"),
    ("T4", "Omar wants to pay 500 SAR for groceries", "txn-low"),

    # -- Stress --
    ("S1", "check on ahmed", "vague"),
    ("S2", "", "empty"),
    ("S3", ("Some background rambling text that keeps going and going about the weather and "
             "the office coffee and the fact that Q3 is coming up. " * 30
             + " Anyway, verify Ahmed was at Budapest HQ today between 9 and 5 "
             + "as normally scheduled."), "long"),
    ("S4", "أحمد كان في موقع العمل اليوم", "arabic"),
    ("S5_a", "Ahmed was on shift at Budapest HQ today from 09:00 to 17:00.", "dup-a"),
    ("S5_b", "Ahmed was on shift at Budapest HQ today from 09:00 to 17:00.", "dup-b"),
    ("S6", "STRUCTURED_NO_CONSENT", "no-consent-structured"),
]


def post_claim(text: str, extra: dict | None = None) -> dict:
    body = {"text": text}
    if extra:
        body.update(extra)
    r = httpx.post(f"{BASE}/claims", json=body, timeout=90.0)
    return {"http": r.status_code, "body": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text}


def run_one(tag: str, text: str, note: str) -> dict:
    # Special-case S6: structured no-consent claim
    if text == "STRUCTURED_NO_CONSENT":
        return post_claim("", extra={
            "subject_id": "worker-001",
            "assertion_text": "Ahmed at Budapest HQ (no consent)",
            "claimed_site": {"lat": 47.486276, "lon": 19.079156, "radius_m": 500, "label": "Budapest HQ"},
            "consent_status": "MISSING",
            "profile": "PRESENCE",
        })
    return post_claim(text)


def summarize_row(tag: str, text: str, note: str, resp: dict) -> tuple[str, str, str, str, str, str]:
    http = resp["http"]
    body = resp["body"]
    if http >= 400:
        return (tag, text[:60], "-", "-", "-", f"HTTP {http}: {str(body)[:80]}")
    r = body.get("evidence_record") or {}
    claim = body.get("claim") or {}
    profile = r.get("profile","?")
    verdict = r.get("verdict","?")
    conf = r.get("confidence","?")
    trace_steps = len(body.get("agent_trace", []))
    return (
        tag,
        text[:60] + ("…" if len(text) > 60 else ""),
        profile,
        f"{verdict} ({conf})",
        str(trace_steps),
        note,
    )


def main() -> int:
    # Ensure server is up.
    for _ in range(20):
        try:
            httpx.get(f"{BASE}/health", timeout=1.0).raise_for_status(); break
        except Exception:
            time.sleep(0.3)
    print(f"server: {httpx.get(f'{BASE}/health').json()}\n")

    all_results = []
    rows = []
    for tag, text, note in TESTS:
        print(f"--- {tag}: {text[:70]}{'…' if len(text)>70 else ''}")
        try:
            resp = run_one(tag, text, note)
        except Exception as exc:
            resp = {"http": -1, "body": f"EXCEPTION: {exc}"}
        all_results.append({"tag": tag, "text": text, "note": note, "response": resp})
        rows.append(summarize_row(tag, text, note, resp))
        # Preview the verdict quickly for the human running the harness.
        if resp["http"] < 400 and isinstance(resp.get("body"), dict):
            r = resp["body"].get("evidence_record") or {}
            print(f"    -> {r.get('profile','?')} · {r.get('verdict','?')} ({r.get('confidence','?')}) · trace={len(resp['body'].get('agent_trace',[]))}", flush=True)
        else:
            print(f"    !! HTTP {resp['http']}: {str(resp.get('body'))[:120]}", flush=True)

    # Persist full JSON for post-hoc inspection.
    OUT_JSON.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print table.
    print("\n\n============= results table =============")
    header = ("#", "Claim (shortened)", "Profile", "Verdict", "Trace", "Note")
    widths = [6, 62, 12, 22, 6, 24]
    def line(cols):
        return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cols, widths)) + " |"
    print(line(header))
    print(line(["-"*w for w in widths]))
    for row in rows:
        print(line(row))

    print(f"\nfull JSON saved to {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
