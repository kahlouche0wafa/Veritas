"""Demo seed data.

Inserts ~20 synthetic prior evidence records across 3 test subjects so the
accumulated-history logic in the verdict engine has real data to compare
against. This is what makes REASSIGNMENT demonstrable — without a history
of Riyadh Site A CONSISTENT records for worker-002, its Budapest claim
would just be a CONSISTENT for a subject VERITAS knows nothing about.

Every seeded record is flagged `is_seed=true` in Supabase (and marked in
its rationale) so nobody mistakes them for real observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..models import (
    Claim,
    ClaimedSite,
    Confidence,
    ConsentStatus,
    EvidenceClass,
    EvidenceItem,
    EvidenceRecord,
    Verdict,
)
from .storage import Storage


@dataclass
class DemoSubject:
    subject_id: str
    phone: str
    site_lat: float
    site_lon: float
    site_label: str
    n_records: int


# The simulator's fixed device location — used as the "observed" position
# for CONSISTENT seed records at Budapest HQ.
SIM_LAT, SIM_LON = 47.486276, 19.079156

DEMO_SUBJECTS: list[DemoSubject] = [
    # Long history at Budapest HQ — this is the CONSISTENT baseline. If a
    # new claim comes in for the same site, it's CONSISTENT; if it comes in
    # for somewhere else and the device is really elsewhere, CONTRADICTED.
    DemoSubject("worker-001", "+99999991000", SIM_LAT, SIM_LON, "Budapest HQ", n_records=8),

    # Long history at Riyadh Site A. In today's REASSIGNMENT demo, the
    # claim for worker-002 will say "Budapest HQ" — a real pattern break.
    DemoSubject("worker-002", "+99999991001", 24.7136, 46.6753, "Riyadh Site A", n_records=7),

    # Third subject at Cairo — mostly so list_claims looks realistic.
    DemoSubject("worker-003", "+99999991003", 30.0444, 31.2357, "Cairo Site B", n_records=5),
]


def _seed_evidence_items(subject: DemoSubject, when: str) -> list[EvidenceItem]:
    """Build the evidence list a real PRESENCE run would have produced for
    this subject on a normal CONSISTENT day.
    """
    site = {
        "lat": subject.site_lat, "lon": subject.site_lon,
        "radius_m": 500.0, "label": subject.site_label,
    }
    return [
        EvidenceItem(
            signal_name="connectivity",
            value={"status": "CONNECTED_DATA", "ok": True, "error": None},
            evidence_class=EvidenceClass.CURRENT, timestamp=when,
        ),
        EvidenceItem(
            signal_name="geofence_vs_claimed_site",
            value={
                "ok": True, "inside": True,
                "distance_m": 0.0, "effective_radius_m": 1500.0,
                "claimed_site": site, "error": None,
            },
            evidence_class=EvidenceClass.CURRENT, timestamp=when,
        ),
        EvidenceItem(
            signal_name="device_swap",
            value={"ok": True, "swapped": False, "error": None},
            evidence_class=EvidenceClass.CARRIER_COMPUTED, timestamp=when,
        ),
        EvidenceItem(
            signal_name="tenure",
            value={
                "ok": True, "tenure_date_check": True,
                "contract_type": "PAYM", "error": None,
            },
            evidence_class=EvidenceClass.CARRIER_COMPUTED, timestamp=when,
        ),
        EvidenceItem(
            signal_name="number_verification",
            value=None, evidence_class=EvidenceClass.CARRIER_COMPUTED, timestamp=when,
            note="not queried — requires production OIDC flow",
        ),
        EvidenceItem(
            signal_name="verdict_rule_matched",
            value="claim_site_match_no_swap",
            evidence_class=EvidenceClass.CARRIER_COMPUTED, timestamp=when,
            note="stable rule tag from decide_verdict() — seeded",
        ),
    ]


def seed_demo(storage: Storage, reset_first: bool = True) -> dict[str, int]:
    """Populate storage with synthetic history. Returns a small counter dict."""
    if reset_first:
        storage.reset()

    now = datetime.now(timezone.utc)
    inserted_claims = 0
    inserted_records = 0

    for subject in DEMO_SUBJECTS:
        for i in range(subject.n_records):
            # Space observations one day apart, ending yesterday.
            when = (now - timedelta(days=i + 1)).isoformat()
            window_start = (now - timedelta(days=i + 1, hours=8)).isoformat()
            window_end = (now - timedelta(days=i + 1)).isoformat()

            claim = Claim(
                subject_id=subject.subject_id,
                phone=subject.phone,
                assertion_text=(
                    f"[SEED] {subject.subject_id} on shift at {subject.site_label} "
                    f"on {when[:10]}."
                ),
                claimed_site=ClaimedSite(
                    lat=subject.site_lat, lon=subject.site_lon,
                    radius_m=500.0, label=subject.site_label,
                ),
                time_window_start=window_start,
                time_window_end=window_end,
                source="seed",
                consent_status=ConsentStatus.ACTIVE,
                created_at=when,
            )
            storage.save_claim(claim)
            inserted_claims += 1

            record = EvidenceRecord(
                claim_id=claim.id,
                profile="PRESENCE",
                verdict=Verdict.CONSISTENT,
                confidence=Confidence.HIGH,
                evidence=_seed_evidence_items(subject, when),
                rationale=(
                    "[SEED] Historical CONSISTENT record — synthetic demo data. "
                    f"Device observed inside {subject.site_label} during the shift window."
                ),
                consent_status=ConsentStatus.ACTIVE,
                created_at=when,
            )
            storage.save_evidence_record(record, is_seed=True)
            inserted_records += 1

    return {
        "subjects": len(DEMO_SUBJECTS),
        "claims": inserted_claims,
        "records": inserted_records,
    }
