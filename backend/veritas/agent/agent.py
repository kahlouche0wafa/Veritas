"""The VERITAS agent — three narrow jobs, none of them verdict-deciding.

    1. normalise  — free-text claim -> structured Claim
    2. select     — pick a verification profile for a claim
    3. explain    — write the rationale for a completed EvidenceRecord

If Groq is unreachable or a key rotation exhausts, each method falls back
to a deterministic path so the pipeline never breaks — the LLM is a
convenience layer, not a hard dependency.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import SETTINGS
from ..models import Claim, ClaimedSite, ConsentStatus, EvidenceRecord
from ..trace import NullTrace, TraceLogger
from .catalog import (
    SITES,
    SUBJECTS,
    catalog_summary_for_prompt,
    find_site_by_alias,
    find_subject_by_alias,
)
from .llm import GroqLLM
from .prompts import NORMALISE_SYSTEM, RATIONALE_SYSTEM


class ClaimNormalisationError(ValueError):
    """Raised when neither the LLM nor the deterministic fallback can pin
    the claim to a known subject + site.
    """


class NoSiteInClaim(ClaimNormalisationError):
    """Subject was resolved but no site was found in the claim.

    Carries the resolved subject so callers (e.g. the API layer) can fall
    back to the subject's historical site — the natural interpretation of
    site-less IDENTITY / TRANSACTION claims like 'verify Sara's identity
    on her registered SIM'.
    """

    def __init__(self, subject, text: str):
        super().__init__(
            f"no site in claim for subject={subject.subject_id if subject else None!r}"
        )
        self.subject = subject
        self.text = text


class Agent:
    def __init__(self, llm: Optional[GroqLLM] = None):
        self.llm = llm or GroqLLM(model=SETTINGS.groq_model)

    # ------------------------------------------------------------------
    # 1. NORMALISE
    # ------------------------------------------------------------------

    def normalise_claim(
        self,
        text: str,
        source: str = "manual",
        default_site: Optional[ClaimedSite] = None,
        trace: Optional[TraceLogger] = None,
    ) -> Claim:
        """Convert free text -> Claim, using the catalog to fill in
        anything the LLM must not invent (phone, coordinates).

        If the text names a subject but no site, and `default_site` is
        supplied, that site is used (the natural interpretation of
        site-less IDENTITY / TRANSACTION claims — 'somewhere plausible
        for this identity'). If `default_site` is None, NoSiteInClaim is
        raised carrying the resolved subject so callers can retry with
        the subject's historical site.
        """
        trace = trace or NullTrace()
        text = text.strip()
        now = datetime.now(timezone.utc)
        parsed = self._normalise_via_llm(text, now)
        if parsed is None:
            trace.add("normalise", "LLM unavailable or JSON parse failed — using deterministic keyword fallback.")
            parsed = self._normalise_via_fallback(text, now)
        else:
            trace.add(
                "normalise",
                f"LLM parsed free text into: subject_key={parsed.get('subject_key')!r}, "
                f"site_key={parsed.get('site_key')!r}, "
                f"window={(parsed.get('time_window') or {}).get('start_iso','?')[:19]}..{(parsed.get('time_window') or {}).get('end_iso','?')[:19]}.",
                {"parsed": parsed},
            )

        subject_key = parsed.get("subject_key")
        site_key = parsed.get("site_key")
        subject = SUBJECTS.get(subject_key) if subject_key else None
        site = SITES.get(site_key) if site_key else None

        if subject is None or site is None:
            # Last-ditch: keyword fallback (works even with no LLM at all).
            if subject is None:
                subject = find_subject_by_alias(text)
            if site is None:
                site = find_site_by_alias(text)

        if subject is None:
            raise ClaimNormalisationError(
                f"could not resolve subject from text: {text!r}"
            )

        # Site resolution: prefer text-derived, then explicit default, then error.
        if site is not None:
            claimed_site = ClaimedSite(
                lat=site.lat, lon=site.lon, radius_m=site.radius_m, label=site.label,
            )
        elif default_site is not None:
            claimed_site = default_site
        else:
            raise NoSiteInClaim(subject=subject, text=text)

        tw = parsed.get("time_window") or {}
        start_iso = tw.get("start_iso") or (now - timedelta(hours=4)).isoformat()
        end_iso = tw.get("end_iso") or (now + timedelta(hours=4)).isoformat()

        return Claim(
            subject_id=subject.subject_id,
            phone=subject.phone,
            assertion_text=parsed.get("assertion_text") or text,
            claimed_site=claimed_site,
            time_window_start=start_iso,
            time_window_end=end_iso,
            source=source,
            consent_status=ConsentStatus.ACTIVE,
        )

    def resolve_claim(
        self, text: str, storage, source: str = "manual",
        trace: Optional[TraceLogger] = None,
    ) -> Claim:
        """Convenience: normalise a free-text claim, and when no site is
        named, use the subject's computed historical site as the expected
        zone. This is the entry point the API layer should use.
        """
        trace = trace or NullTrace()
        try:
            return self.normalise_claim(text, source=source, trace=trace)
        except NoSiteInClaim as gap:
            trace.add(
                "normalise",
                f"No site named in the claim — falling back to subject's historical "
                f"site as the 'expected zone' for {gap.subject.subject_id}.",
                {"subject_id": gap.subject.subject_id},
            )
            hs = storage.compute_historical_site(gap.subject.subject_id)
            if hs is None:
                trace.add(
                    "normalise",
                    f"No history for subject {gap.subject.subject_id!r} either — "
                    "cannot resolve claim.",
                )
                raise ClaimNormalisationError(
                    f"no site in claim and no history for subject "
                    f"{gap.subject.subject_id!r}: {text!r}"
                ) from gap
            trace.history(
                f"Loaded {hs.sample_count} prior records for {gap.subject.subject_id}; "
                f"dominant site = {hs.label or 'unnamed'} at ({hs.lat:.4f},{hs.lon:.4f}).",
                {"sample_count": hs.sample_count, "label": hs.label},
            )
            fallback = ClaimedSite(
                lat=hs.lat, lon=hs.lon, radius_m=hs.radius_m,
                label=(hs.label or "expected zone") + " (from history)",
            )
            return self.normalise_claim(text, source=source, default_site=fallback, trace=trace)

    def _normalise_via_llm(self, text: str, now: datetime) -> Optional[dict]:
        if not self.llm.available:
            return None
        user = (
            f"now_utc: {now.isoformat()}\n\n"
            f"{catalog_summary_for_prompt()}\n\n"
            f"User claim (verbatim):\n\"\"\"\n{text}\n\"\"\""
        )
        data = self.llm.complete_json(NORMALISE_SYSTEM, user)
        return data

    def _normalise_via_fallback(self, text: str, now: datetime) -> dict:
        """Deterministic keyword-only normalisation — used when the LLM is
        unavailable so the API still works.
        """
        subject = find_subject_by_alias(text)
        site = find_site_by_alias(text)
        return {
            "subject_key": subject.subject_id if subject else None,
            "site_key": site.key if site else None,
            "assertion_text": text,
            "time_window": {
                "start_iso": (now - timedelta(hours=4)).isoformat(),
                "end_iso": (now + timedelta(hours=4)).isoformat(),
            },
            "confidence_notes": "resolved without LLM (keyword fallback)",
        }

    # ------------------------------------------------------------------
    # 2. SELECT PROFILE
    # ------------------------------------------------------------------

    def select_profile(self, claim: Claim, trace: Optional[TraceLogger] = None) -> str:
        """Return the verification profile name for this claim.

        Keyword matches are word-boundary safe — 'sar' does NOT match into
        'Sara', 'pay' does not match into 'payment history', etc. Every
        matched keyword is recorded in the trace so a mismatch is easy to
        spot when reviewing a run.
        """
        import re
        trace = trace or NullTrace()
        text = claim.assertion_text
        # Currency + transaction verbs, matched with word boundaries.
        txn_keywords = (
            "transfer", "transferred", "payment", "otp", "authorise", "authorize",
            "wire", "pay", "paying", "purchase", "purchased", "invoice",
            "sar", "aed", "usd", "eur", "gbp", "dollars", "euros", "riyals", "dirhams",
        )
        id_keywords = (
            "verify identity", "kyc", "sim swap", "account holder",
            "password reset", "confirm the identity", "confirm their identity",
            "new device", "unauthorised access", "unauthorized access",
        )
        def _match(word: str) -> bool:
            return re.search(r"\b" + re.escape(word) + r"\b", text, flags=re.IGNORECASE) is not None
        matched_txn = [w for w in txn_keywords if _match(w)]
        matched_id = [w for w in id_keywords if _match(w)]
        if matched_txn:
            trace.select_profile(
                f"Selected TRANSACTION — matched keyword(s): {', '.join(matched_txn)}.",
                {"profile": "TRANSACTION", "matched": matched_txn},
            )
            return "TRANSACTION"
        if matched_id:
            trace.select_profile(
                f"Selected IDENTITY — matched keyword(s): {', '.join(matched_id)}.",
                {"profile": "IDENTITY", "matched": matched_id},
            )
            return "IDENTITY"
        trace.select_profile(
            "Selected PRESENCE — claim asserts location and/or a time window.",
            {"profile": "PRESENCE"},
        )
        return "PRESENCE"

    # ------------------------------------------------------------------
    # 3. EXPLAIN
    # ------------------------------------------------------------------

    def write_rationale(self, claim: Claim, record: EvidenceRecord) -> str:
        """Write a human-readable rationale for a completed EvidenceRecord.

        The verdict, confidence, and matched rule tag are already set —
        this method never changes them. It only produces prose.
        """
        # Compact summary of the evidence the LLM should reason over.
        summary = _evidence_summary_for_rationale(claim, record)
        if self.llm.available:
            user = json.dumps(summary, indent=2)
            out = self.llm.complete_text(RATIONALE_SYSTEM, user)
            if out:
                return out.strip()
        # Deterministic fallback (same one Step 2's smoke test used).
        from ..presence import _fallback_rationale
        return _fallback_rationale(claim, record)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _evidence_summary_for_rationale(claim: Claim, record: EvidenceRecord) -> dict:
    """Small, prompt-friendly dict of the facts the rationale should quote."""
    by_signal = {item.signal_name: item.value for item in record.evidence}
    return {
        "claim": {
            "subject_id": claim.subject_id,
            "assertion_text": claim.assertion_text,
            "claimed_site_label": claim.claimed_site.label,
            "time_window": {
                "start": claim.time_window_start,
                "end": claim.time_window_end,
            },
        },
        "verdict": record.verdict.value,
        "confidence": record.confidence.value,
        "matched_rule": by_signal.get("verdict_rule_matched"),
        "signals": {
            "connectivity_status": (by_signal.get("connectivity") or {}).get("status"),
            "geofence_vs_claimed_site": by_signal.get("geofence_vs_claimed_site"),
            "geofence_vs_historical_site": by_signal.get("geofence_vs_historical_site"),
            "device_swap": by_signal.get("device_swap"),
            "tenure": by_signal.get("tenure"),
            "number_verification": "not queried — requires production OIDC flow",
        },
        "liveness": record.liveness,
    }


# ---------------------------------------------------------------------
# Improved liveness stub — used by presence.py so the CONTRADICTED path
# has a "sent -> replied at [time] from [zone]" trace per the brief.
# ---------------------------------------------------------------------

def build_liveness_stub(claim: Claim, evidence_items) -> dict:
    """Return a deterministic-but-plausible liveness challenge trace.

    Everything here is clearly labelled 'stub' and 'mock' — no SMS is sent,
    no real timing measured — but the shape mirrors what a production
    challenge would record so the demo tells the right story.
    """
    now = datetime.now(timezone.utc)
    responder_zone = "unknown"
    for item in evidence_items:
        if item.signal_name == "geofence_vs_claimed_site":
            v = item.value or {}
            if v.get("distance_m") is not None:
                km = v["distance_m"] / 1000.0
                responder_zone = (
                    f"same cell as observed device "
                    f"(~{km:.0f} km from claimed site '{v['claimed_site'].get('label')}')"
                )
            break
    return {
        "stub": True,
        "note": (
            "Prototype stub — no SMS actually delivered. Times and zone are "
            "deterministic mock data illustrating the CONTRADICTED escalation flow."
        ),
        "challenge_type": "sms_deep_link_tap",
        "sent_at": now.isoformat(),
        "replied_at": (now + timedelta(seconds=43)).isoformat(),
        "response_latency_seconds": 43,
        "responder_zone": responder_zone,
        "outcome": "responded_from_non_claimed_zone",
    }
