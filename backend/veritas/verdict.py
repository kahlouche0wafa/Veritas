"""Deterministic verdict core.

decide_verdict is the ONLY place a verdict is produced. It is pure Python —
no LLM, no network calls, no hidden state — so we can point at it, explain
it to a judge in one sentence, and audit it in a code review.

The one-sentence pitch: given a claim, the network evidence gathered for
that claim, and the subject's accumulated history, pick one of four
verdicts by rule; the LLM never decides compliance.

Signals expected in `evidence` (a dict of already-queried CAMARA results,
each an object from veritas.camara):

    claim_geofence      GeofenceResult   — device vs the claim's stated site
    historical_geofence GeofenceResult | None  — device vs subject's prior site
    connectivity        ConnectivityResult
    device_swap         SwapResult
    tenure              TenureResult

`historical_geofence` may be None when we have no accumulated history for
the subject — in that case REASSIGNMENT cannot fire.
"""

from __future__ import annotations

from typing import Optional

from .camara.base import (
    ConnectivityResult,
    GeofenceResult,
    SwapResult,
    TenureResult,
)
from .models import Claim, Confidence, ConsentStatus, HistoricalSite, Verdict


def _same_site(
    site_lat: float, site_lon: float, historical: HistoricalSite, tolerance_m: float = 250.0
) -> bool:
    """Are the claimed site and the historical site 'the same place'?"""
    from .camara.base import haversine_m
    return haversine_m(site_lat, site_lon, historical.lat, historical.lon) <= tolerance_m


def decide_verdict(
    claim: Claim,
    claim_geofence: GeofenceResult,
    historical_geofence: Optional[GeofenceResult],
    historical_site: Optional[HistoricalSite],
    connectivity: ConnectivityResult,
    device_swap: SwapResult,
    tenure: TenureResult,
) -> tuple[Verdict, Confidence, str]:
    """Return (verdict, confidence, short_reason).

    `short_reason` is a machine-terse tag ("claim_site_match_no_swap",
    "unreachable", "site_mismatch_no_history") — a stable identifier the
    UI/logs can key off. The human-readable rationale is written later by
    the LLM (Step 4), using the full evidence record.
    """

    # ---- 1. Consent gate ------------------------------------------------
    # If consent isn't active we don't produce a verdict at all. Refusing
    # here is the whole point — the pitch is that VERITAS never reaches for
    # data it isn't entitled to, and that refusal is auditable.
    if claim.consent_status != ConsentStatus.ACTIVE:
        return (
            Verdict.NO_VERDICT_NO_CONSENT,
            Confidence.HIGH,
            "consent_not_active",
        )

    # ---- 2. INSUFFICIENT: can't see the device -------------------------
    # Either the device is offline, or the location signal failed. In both
    # cases we decline to guess — INSUFFICIENT is a first-class verdict,
    # not a fallback.
    if not connectivity.ok or not connectivity.reachable:
        return (
            Verdict.INSUFFICIENT,
            Confidence.LOW,
            "device_unreachable",
        )
    if not claim_geofence.ok or claim_geofence.inside is None:
        return (
            Verdict.INSUFFICIENT,
            Confidence.LOW,
            "location_signal_unavailable",
        )

    # ---- 3. Geofence match against the claimed site --------------------
    if claim_geofence.inside:
        # The claim's stated site matches where the device actually is.
        # Whether that's CONSISTENT or REASSIGNMENT depends on history.

        if historical_site is not None and not _same_site(
            claim.claimed_site.lat, claim.claimed_site.lon, historical_site
        ):
            # The subject has been consistently observed at a *different*
            # site historically, but today's claim declares this new site
            # AND the device is genuinely there. That's a pattern break
            # the claim itself admits — REASSIGNMENT, not a lie.
            conf = Confidence.MEDIUM
            if device_swap.ok and device_swap.swapped:
                # A recent SIM/device swap dents our identity confidence
                # even though the location story is coherent.
                conf = Confidence.LOW
            return (
                Verdict.REASSIGNMENT,
                conf,
                "claim_site_matches_but_differs_from_history",
            )

        # No history, or the claimed site is the same site the subject
        # has always been at — this is a straightforward CONSISTENT.
        conf = Confidence.HIGH
        short = "claim_site_match_no_swap"
        if device_swap.ok and device_swap.swapped:
            # Device swap means we can't be as sure the SIM's owner is the
            # person we think — location is consistent but identity trust
            # is degraded.
            conf = Confidence.MEDIUM
            short = "claim_site_match_recent_swap"
        return (Verdict.CONSISTENT, conf, short)

    # ---- 4. Geofence mismatch -----------------------------------------
    # The device is genuinely not at the site the claim asserts.
    # CONTRADICTED. The presence orchestrator (Step 4) will fire a
    # liveness challenge before finalizing this — but the verdict itself
    # is already CONTRADICTED here; the challenge only feeds into
    # rationale and downstream actions, not into the rule outcome.
    conf = Confidence.HIGH
    short = "claim_site_mismatch"
    if device_swap.ok and device_swap.swapped:
        # Recent device swap + wrong location: strongly suspicious, but
        # the swap also explains why our identity linkage is weaker, so
        # we mark this MEDIUM rather than HIGH.
        conf = Confidence.MEDIUM
        short = "claim_site_mismatch_recent_swap"
    return (Verdict.CONTRADICTED, conf, short)
