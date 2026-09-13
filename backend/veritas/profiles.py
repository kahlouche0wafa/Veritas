"""Verification profile registry and thin non-PRESENCE profiles.

Each profile is a different *evidence plan* over the same PRESENCE
pipeline. The verdict rules never fork — only the signal-selection list
and any profile-specific extras (a friction action / OTP stub for
TRANSACTION) change.

Profiles + their evidence plans:

    PRESENCE                — full signal set
    IDENTITY                — same minus tenure? kept full for prototype
    TRANSACTION (default)   — full + OTP stub
    TRANSACTION (low-risk)  — location + connectivity only + OTP stub
                              (the 'minimum sufficient evidence' principle)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from .camara.base import CamaraClient
from .models import (
    Claim,
    Confidence,
    ConsentStatus,
    EvidenceClass,
    EvidenceItem,
    EvidenceRecord,
    HistoricalSite,
    Verdict,
)
from .presence import ALL_SIGNALS, run_presence_verification
from .trace import NullTrace, TraceLogger

PROFILE_PRESENCE = "PRESENCE"
PROFILE_IDENTITY = "IDENTITY"
PROFILE_TRANSACTION = "TRANSACTION"

ALL_PROFILES = (PROFILE_PRESENCE, PROFILE_IDENTITY, PROFILE_TRANSACTION)


# ---------------------------------------------------------------------
# Risk scoring for TRANSACTION — "minimum sufficient evidence"
# ---------------------------------------------------------------------

# Threshold: at or below this amount (in any currency), TRANSACTION uses
# a trimmed evidence plan (location + connectivity only + OTP stub).
LOW_RISK_AMOUNT_THRESHOLD = 1_000.0

_AMOUNT_RE = re.compile(
    r"""(?ix)
    (?<![A-Z0-9])              # not part of a larger token
    (?P<amount>\d[\d,\.]*)     # digits, optional group separators / decimal
    \s*
    (?P<unit>k|m)?             # optional k/m multiplier
    \s*
    (?:sar|aed|usd|eur|gbp|dollars?|euros?|riyals?|dirhams?|pounds?)?  # optional currency word
    """,
)


def infer_amount(text: str) -> Optional[float]:
    """Extract the largest currency amount mentioned in the text.
    Returns None if no amount is found. Recognises 500 SAR, $50k, 120,000 EUR, etc.
    """
    if not text:
        return None
    best: Optional[float] = None
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group("amount").replace(",", "")
        try:
            n = float(raw)
        except ValueError:
            continue
        unit = (m.group("unit") or "").lower()
        if unit == "k":
            n *= 1_000
        elif unit == "m":
            n *= 1_000_000
        if best is None or n > best:
            best = n
    return best


def _transaction_plan(text: str, trace: TraceLogger) -> tuple[list[str], str]:
    """Pick the evidence plan for a TRANSACTION claim based on inferred amount.
    Returns (plan, human_reason)."""
    amount = infer_amount(text)
    if amount is None:
        reason = "no amount detected — using full evidence plan (high-caution default)."
        trace.plan_evidence(f"TRANSACTION risk: unknown — {reason}",
                            {"inferred_amount": None})
        return list(ALL_SIGNALS), reason
    if amount <= LOW_RISK_AMOUNT_THRESHOLD:
        reason = (
            f"amount {amount:,.0f} detected ≤ {LOW_RISK_AMOUNT_THRESHOLD:,.0f} threshold — "
            "low-risk, trimming to connectivity + location only."
        )
        trace.plan_evidence(
            f"TRANSACTION risk: LOW — {reason}",
            {"inferred_amount": amount, "risk": "low"},
        )
        return ["connectivity", "geofence_vs_claimed_site", "geofence_vs_historical_site"], reason
    reason = (
        f"amount {amount:,.0f} detected > {LOW_RISK_AMOUNT_THRESHOLD:,.0f} threshold — "
        "high-risk, using full evidence plan."
    )
    trace.plan_evidence(
        f"TRANSACTION risk: HIGH — {reason}",
        {"inferred_amount": amount, "risk": "high"},
    )
    return list(ALL_SIGNALS), reason


# ---------------------------------------------------------------------
# IDENTITY
# ---------------------------------------------------------------------

def run_identity_verification(
    claim: Claim,
    client: CamaraClient,
    historical_site: Optional[HistoricalSite] = None,
    rationale_writer: Optional[Callable] = None,
    trace: Optional[TraceLogger] = None,
) -> EvidenceRecord:
    trace = trace or NullTrace()
    # Defense in depth: even if called directly, the consent gate blocks.
    if claim.consent_status != ConsentStatus.ACTIVE:
        return _consent_denied_record(claim, PROFILE_IDENTITY, trace)
    trace.plan_evidence(
        "IDENTITY plan: full signal set — identity checks always want swap + tenure.",
        {"plan": list(ALL_SIGNALS)},
    )
    record = run_presence_verification(
        claim, client,
        historical_site=historical_site,
        rationale_writer=None,
        trace=trace,
        evidence_plan=None,
        profile_label=PROFILE_IDENTITY,
        defer_rationale=True,
    )
    record.evidence.append(EvidenceItem(
        signal_name="friction_action",
        value=None,
        evidence_class=EvidenceClass.CARRIER_COMPUTED,
        note="no friction action triggered (identity check is passive)",
        why="Identity verification observes signals; it does not challenge the user.",
    ))
    _write_rationale(claim, record, rationale_writer, trace)
    trace.complete(f"Evidence record assembled for IDENTITY. record_id={record.id[:8]}…")
    return record


# ---------------------------------------------------------------------
# TRANSACTION
# ---------------------------------------------------------------------

def run_transaction_verification(
    claim: Claim,
    client: CamaraClient,
    historical_site: Optional[HistoricalSite] = None,
    rationale_writer: Optional[Callable] = None,
    trace: Optional[TraceLogger] = None,
) -> EvidenceRecord:
    trace = trace or NullTrace()
    # Defense in depth: no OTP challenge fires if consent isn't ACTIVE.
    if claim.consent_status != ConsentStatus.ACTIVE:
        return _consent_denied_record(claim, PROFILE_TRANSACTION, trace)
    plan, _reason = _transaction_plan(claim.assertion_text, trace)
    record = run_presence_verification(
        claim, client,
        historical_site=historical_site,
        rationale_writer=None,
        trace=trace,
        evidence_plan=plan,
        profile_label=PROFILE_TRANSACTION,
        defer_rationale=True,
    )
    otp = _stub_otp_challenge(claim)
    trace.add(
        "gather",
        "TRANSACTION always fires the friction/OTP challenge stub — the point of OTP "
        "is a challenge *before* authorising, not a response to a verdict.",
        {"friction": "sms_otp"},
    )
    trace.result(
        f"OTP stub: {otp['challenge_type']} → {otp['outcome']} (clearly labelled a stub).",
        {"outcome": otp["outcome"]},
    )
    record.evidence.append(EvidenceItem(
        signal_name="friction_action",
        value=otp,
        evidence_class=EvidenceClass.CARRIER_COMPUTED,
        note="stubbed OTP challenge queued for transaction authorisation",
        why="Transactions require an OTP challenge before authorising, regardless of verdict.",
    ))
    _write_rationale(claim, record, rationale_writer, trace)
    trace.complete(f"Evidence record assembled for TRANSACTION. record_id={record.id[:8]}…")
    return record


# ---------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------

def run_verification(
    profile: str,
    claim: Claim,
    client: CamaraClient,
    historical_site: Optional[HistoricalSite] = None,
    rationale_writer: Optional[Callable] = None,
    trace: Optional[TraceLogger] = None,
) -> EvidenceRecord:
    """One entry point the API layer calls — routes to the right profile.

    The consent gate is the FIRST thing that happens here — before any
    profile-specific work, before any friction action, before any network
    query. This is the single-source-of-truth for the pitch's "zero
    action on missing consent" claim. Defense in depth: the profile
    handlers and presence.py both re-check, so removing this gate would
    still keep network calls guarded.
    """
    trace = trace or NullTrace()
    p = profile.upper()
    if claim.consent_status != ConsentStatus.ACTIVE:
        return _consent_denied_record(claim, p, trace)
    if p == PROFILE_PRESENCE:
        record = run_presence_verification(
            claim, client,
            historical_site=historical_site,
            rationale_writer=rationale_writer,
            trace=trace,
        )
        trace.complete(f"Evidence record assembled for PRESENCE. record_id={record.id[:8]}…")
        return record
    if p == PROFILE_IDENTITY:
        return run_identity_verification(
            claim, client,
            historical_site=historical_site,
            rationale_writer=rationale_writer,
            trace=trace,
        )
    if p == PROFILE_TRANSACTION:
        return run_transaction_verification(
            claim, client,
            historical_site=historical_site,
            rationale_writer=rationale_writer,
            trace=trace,
        )
    raise ValueError(f"unknown profile: {profile!r} (known: {ALL_PROFILES})")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _consent_denied_record(
    claim: Claim, profile: str, trace: TraceLogger,
) -> EvidenceRecord:
    """The single, profile-agnostic response when consent is not ACTIVE.

    Fires zero CAMARA calls, zero LLM calls, zero friction actions, zero
    OTP stubs — the whole pipeline is refused. Same shape regardless of
    which profile was requested, because "no verdict" is not a verdict
    IDENTITY / TRANSACTION can decorate.
    """
    trace.skip(
        "Consent gate closed BEFORE profile dispatch — no CAMARA queries, "
        "no LLM calls, no friction action will be issued.",
        {"consent_status": claim.consent_status.value},
    )
    evidence = [EvidenceItem(
        signal_name="consent_check",
        value=claim.consent_status.value,
        evidence_class=EvidenceClass.CARRIER_COMPUTED,
        note="no network signals queried — consent gate closed",
        why="Consent must be ACTIVE before any signal is queried or action taken.",
    )]
    record = EvidenceRecord(
        claim_id=claim.id,
        profile=profile,
        verdict=Verdict.NO_VERDICT_NO_CONSENT,
        confidence=Confidence.HIGH,
        evidence=evidence,
        rationale=(
            "No verdict produced. Consent for the subject is not ACTIVE, so "
            "VERITAS refused to query any network signal or trigger any "
            "friction action regardless of the requested profile."
        ),
        consent_status=claim.consent_status,
    )
    trace.verdict(
        "NO_VERDICT_NO_CONSENT — pipeline halted before any evidence "
        "gathering or friction action.",
        {"verdict": record.verdict.value, "confidence": record.confidence.value},
    )
    trace.complete(f"Consent-denied record assembled. record_id={record.id[:8]}…")
    return record


def _stub_otp_challenge(claim: Claim) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "stub": True,
        "note": (
            "Prototype stub — no OTP actually sent. Shape mirrors what a "
            "production OTP challenge trace would record."
        ),
        "challenge_type": "sms_otp",
        "sent_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "outcome": "challenge_queued_awaiting_response",
    }


def _write_rationale(
    claim: Claim, record: EvidenceRecord, rationale_writer, trace: TraceLogger
) -> None:
    if rationale_writer is None:
        from .presence import _fallback_rationale
        record.rationale = _fallback_rationale(claim, record)
        trace.explain("Deterministic fallback rationale used (no LLM writer supplied).")
        return
    trace.explain(
        "Re-asking LLM to write the rationale so it sees the profile's extras.",
    )
    try:
        record.rationale = rationale_writer(claim, record)
        trace.result("Rationale written.", {"length_chars": len(record.rationale)})
    except Exception as exc:
        from .presence import _fallback_rationale
        record.rationale = _fallback_rationale(claim, record) + (
            f"\n\n(note: rationale writer failed: {exc})"
        )
        trace.result(f"LLM rationale failed ({exc}) — used deterministic fallback.")
