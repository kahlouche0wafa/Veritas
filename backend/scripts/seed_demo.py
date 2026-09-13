"""CLI wrapper for the demo seed.

    python -m scripts.seed_demo         # reset + seed against the configured backend
    python -m scripts.seed_demo --keep  # seed without resetting first
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veritas.db import get_storage  # noqa: E402
from veritas.db.seed import seed_demo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="don't reset before seeding")
    args = ap.parse_args()

    storage = get_storage()
    print(f"Storage backend: {storage.backend_name}")
    if storage.backend_name == "InMemory":
        print(
            "  (in-memory only — records will not persist across processes. "
            "Set SUPABASE_URL + SUPABASE_SERVICE_KEY in .env and apply "
            "veritas/db/schema.sql to persist.)"
        )

    counts = seed_demo(storage, reset_first=not args.keep)
    print(f"Seeded: {counts}")

    # Show computed historical sites so the REASSIGNMENT precondition is visible.
    print("\nComputed HistoricalSite per subject:")
    for subject_id in ("worker-001", "worker-002", "worker-003"):
        hs = storage.compute_historical_site(subject_id)
        if hs is None:
            print(f"  {subject_id}: (no history)")
        else:
            print(
                f"  {subject_id}: {hs.label} "
                f"({hs.lat:.4f}, {hs.lon:.4f}) — {hs.sample_count} prior CONSISTENT records"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
