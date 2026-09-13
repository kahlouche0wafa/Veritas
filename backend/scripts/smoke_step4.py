"""Step 4 smoke test — full free-text → verdict → LLM rationale flow.

For each free-text claim we:
  1. Normalise (LLM) → structured Claim
  2. Select profile (LLM classifier — will be PRESENCE for all here)
  3. Run PRESENCE verification with computed historical site
  4. Have the agent write the rationale (LLM)
  5. Persist to storage

The verdict on each line is chosen by decide_verdict() — the LLM only
writes the prose next to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# LLM rationales sometimes contain UTF-8 punctuation (curly quotes, narrow
# no-break spaces). Windows' default cp1252 stdout crashes on those, so
# force UTF-8 for this smoke test's printing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from veritas.agent import Agent, ClaimNormalisationError  # noqa: E402
from veritas.camara import ScriptedCamaraClient  # noqa: E402
from veritas.config import SETTINGS  # noqa: E402
from veritas.db import get_storage, reset_storage_cache  # noqa: E402
from veritas.db.seed import seed_demo  # noqa: E402
from veritas.presence import run_presence_verification  # noqa: E402


FREE_TEXT_CLAIMS = [
    # Ahmed (worker-001), historical Budapest HQ, claim Budapest HQ -> CONSISTENT
    "Ahmed was on shift at Budapest HQ today from 09:00 to 17:00.",

    # Sara (worker-002), historical Riyadh, claim Budapest HQ, device really in
    # Budapest -> REASSIGNMENT (pattern break the claim itself declares)
    "Sara has been reassigned to the Budapest office this week and is there today.",

    # Omar (worker-003), historical Cairo, but claim Riyadh Site A;
    # device is really in Budapest -> CONTRADICTED, liveness stub fires
    "Omar clocked in at Riyadh Site A this morning at 08:00.",

    # Layla (worker-004) -> unreachable device (+99999991002) -> INSUFFICIENT
    "Layla was on site at the Cairo site during the morning shift.",
]


def main() -> int:
    print(f"LLM available: {SETTINGS.has_llm}")
    reset_storage_cache()
    storage = get_storage()
    seed_demo(storage)
    print(f"Storage: {storage.backend_name} (seeded)\n")

    agent = Agent()
    client = ScriptedCamaraClient()

    for text in FREE_TEXT_CLAIMS:
        print("=" * 72)
        print(f"INPUT: {text}")
        try:
            claim = agent.normalise_claim(text)
        except ClaimNormalisationError as exc:
            print(f"  normalise failed: {exc}")
            continue

        profile = agent.select_profile(claim)
        print(
            f"  normalised -> subject={claim.subject_id} "
            f"phone={claim.phone} site={claim.claimed_site.label}  "
            f"window={claim.time_window_start[:19]} .. {claim.time_window_end[:19]}"
        )
        print(f"  profile    -> {profile}")

        hs = storage.compute_historical_site(claim.subject_id)
        record = run_presence_verification(
            claim, client, historical_site=hs,
            rationale_writer=agent.write_rationale,
        )
        storage.save_claim(claim)
        storage.save_evidence_record(record)

        print(f"  verdict    -> {record.verdict.value}  ({record.confidence.value})")
        print(f"  rationale  -> {record.rationale}")
        if record.liveness:
            print(
                f"  liveness   -> {record.liveness['outcome']} "
                f"@ {record.liveness['replied_at'][11:19]} UTC, "
                f"zone: {record.liveness['responder_zone']}"
            )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
