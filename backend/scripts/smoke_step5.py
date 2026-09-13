"""Step 5 smoke — all three profiles routed through decide_verdict.

We fire one claim per profile so each demo card lands, then also fire a
mixed batch through the dispatcher to prove profile routing works.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from veritas.agent import Agent  # noqa: E402
from veritas.camara import ScriptedCamaraClient  # noqa: E402
from veritas.db import get_storage, reset_storage_cache  # noqa: E402
from veritas.db.seed import seed_demo  # noqa: E402
from veritas.models import Claim, ClaimedSite  # noqa: E402
from veritas.profiles import ALL_PROFILES, run_verification  # noqa: E402

SIM_LAT, SIM_LON = 47.486276, 19.079156


def _window():
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=4)).isoformat(),
    )


def _direct_claim(subject_id, phone, assertion, site_label, lat, lon, radius=500.0):
    ws, we = _window()
    return Claim(
        subject_id=subject_id, phone=phone, assertion_text=assertion,
        claimed_site=ClaimedSite(lat=lat, lon=lon, radius_m=radius, label=site_label),
        time_window_start=ws, time_window_end=we,
    )


DIRECT_CLAIMS = [
    (
        "PRESENCE",
        _direct_claim(
            "worker-001", "+99999991000",
            "Ahmed on shift at Budapest HQ today.",
            "Budapest HQ", SIM_LAT, SIM_LON,
        ),
    ),
    (
        "IDENTITY",
        _direct_claim(
            "worker-002", "+99999991001",
            "Verify Sara's identity on her registered device (expected zone: Budapest HQ).",
            "Budapest HQ", SIM_LAT, SIM_LON,
        ),
    ),
    (
        "TRANSACTION",
        _direct_claim(
            "worker-003", "+99999991003",
            "Omar wants to authorise a payment from his account (expected zone: Budapest HQ).",
            "Budapest HQ", SIM_LAT, SIM_LON,
        ),
    ),
    (
        "TRANSACTION",
        _direct_claim(
            "worker-001", "+99999991000",
            "Ahmed initiated a payment while allegedly at Riyadh Site A.",
            "Riyadh Site A", 24.7136, 46.6753,
        ),
    ),
]


AGENT_ROUTED_CLAIMS = [
    "Verify Sara's identity — this is a KYC check on her registered SIM.",
    "Ahmed wants to authorise a transfer from his account — please OTP challenge.",
    "Omar on shift at Cairo Site B today from 08:00 to 16:00.",
]


def main() -> int:
    reset_storage_cache()
    storage = get_storage()
    seed_demo(storage)
    print(f"Storage: {storage.backend_name} (seeded)")
    print(f"Known profiles: {ALL_PROFILES}\n")

    agent = Agent()
    client = ScriptedCamaraClient()

    print("========= direct profile dispatch =========")
    for profile, claim in DIRECT_CLAIMS:
        hs = storage.compute_historical_site(claim.subject_id)
        record = run_verification(
            profile, claim, client,
            historical_site=hs,
            rationale_writer=agent.write_rationale,
        )
        storage.save_claim(claim)
        storage.save_evidence_record(record)
        friction = next(
            (e.value for e in record.evidence if e.signal_name == "friction_action"),
            "(no friction item)",
        )
        print(
            f"  {profile:12s} {claim.subject_id:12s} -> "
            f"{record.verdict.value:22s} ({record.confidence.value})   "
            f"friction={friction if friction is None else friction.get('challenge_type') if isinstance(friction, dict) else friction}"
        )

    print("\n========= agent-routed (free text -> profile pick) =========")
    for text in AGENT_ROUTED_CLAIMS:
        # resolve_claim falls back to the subject's historical site if the
        # text doesn't name one (common for IDENTITY / TRANSACTION claims).
        claim = agent.resolve_claim(text, storage)
        profile = agent.select_profile(claim)
        hs = storage.compute_historical_site(claim.subject_id)
        record = run_verification(
            profile, claim, client,
            historical_site=hs,
            rationale_writer=agent.write_rationale,
        )
        storage.save_claim(claim)
        storage.save_evidence_record(record)
        print(f"  '{text[:60]}...'")
        print(
            f"    -> profile={profile}  subject={claim.subject_id}  "
            f"verdict={record.verdict.value} ({record.confidence.value})"
        )

    print("\n========= TRANSACTION full rationale sample =========")
    txn_records = [
        r for _, r in storage.list_claims(limit=50)
        if r and r.profile == "TRANSACTION"
    ]
    if txn_records:
        r = txn_records[0]
        print(f"verdict:   {r.verdict.value} ({r.confidence.value})")
        print(f"rationale: {r.rationale}")
        otp = next(
            (e.value for e in r.evidence if e.signal_name == "friction_action"),
            None,
        )
        if otp:
            print(f"OTP stub:  {otp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
