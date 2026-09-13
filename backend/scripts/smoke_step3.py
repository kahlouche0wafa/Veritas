"""Step 3 smoke test — seed, then verify the storage read-model is
usable by the Step 2 verdict engine.

For each demo subject we:
  1. Fetch the computed HistoricalSite from stored records.
  2. Build a fresh claim (CONSISTENT / REASSIGNMENT / CONTRADICTED cases).
  3. Run the PRESENCE profile with that historical site.
  4. Save the new claim + record.
  5. Read them back and verify shape.

Success looks like: worker-002 gets REASSIGNMENT (because its computed
history is Riyadh, and the new claim + real device location are Budapest);
worker-001 stays CONSISTENT; worker-003 goes CONTRADICTED when we send an
Egypt-based subject to a Budapest-claimed shift.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veritas.camara import ScriptedCamaraClient  # noqa: E402
from veritas.db import get_storage, reset_storage_cache  # noqa: E402
from veritas.db.seed import seed_demo  # noqa: E402
from veritas.models import Claim, ClaimedSite  # noqa: E402
from veritas.presence import run_presence_verification  # noqa: E402

SIM_LAT, SIM_LON = 47.486276, 19.079156


def _window():
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=8)).isoformat(),
    )


def main() -> int:
    reset_storage_cache()
    storage = get_storage()
    print(f"Storage: {storage.backend_name}")

    counts = seed_demo(storage)
    print(f"Seeded: {counts}\n")

    client = ScriptedCamaraClient()
    ws, we = _window()

    fresh_claims = [
        # worker-001: history Budapest, claim Budapest -> CONSISTENT.
        Claim(
            subject_id="worker-001", phone="+99999991000",
            assertion_text="Worker 001 on shift at Budapest HQ today.",
            claimed_site=ClaimedSite(SIM_LAT, SIM_LON, 500.0, "Budapest HQ"),
            time_window_start=ws, time_window_end=we,
        ),
        # worker-002: history Riyadh, claim Budapest, device really in Budapest -> REASSIGNMENT.
        Claim(
            subject_id="worker-002", phone="+99999991001",
            assertion_text="Worker 002 has been reassigned to Budapest HQ this week.",
            claimed_site=ClaimedSite(SIM_LAT, SIM_LON, 500.0, "Budapest HQ"),
            time_window_start=ws, time_window_end=we,
        ),
        # worker-003: history Cairo, claim Budapest, device really in Budapest.
        # Also a REASSIGNMENT (device matches new claim, history disagrees).
        Claim(
            subject_id="worker-003", phone="+99999991003",
            assertion_text="Worker 003 moved to Budapest HQ.",
            claimed_site=ClaimedSite(SIM_LAT, SIM_LON, 500.0, "Budapest HQ"),
            time_window_start=ws, time_window_end=we,
        ),
        # worker-001 again, but claim Riyadh -> CONTRADICTED (device is Budapest).
        Claim(
            subject_id="worker-001", phone="+99999991000",
            assertion_text="Worker 001 clocked in at Riyadh Site A this morning.",
            claimed_site=ClaimedSite(24.7136, 46.6753, 500.0, "Riyadh Site A"),
            time_window_start=ws, time_window_end=we,
        ),
    ]

    for claim in fresh_claims:
        hs = storage.compute_historical_site(claim.subject_id)
        record = run_presence_verification(claim, client, historical_site=hs)
        storage.save_claim(claim)
        storage.save_evidence_record(record)
        hs_label = hs.label if hs else "(none)"
        print(
            f"{claim.subject_id:12s} history={hs_label:16s} "
            f"claim={claim.claimed_site.label:16s} -> "
            f"{record.verdict.value:22s} ({record.confidence.value})"
        )

    # Read-back check.
    print(f"\nlist_claims (top 6):")
    for claim, record in storage.list_claims(limit=6):
        v = record.verdict.value if record else "(no record)"
        print(f"  {claim.created_at[:19]}  {claim.subject_id:12s} {claim.claimed_site.label:16s} -> {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
