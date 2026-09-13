"""Compare scripted vs live test-matrix runs and highlight verdict/profile
changes. Used to prove that switching from scripted CAMARA presets to the
real Nokia simulator did not silently change any verdict logic — only
inputs changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
SCRIPTED = HERE / "test_matrix_output_scripted.json"
LIVE = HERE / "test_matrix_output.json"


def _index(rows):
    by_tag = {}
    for row in rows:
        by_tag[row["tag"]] = row
    return by_tag


def _digest(row):
    body = row["response"].get("body")
    if not isinstance(body, dict):
        return {"http": row["response"].get("http"), "err": str(body)[:120]}
    r = body.get("evidence_record") or {}
    signals = [e["signal_name"] for e in r.get("evidence", [])]
    return {
        "http": row["response"].get("http"),
        "profile": r.get("profile"),
        "verdict": r.get("verdict"),
        "confidence": r.get("confidence"),
        "trace_steps": len(body.get("agent_trace", [])),
        "signal_count": len(signals),
    }


def main() -> int:
    if not SCRIPTED.exists():
        print(f"missing baseline: {SCRIPTED}")
        return 1
    if not LIVE.exists():
        print(f"missing live results: {LIVE}")
        return 1
    scripted = _index(json.loads(SCRIPTED.read_text(encoding="utf-8")))
    live = _index(json.loads(LIVE.read_text(encoding="utf-8")))

    print(f"{'#':<7}{'Scripted':<35}{'Live':<35}{'Same verdict?':<15}")
    print("-" * 92)
    same = diff = 0
    for tag in sorted(set(list(scripted.keys()) + list(live.keys()))):
        s = _digest(scripted[tag]) if tag in scripted else {"missing": True}
        l = _digest(live[tag]) if tag in live else {"missing": True}
        s_summary = f"{s.get('profile','-')}·{s.get('verdict','-')}({s.get('confidence','-')})"
        l_summary = f"{l.get('profile','-')}·{l.get('verdict','-')}({l.get('confidence','-')})"
        # both non-200 → equivalent
        if s.get("http", 200) >= 400 and l.get("http", 200) >= 400:
            marker = "both-error"; same += 1
        elif s.get("verdict") == l.get("verdict") and s.get("profile") == l.get("profile"):
            marker = "yes"; same += 1
        else:
            marker = "DIFF"; diff += 1
        print(f"{tag:<7}{s_summary:<35}{l_summary:<35}{marker:<15}")

    print("-" * 92)
    print(f"Same verdict+profile: {same}   Differences: {diff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
