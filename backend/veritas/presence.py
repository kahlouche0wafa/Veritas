"""PRESENCE verification profile.

Given a claim ("subject X at site Y between t1 and t2"), gather the network
evidence that would prove or disprove it, and hand it to decide_verdict.

Two things wrap the deterministic engine here:
  * A TraceLogger records every step (gather, result, skip, geofence check,
    history lookup, verdict, explain) so the audit trail is visible in the
    UI without leaking into the verdict rules.
  * An evidence_plan (set of signal names) lets callers narrow the checks
    for low-risk claims. The plan is only about what to QUERY — the verdict
    logic itself never forks on the plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

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
from .trace import NullTrace, TraceLogger
from .verdict import decide_verdict

PROFILE = "PRESENCE"

# The full set of signals a maximum-rigour PRESENCE run gathers.
# Individual signals can be dropped via evidence_plan (e.g. low-value TXN).
ALL_SIGNALS = (
    "connectivity",
    "geofence_vs_claimed_site",
    "geofence_vs_historical_site",
    "device_swap",
    "tenure",
    "number_verification",
)


# Human-readable reason each signal is queried. Attached to the
# EvidenceItem.why so the frontend can show per-row rationale without
# a second source of truth.
WHY_QUERIED = {
    "connectivity":
        "Establishes whether the device is reachable at all — no signal is meaningful if it isn't.",
    "geofence_vs_claimed_site":
        "Directly tests the claim: is the device inside the site the claim asserts?",
    "geofence_vs_historical_site":
        "Detects pattern breaks — a device match against history distinguishes REASSIGNMENT from CONTRADICTED.",
    "device_swap":
        "A recent SIM/device swap degrades identity trust regardless of location outcome.",
    "tenure":
        "KYC anchor — long-tenured contracts are less likely to be freshly recycled numbers.",
    "number_verification":
        "Not queried in the prototype — requires a production 3-legged OIDC flow.",
}

WHY_SKIPPED = {
    "geofence_vs_historical_site":
        "No accumulated history for this subject yet — nothing to compare against.",
    "device_swap":
        "Trimmed from the plan (low-risk claim — location + connectivity is sufficient).",
    "tenure":
        "Trimmed from the plan (low-risk claim — carrier KYC is not decision-relevant).",
    "number_verification":
        "Requires production OIDC flow, out of prototype scope.",
}


def _stub_liveness_challenge(claim: Claim, evidence_items=None) -> dict:
    """Simulated liveness challenge — a real SMS/link-tap integration is
    out of scope for the prototype. We log a deterministic, clearly-labeled
    stub so the CONTRADICTED path is visible end-to-end without pretending
    to have infrastructure we don't.
    """
    try:
        from .agent import build_liveness_stub  # local import to avoid cycles
        return build_liveness_stub(claim, evidence_items or [])
    except Exception:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "stub": True,
            "note": "Liveness challenge NOT actually sent — prototype stub.",
            "sent_at": now, "replied_at": None,
            "responder_zone": None, "outcome": "no_response_recorded",
        }


def run_presence_verification(
    claim: Claim,
    client: CamaraClient,
    historical_site: Optional[HistoricalSite] = None,
    rationale_writer=None,
    trace: Optional[TraceLogger] = None,
    evidence_plan: Optional[Iterable[str]] = None,
    profile_label: str = PROFILE,
    defer_rationale: bool = False,
) -> EvidenceRecord:
    """Execute the PRESENCE evidence-gathering pipeline and return an
    EvidenceRecord (with the deterministic verdict already set).

    Callers (identity / transaction profiles) reuse this with different
    evidence_plans and profile labels. See profiles.py.
    """
    trace = trace or NullTrace()
    plan = set(evidence_plan) if evidence_plan is not None else set(ALL_SIGNALS)

    evidence: list[EvidenceItem] = []

    # ---- Consent short-circuit ---------------------------------------
    if claim.consent_status != ConsentStatus.ACTIVE:
        trace.skip(
            "Consent gate closed — refusing to query any network signal.",
            {"consent_status": claim.consent_status.value},
        )
        evidence.append(EvidenceItem(
            signal_name="consent_check",
            value=claim.consent_status.value,
            evidence_class=EvidenceClass.CARRIER_COMPUTED,
            note="no network signals queried — consent gate closed",
            why="Consent must be ACTIVE before any signal is queried.",
        ))
        record = EvidenceRecord(
            claim_id=claim.id,
            profile=profile_label,
            verdict=Verdict.NO_VERDICT_NO_CONSENT,
            confidence=Confidence.HIGH,
            evidence=evidence,
            rationale=(
                "No verdict produced. Consent for the subject is not ACTIVE, "
                "so VERITAS did not query any network signals."
            ),
            consent_status=claim.consent_status,
        )
        trace.verdict("NO_VERDICT_NO_CONSENT — consent gate closed.", {
            "verdict": record.verdict.value, "confidence": record.confidence.value,
        })
        trace.complete("Record assembled; no network signals were sent.")
        return record

    # ---- Plan announcement ------------------------------------------
    active_signals = [s for s in ALL_SIGNALS if s in plan]
    if historical_site is None and "geofence_vs_historical_site" in active_signals:
        active_signals.remove("geofence_vs_historical_site")
    trace.plan_evidence(
        f"Evidence plan for {profile_label}: {', '.join(active_signals)}. "
        f"Skipped: {', '.join([s for s in ALL_SIGNALS if s not in active_signals]) or 'nothing'}.",
        {"plan": active_signals, "skipped": [s for s in ALL_SIGNALS if s not in active_signals]},
    )

    # ---- 1. connectivity --------------------------------------------
    connectivity = None
    if "connectivity" in plan:
        trace.gather(
            f"Calling getConnectivityStatus for {claim.phone} — establish reachability first.",
            {"api": "getConnectivityStatus", "phone": claim.phone},
        )
        connectivity = client.get_connectivity(claim.phone)
        trace.result(
            f"connectivityStatus = {connectivity.status!r}"
            + (f" (error: {connectivity.error})" if connectivity.error else ""),
            {"status": connectivity.status, "ok": connectivity.ok},
        )
        evidence.append(EvidenceItem(
            signal_name="connectivity",
            value={"status": connectivity.status, "ok": connectivity.ok, "error": connectivity.error},
            evidence_class=EvidenceClass.CURRENT,
            why=WHY_QUERIED["connectivity"],
        ))
    else:
        trace.skip("connectivity not queried per plan.")
        evidence.append(EvidenceItem(
            signal_name="connectivity", value=None,
            evidence_class=EvidenceClass.CURRENT,
            note="skipped per evidence plan",
            why=WHY_SKIPPED.get("connectivity", "trimmed per plan"),
        ))

    # ---- 2. geofence vs claimed site --------------------------------
    claim_geofence = None
    if "geofence_vs_claimed_site" in plan:
        trace.gather(
            f"Calling retrieveLocation for {claim.phone} to test the claimed site "
            f"({claim.claimed_site.label or 'unnamed'} @ "
            f"{claim.claimed_site.lat:.4f},{claim.claimed_site.lon:.4f}, "
            f"radius {claim.claimed_site.radius_m}m).",
            {"api": "retrieveLocation", "phone": claim.phone,
             "site": {"lat": claim.claimed_site.lat, "lon": claim.claimed_site.lon,
                      "radius_m": claim.claimed_site.radius_m,
                      "label": claim.claimed_site.label}},
        )
        claim_geofence = client.check_geofence(
            claim.phone, claim.claimed_site.lat,
            claim.claimed_site.lon, claim.claimed_site.radius_m,
        )
        if claim_geofence.ok:
            trace.result(
                f"Device at ({claim_geofence.location.latitude:.4f},"
                f"{claim_geofence.location.longitude:.4f}), "
                f"radius {claim_geofence.location.radius_m}m "
                f"(reported by simulator).",
                {"lat": claim_geofence.location.latitude,
                 "lon": claim_geofence.location.longitude,
                 "radius_m": claim_geofence.location.radius_m},
            )
            trace.geofence_check(
                f"Haversine distance {claim_geofence.distance_m}m vs effective radius "
                f"{claim_geofence.effective_radius_m}m → "
                f"{'INSIDE' if claim_geofence.inside else 'OUTSIDE'}. "
                f"(Note: VERITAS computes this locally; verifyLocation is unreliable in the simulator.)",
                {"distance_m": claim_geofence.distance_m,
                 "effective_radius_m": claim_geofence.effective_radius_m,
                 "inside": claim_geofence.inside},
            )
        else:
            trace.result(
                f"retrieveLocation failed: {claim_geofence.error}",
                {"error": claim_geofence.error},
            )
        evidence.append(EvidenceItem(
            signal_name="geofence_vs_claimed_site",
            value={
                "ok": claim_geofence.ok, "inside": claim_geofence.inside,
                "distance_m": claim_geofence.distance_m,
                "effective_radius_m": claim_geofence.effective_radius_m,
                "claimed_site": {
                    "lat": claim.claimed_site.lat, "lon": claim.claimed_site.lon,
                    "radius_m": claim.claimed_site.radius_m,
                    "label": claim.claimed_site.label,
                },
                "error": claim_geofence.error,
            },
            evidence_class=EvidenceClass.CURRENT,
            why=WHY_QUERIED["geofence_vs_claimed_site"],
        ))

    # ---- 3. geofence vs historical site -----------------------------
    historical_geofence = None
    if historical_site is not None and "geofence_vs_historical_site" in plan:
        trace.history(
            f"History for {claim.subject_id}: dominant site "
            f"'{historical_site.label or 'unnamed'}' at "
            f"({historical_site.lat:.4f},{historical_site.lon:.4f}) "
            f"from {historical_site.sample_count} prior CONSISTENT records.",
            {"label": historical_site.label,
             "sample_count": historical_site.sample_count,
             "last_seen": historical_site.last_seen},
        )
        trace.gather(
            f"Calling retrieveLocation again vs historical site to detect pattern break "
            f"(REASSIGNMENT vs CONTRADICTED disambiguation).",
            {"api": "retrieveLocation", "purpose": "historical_geofence"},
        )
        historical_geofence = client.check_geofence(
            claim.phone, historical_site.lat,
            historical_site.lon, historical_site.radius_m,
        )
        if historical_geofence.ok:
            trace.geofence_check(
                f"Distance from historical site '{historical_site.label}': "
                f"{historical_geofence.distance_m}m → "
                f"{'INSIDE' if historical_geofence.inside else 'OUTSIDE'}.",
                {"distance_m": historical_geofence.distance_m,
                 "inside": historical_geofence.inside},
            )
        else:
            trace.result(f"historical geofence failed: {historical_geofence.error}")
        evidence.append(EvidenceItem(
            signal_name="geofence_vs_historical_site",
            value={
                "ok": historical_geofence.ok, "inside": historical_geofence.inside,
                "distance_m": historical_geofence.distance_m,
                "effective_radius_m": historical_geofence.effective_radius_m,
                "historical_site": {
                    "lat": historical_site.lat, "lon": historical_site.lon,
                    "radius_m": historical_site.radius_m,
                    "label": historical_site.label,
                    "sample_count": historical_site.sample_count,
                    "last_seen": historical_site.last_seen,
                },
            },
            evidence_class=EvidenceClass.ACCUMULATED,
            why=WHY_QUERIED["geofence_vs_historical_site"],
        ))
    elif "geofence_vs_historical_site" in plan and historical_site is None:
        trace.skip(
            "No accumulated history for this subject — REASSIGNMENT branch cannot fire.",
            {"reason": "no_history"},
        )

    # ---- 4. device swap ---------------------------------------------
    device_swap = None
    if "device_swap" in plan:
        trace.gather(
            f"Calling checkDeviceSwap for {claim.phone} — identity-trust modifier.",
            {"api": "checkDeviceSwap", "phone": claim.phone},
        )
        device_swap = client.check_device_swap(claim.phone)
        trace.result(
            f"swapped = {device_swap.swapped}"
            + (f" (error: {device_swap.error})" if device_swap.error else ""),
            {"swapped": device_swap.swapped, "ok": device_swap.ok},
        )
        evidence.append(EvidenceItem(
            signal_name="device_swap",
            value={"ok": device_swap.ok, "swapped": device_swap.swapped, "error": device_swap.error},
            evidence_class=EvidenceClass.CARRIER_COMPUTED,
            why=WHY_QUERIED["device_swap"],
        ))
    else:
        trace.skip(
            "device_swap trimmed from plan — not decision-relevant for this risk level.",
        )
        evidence.append(EvidenceItem(
            signal_name="device_swap", value=None,
            evidence_class=EvidenceClass.CARRIER_COMPUTED,
            note="skipped per evidence plan",
            why=WHY_SKIPPED["device_swap"],
        ))

    # ---- 5. tenure ---------------------------------------------------
    tenure = None
    if "tenure" in plan:
        trace.gather(
            f"Calling checkTenure for {claim.phone} — KYC anchor.",
            {"api": "checkTenure", "phone": claim.phone},
        )
        tenure = client.get_tenure(claim.phone)
        trace.result(
            f"tenureDateCheck={tenure.tenure_date_check} contract={tenure.contract_type!r}",
            {"tenure_date_check": tenure.tenure_date_check,
             "contract_type": tenure.contract_type},
        )
        evidence.append(EvidenceItem(
            signal_name="tenure",
            value={
                "ok": tenure.ok, "tenure_date_check": tenure.tenure_date_check,
                "contract_type": tenure.contract_type, "error": tenure.error,
            },
            evidence_class=EvidenceClass.CARRIER_COMPUTED,
            why=WHY_QUERIED["tenure"],
        ))
    else:
        trace.skip("tenure trimmed from plan.")
        evidence.append(EvidenceItem(
            signal_name="tenure", value=None,
            evidence_class=EvidenceClass.CARRIER_COMPUTED,
            note="skipped per evidence plan",
            why=WHY_SKIPPED["tenure"],
        ))

    # ---- 6. number verification (always skipped in prototype) -------
    trace.skip(
        "number_verification: not queried — requires production OIDC flow.",
        {"reason": "requires_production_oidc"},
    )
    evidence.append(EvidenceItem(
        signal_name="number_verification",
        value=None,
        evidence_class=EvidenceClass.CARRIER_COMPUTED,
        note="not queried — requires production OIDC flow",
        why=WHY_SKIPPED["number_verification"],
    ))

    # ---- Decide ------------------------------------------------------
    # decide_verdict is pure. It takes signals it needs; when a plan
    # trimmed a signal, we pass a synthesized "ok=False" so the rule
    # engine sees an unknown, not a false claim.
    from .camara.base import ConnectivityResult, SwapResult, TenureResult
    verdict, confidence, short_reason = decide_verdict(
        claim=claim,
        claim_geofence=claim_geofence,
        historical_geofence=historical_geofence,
        historical_site=historical_site,
        connectivity=connectivity or ConnectivityResult(ok=False, error="not queried"),
        device_swap=device_swap or SwapResult(ok=False, swapped=None, error="not queried"),
        tenure=tenure or TenureResult(ok=False, error="not queried"),
    )
    trace.verdict(
        f"decide_verdict() returned {verdict.value} "
        f"(confidence {confidence.value}). Matched rule: '{short_reason}'.",
        {"verdict": verdict.value, "confidence": confidence.value,
         "rule": short_reason},
    )
    evidence.append(EvidenceItem(
        signal_name="verdict_rule_matched",
        value=short_reason,
        evidence_class=EvidenceClass.CARRIER_COMPUTED,
        note="stable rule tag from decide_verdict()",
        why="Which branch of decide_verdict() fired. Deterministic; not an LLM decision.",
    ))

    # ---- Liveness (CONTRADICTED only) -------------------------------
    liveness = None
    if verdict == Verdict.CONTRADICTED:
        trace.add(
            "gather",
            "Verdict is CONTRADICTED → firing prototype liveness challenge stub.",
            {"escalation": "liveness"},
        )
        liveness = _stub_liveness_challenge(claim, evidence)
        trace.result(
            f"liveness stub: outcome={liveness['outcome']}, "
            f"zone={liveness.get('responder_zone')} (clearly labelled a stub).",
            {"outcome": liveness["outcome"]},
        )

    record = EvidenceRecord(
        claim_id=claim.id, profile=profile_label,
        verdict=verdict, confidence=confidence,
        evidence=evidence, rationale="",
        consent_status=claim.consent_status, liveness=liveness,
    )

    # ---- Rationale (LLM) --------------------------------------------
    # Profile wrappers (IDENTITY / TRANSACTION) pass defer_rationale=True
    # because they add profile-specific evidence AFTER this call and want
    # the LLM to see the final list — so skip emitting a misleading
    # 'No LLM available' trace step here and leave rationale empty.
    if defer_rationale:
        return record
    if rationale_writer is not None:
        trace.explain(
            "Asking LLM to write a human-readable rationale over the completed "
            "evidence record. The verdict is already set; the LLM cannot change it.",
        )
        try:
            record.rationale = rationale_writer(claim, record)
            trace.result("Rationale written.",
                         {"length_chars": len(record.rationale)})
        except Exception as exc:
            record.rationale = _fallback_rationale(claim, record) + (
                f"\n\n(note: rationale writer failed: {exc})"
            )
            trace.result(
                f"LLM rationale failed ({exc}) — used deterministic fallback.",
            )
    else:
        record.rationale = _fallback_rationale(claim, record)
        trace.explain("No LLM available — used deterministic fallback rationale.")

    return record


def _fallback_rationale(claim: Claim, record: EvidenceRecord) -> str:
    parts = [f"Verdict: {record.verdict.value} (confidence: {record.confidence.value})."]
    for item in record.evidence:
        if item.signal_name == "geofence_vs_claimed_site" and item.value:
            v = item.value
            if v.get("ok") and v.get("inside") is not None:
                parts.append(
                    f"Device was {'inside' if v['inside'] else 'outside'} the claimed "
                    f"site ({v['claimed_site'].get('label') or 'unnamed'}), "
                    f"distance {v['distance_m']}m vs radius {v['effective_radius_m']}m."
                )
            else:
                parts.append(
                    f"Geofence check against the claimed site could not run: "
                    f"{v.get('error') or 'no result'}."
                )
        elif item.signal_name == "geofence_vs_historical_site" and item.value and item.value.get("ok"):
            v = item.value
            parts.append(
                f"For reference, the subject's historical site "
                f"({v['historical_site'].get('label') or 'unnamed'}) is "
                f"{'still matching' if v['inside'] else 'no longer matching'} — "
                f"distance {v['distance_m']}m."
            )
        elif item.signal_name == "connectivity" and item.value:
            parts.append(f"Device connectivity: {item.value.get('status')}.")
        elif item.signal_name == "device_swap" and item.value:
            parts.append(
                "Recent device swap detected." if item.value.get("swapped")
                else "No recent device swap."
            )
    if record.liveness is not None:
        parts.append(
            "A liveness challenge was queued (prototype stub — not actually delivered)."
        )
    return " ".join(parts)
