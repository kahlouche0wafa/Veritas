"""Core VERITAS data models.

These are the objects that flow through the whole system: a Claim goes in,
signals are gathered as EvidenceItems, and an EvidenceRecord (with its
Verdict) comes out. Nothing here talks to the network or the LLM — those
concerns live in the camara/, verdict, presence, and agent modules.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


# --- Enums (kept as str Enums so JSON serialization is trivial) -----------

class Verdict(str, Enum):
    CONSISTENT = "CONSISTENT"
    REASSIGNMENT = "REASSIGNMENT"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT = "INSUFFICIENT"
    # Not a verdict per se — returned when we refuse to produce one because
    # consent isn't active. Kept separate so it can never be confused with
    # a real verdict on a dashboard.
    NO_VERDICT_NO_CONSENT = "NO_VERDICT_NO_CONSENT"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConsentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MISSING = "MISSING"


class EvidenceClass(str, Enum):
    # Read live from the network right now (e.g. retrieveLocation).
    CURRENT = "current"
    # Assembled from VERITAS's own historical record (prior evidence records).
    ACCUMULATED = "accumulated"
    # A carrier-side computed signal not tied to a specific moment
    # (e.g. tenure, device-swap window).
    CARRIER_COMPUTED = "carrier_computed"


# --- Value objects --------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


@dataclass
class ClaimedSite:
    lat: float
    lon: float
    radius_m: float
    label: Optional[str] = None  # human-readable name, if the claim named the site


@dataclass
class Claim:
    subject_id: str
    phone: str
    assertion_text: str            # the original free-text claim, verbatim
    claimed_site: ClaimedSite
    time_window_start: str         # ISO8601
    time_window_end: str           # ISO8601
    source: str = "manual"         # who or what filed the claim
    consent_status: ConsentStatus = ConsentStatus.ACTIVE
    id: str = field(default_factory=_uid)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["consent_status"] = self.consent_status.value
        return d


@dataclass
class EvidenceItem:
    """A single signal that was queried (or deliberately skipped) while
    building the verdict. The `value` field carries whatever the signal
    returned — booleans, floats, error strings, or a small dict — so the
    audit trail is self-describing without a hundred narrow subtypes.
    """

    signal_name: str
    value: Any
    evidence_class: EvidenceClass
    timestamp: str = field(default_factory=_now)
    note: Optional[str] = None  # e.g. "not queried — requires production OIDC flow"
    why: Optional[str] = None   # one-line reason this signal was chosen or skipped

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_class"] = self.evidence_class.value
        return d


@dataclass
class EvidenceRecord:
    claim_id: str
    profile: str                    # e.g. "PRESENCE" | "IDENTITY" | "TRANSACTION"
    verdict: Verdict
    confidence: Confidence
    evidence: list[EvidenceItem]
    rationale: str                  # human-readable — Step 4 will replace the stub
    consent_status: ConsentStatus
    id: str = field(default_factory=_uid)
    created_at: str = field(default_factory=_now)
    # Optional: liveness challenge trace, when a CONTRADICTED path fired one.
    liveness: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim_id": self.claim_id,
            "profile": self.profile,
            "verdict": self.verdict.value,
            "confidence": self.confidence.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "rationale": self.rationale,
            "consent_status": self.consent_status.value,
            "liveness": self.liveness,
            "created_at": self.created_at,
        }


# --- Accumulated history helper ------------------------------------------

@dataclass
class HistoricalSite:
    """The site a subject has been consistently observed at, per accumulated
    evidence. Used by the REASSIGNMENT branch to detect a pattern break.
    """

    lat: float
    lon: float
    radius_m: float
    label: Optional[str] = None
    sample_count: int = 0           # how many prior consistent observations
    last_seen: Optional[str] = None
