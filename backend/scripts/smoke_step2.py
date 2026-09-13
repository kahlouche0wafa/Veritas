"""Step 2 smoke test — walk all four verdicts end-to-end.

For each scenario we build a Claim, optionally supply a HistoricalSite,
run the PRESENCE profile against the scripted CAMARA client, and print
the resulting EvidenceRecord. The verdict on each line at the bottom is
the one-glance proof that Step 2's rule engine is doing what the brief
asked for.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veritas.camara import ScriptedCamaraClient  # noqa: E402
from veritas.models import Claim, ClaimedSite, ConsentStatus, HistoricalSite  # noqa: E402
from veritas.presence import run_presence_verification  # noqa: E402

# The simulator's fixed device location — used both as a matching claimed
# site (CONSISTENT / REASSIGNMENT) and as the historical baseline.
SIM_LAT, SIM_LON = 47.486276, 19.079156

# A site the device is definitely NOT at (Riyadh) — used for CONTRADICTED.
RIYADH_LAT, RIYADH_LON = 24.7136, 46.6753


def _window():
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=8)).isoformat(),
    )


def scenario_consistent():
    ws, we = _window()
    return Claim(
        subject_id="worker-001",
        phone="+99999991000",
        assertion_text=(
            "Worker 001 was on shift at the Budapest HQ site today, 09:00-17:00."
        ),
        claimed_site=ClaimedSite(lat=SIM_LAT, lon=SIM_LON, radius_m=500.0, label="Budapest HQ"),
        time_window_start=ws,
        time_window_end=we,
    ), HistoricalSite(
        lat=SIM_LAT, lon=SIM_LON, radius_m=500.0, label="Budapest HQ",
        sample_count=42, last_seen=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )


def scenario_reassignment():
    # Same device (still at simulator's Budapest coordinates), but the claim
    # asserts that today the subject is at the Budapest site — while their
    # historical site is somewhere else (Riyadh). The claim itself declares
    # the new location; VERITAS confirms the device is genuinely there.
    ws, we = _window()
    return Claim(
        subject_id="worker-002",
        phone="+99999991001",
        assertion_text=(
            "Worker 002 has been reassigned from Riyadh Site A to Budapest HQ "
            "this week and is on site there today."
        ),
        claimed_site=ClaimedSite(lat=SIM_LAT, lon=SIM_LON, radius_m=500.0, label="Budapest HQ"),
        time_window_start=ws,
        time_window_end=we,
    ), HistoricalSite(
        lat=RIYADH_LAT, lon=RIYADH_LON, radius_m=500.0, label="Riyadh Site A",
        sample_count=57, last_seen=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )


def scenario_contradicted():
    # The claim insists the subject is at Riyadh Site A, but the device
    # is actually in Budapest. Straight CONTRADICTED, liveness stub fires.
    ws, we = _window()
    return Claim(
        subject_id="worker-003",
        phone="+99999991003",
        assertion_text=(
            "Worker 003 clocked in at Riyadh Site A this morning at 08:00."
        ),
        claimed_site=ClaimedSite(
            lat=RIYADH_LAT, lon=RIYADH_LON, radius_m=500.0, label="Riyadh Site A"
        ),
        time_window_start=ws,
        time_window_end=we,
    ), HistoricalSite(
        lat=RIYADH_LAT, lon=RIYADH_LON, radius_m=500.0, label="Riyadh Site A",
        sample_count=88, last_seen=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )


def scenario_insufficient():
    # The unreachable device (+99999991002 returns NOT_CONNECTED).
    ws, we = _window()
    return Claim(
        subject_id="worker-004",
        phone="+99999991002",
        assertion_text=(
            "Worker 004 was at Budapest HQ during the morning shift."
        ),
        claimed_site=ClaimedSite(lat=SIM_LAT, lon=SIM_LON, radius_m=500.0, label="Budapest HQ"),
        time_window_start=ws,
        time_window_end=we,
    ), None


def scenario_no_consent():
    # Consent gate — VERITAS refuses to produce a verdict at all.
    ws, we = _window()
    claim = Claim(
        subject_id="worker-005",
        phone="+99999991000",
        assertion_text="Worker 005 was at Budapest HQ.",
        claimed_site=ClaimedSite(lat=SIM_LAT, lon=SIM_LON, radius_m=500.0, label="Budapest HQ"),
        time_window_start=ws,
        time_window_end=we,
        consent_status=ConsentStatus.MISSING,
    )
    return claim, None


SCENARIOS = [
    ("CONSISTENT scenario",   scenario_consistent),
    ("REASSIGNMENT scenario", scenario_reassignment),
    ("CONTRADICTED scenario", scenario_contradicted),
    ("INSUFFICIENT scenario", scenario_insufficient),
    ("NO-CONSENT scenario",   scenario_no_consent),
]


def main() -> int:
    client = ScriptedCamaraClient()
    summary = []
    for label, build in SCENARIOS:
        claim, hist = build()
        print(f"\n=========== {label} ===========")
        print(f"claim: {claim.assertion_text}")
        record = run_presence_verification(claim, client, historical_site=hist)
        print(f"verdict:    {record.verdict.value}")
        print(f"confidence: {record.confidence.value}")
        print(f"rationale:  {record.rationale}")
        if record.liveness:
            print(f"liveness:   {record.liveness['outcome']}  (stub)")
        summary.append((label, record.verdict.value, record.confidence.value))

    print("\n=========== summary ===========")
    for label, v, c in summary:
        print(f"  {label:28s} -> {v}  ({c})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
