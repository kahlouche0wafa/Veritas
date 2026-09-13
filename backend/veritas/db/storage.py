"""Storage layer for claims + evidence records.

Two backends behind one interface:

  * SupabaseStorage — talks to a real Supabase project (schema.sql applied).
  * InMemoryStorage — process-local dict store, so the API and demo run
                      end-to-end even if Supabase isn't configured yet.

The factory picks Supabase when SUPABASE_URL + SUPABASE_SERVICE_KEY are set
and the `supabase` package is importable; it falls back to in-memory
otherwise. This keeps the prototype demo bulletproof.

Compute helpers (compute_historical_site) live here rather than in the
verdict module because they're read-model concerns — the deterministic
verdict engine stays free of DB knowledge.
"""

from __future__ import annotations

import statistics
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import SETTINGS
from ..models import (
    Claim,
    ClaimedSite,
    ConsentStatus,
    EvidenceItem,
    EvidenceRecord,
    HistoricalSite,
    Verdict,
    Confidence,
    EvidenceClass,
)


# ---------- interface -----------------------------------------------------

class Storage(ABC):
    backend_name: str = "abstract"

    @abstractmethod
    def save_claim(self, claim: Claim) -> None: ...

    @abstractmethod
    def save_evidence_record(self, record: EvidenceRecord, is_seed: bool = False) -> None: ...

    @abstractmethod
    def get_claim(self, claim_id: str) -> Optional[Claim]: ...

    @abstractmethod
    def get_evidence_record_for_claim(self, claim_id: str) -> Optional[EvidenceRecord]: ...

    @abstractmethod
    def list_claims(self, limit: int = 50) -> list[tuple[Claim, Optional[EvidenceRecord]]]: ...

    @abstractmethod
    def list_evidence_records_for_subject(
        self, subject_id: str, only_verdicts: Optional[list[Verdict]] = None
    ) -> list[EvidenceRecord]: ...

    @abstractmethod
    def reset(self) -> None: ...

    # --- shared read-model helper ------------------------------------

    def compute_historical_site(
        self, subject_id: str, min_samples: int = 3
    ) -> Optional[HistoricalSite]:
        """Cluster the subject's prior CONSISTENT records to their dominant
        claimed site, and return it as a HistoricalSite.

        We use claimed-site coordinates (not the device's reported
        coordinates) as the anchor because CONSISTENT means "device was at
        the claimed site" — so the claimed site IS the observed site, but
        with a labelled name we can quote in the rationale.
        """
        records = self.list_evidence_records_for_subject(
            subject_id, only_verdicts=[Verdict.CONSISTENT]
        )
        if len(records) < min_samples:
            return None

        # Bucket by rounded (lat, lon) so tiny float noise doesn't split
        # a real site into multiple pseudo-sites.
        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for r in records:
            for item in r.evidence:
                if item.signal_name != "geofence_vs_claimed_site":
                    continue
                cs = (item.value or {}).get("claimed_site") or {}
                lat, lon = cs.get("lat"), cs.get("lon")
                if lat is None or lon is None:
                    continue
                key = (round(lat, 3), round(lon, 3))
                buckets[key].append({
                    "lat": lat, "lon": lon,
                    "radius_m": cs.get("radius_m", 500.0),
                    "label": cs.get("label"),
                    "created_at": r.created_at,
                })
                break

        if not buckets:
            return None

        # Dominant site = the bucket with the most observations.
        best_key, samples = max(buckets.items(), key=lambda kv: len(kv[1]))
        lats = [s["lat"] for s in samples]
        lons = [s["lon"] for s in samples]
        labels = [s["label"] for s in samples if s["label"]]
        last_seen = max(s["created_at"] for s in samples)

        return HistoricalSite(
            lat=statistics.fmean(lats),
            lon=statistics.fmean(lons),
            radius_m=samples[0]["radius_m"],
            label=Counter(labels).most_common(1)[0][0] if labels else None,
            sample_count=len(samples),
            last_seen=last_seen,
        )


# ---------- in-memory backend --------------------------------------------

class InMemoryStorage(Storage):
    backend_name = "InMemory"

    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}
        self._records: dict[str, EvidenceRecord] = {}
        self._records_by_claim: dict[str, str] = {}
        self._seed_flags: dict[str, bool] = {}

    def save_claim(self, claim: Claim) -> None:
        self._claims[claim.id] = claim

    def save_evidence_record(self, record: EvidenceRecord, is_seed: bool = False) -> None:
        self._records[record.id] = record
        self._records_by_claim[record.claim_id] = record.id
        self._seed_flags[record.id] = is_seed

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self._claims.get(claim_id)

    def get_evidence_record_for_claim(self, claim_id: str) -> Optional[EvidenceRecord]:
        rid = self._records_by_claim.get(claim_id)
        return self._records.get(rid) if rid else None

    def list_claims(self, limit: int = 50) -> list[tuple[Claim, Optional[EvidenceRecord]]]:
        # Newest first.
        claims = sorted(self._claims.values(), key=lambda c: c.created_at, reverse=True)[:limit]
        return [(c, self.get_evidence_record_for_claim(c.id)) for c in claims]

    def list_evidence_records_for_subject(
        self, subject_id: str, only_verdicts: Optional[list[Verdict]] = None
    ) -> list[EvidenceRecord]:
        allowed = set(only_verdicts) if only_verdicts else None
        out = []
        for record in self._records.values():
            claim = self._claims.get(record.claim_id)
            if claim is None or claim.subject_id != subject_id:
                continue
            if allowed and record.verdict not in allowed:
                continue
            out.append(record)
        return sorted(out, key=lambda r: r.created_at)

    def reset(self) -> None:
        self._claims.clear()
        self._records.clear()
        self._records_by_claim.clear()
        self._seed_flags.clear()


# ---------- Supabase backend ---------------------------------------------

# Row-shape helpers — kept together so a schema tweak is a single-file edit.

def _claim_to_row(claim: Claim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "subject_id": claim.subject_id,
        "phone": claim.phone,
        "assertion_text": claim.assertion_text,
        "claimed_site_lat": claim.claimed_site.lat,
        "claimed_site_lon": claim.claimed_site.lon,
        "claimed_radius_m": claim.claimed_site.radius_m,
        "claimed_site_label": claim.claimed_site.label,
        "time_window_start": claim.time_window_start,
        "time_window_end": claim.time_window_end,
        "source": claim.source,
        "consent_status": claim.consent_status.value,
        "created_at": claim.created_at,
    }


def _row_to_claim(row: dict[str, Any]) -> Claim:
    return Claim(
        id=row["id"],
        subject_id=row["subject_id"],
        phone=row["phone"],
        assertion_text=row["assertion_text"],
        claimed_site=ClaimedSite(
            lat=float(row["claimed_site_lat"]),
            lon=float(row["claimed_site_lon"]),
            radius_m=float(row["claimed_radius_m"]),
            label=row.get("claimed_site_label"),
        ),
        time_window_start=row["time_window_start"],
        time_window_end=row["time_window_end"],
        source=row.get("source") or "manual",
        consent_status=ConsentStatus(row.get("consent_status") or "ACTIVE"),
        created_at=row["created_at"],
    )


def _record_to_row(record: EvidenceRecord, is_seed: bool) -> dict[str, Any]:
    return {
        "id": record.id,
        "claim_id": record.claim_id,
        "profile": record.profile,
        "verdict": record.verdict.value,
        "confidence": record.confidence.value,
        "evidence": [e.to_dict() for e in record.evidence],
        "rationale": record.rationale,
        "consent_status": record.consent_status.value,
        "liveness": record.liveness,
        "is_seed": is_seed,
        "created_at": record.created_at,
    }


def _row_to_record(row: dict[str, Any]) -> EvidenceRecord:
    evidence = [
        EvidenceItem(
            signal_name=e["signal_name"],
            value=e["value"],
            evidence_class=EvidenceClass(e["evidence_class"]),
            timestamp=e.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            note=e.get("note"),
            why=e.get("why"),
        )
        for e in (row.get("evidence") or [])
    ]
    return EvidenceRecord(
        id=row["id"],
        claim_id=row["claim_id"],
        profile=row["profile"],
        verdict=Verdict(row["verdict"]),
        confidence=Confidence(row["confidence"]),
        evidence=evidence,
        rationale=row.get("rationale") or "",
        consent_status=ConsentStatus(row.get("consent_status") or "ACTIVE"),
        liveness=row.get("liveness"),
        created_at=row["created_at"],
    )


class SupabaseStorage(Storage):
    """Talks to Supabase's PostgREST API directly via httpx.

    We bypass the supabase-py SDK because (a) older versions reject the new
    sb_secret_* / sb_publishable_* key format at a client-side regex check
    before ever making a request, and (b) the SDK's internal httpx client
    doesn't expose an SSL context, which breaks on Windows where Python's
    bundled certifi doesn't cover every root Supabase's cert chain uses.

    Same truststore trick as the Nokia client — routes TLS through the OS
    root store.
    """

    backend_name = "Supabase"

    def __init__(self) -> None:
        import httpx
        from ..camara.live import _make_ssl_context

        if not SETTINGS.has_supabase:
            raise RuntimeError("SupabaseStorage needs SUPABASE_URL + SUPABASE_SERVICE_KEY")
        url = SETTINGS.supabase_url.rstrip("/")
        key = SETTINGS.supabase_service_key
        self._base = url + "/rest/v1"
        self._client = httpx.Client(
            base_url=self._base,
            headers={
                "apikey":        key,
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
            timeout=15.0,
            verify=_make_ssl_context(),
        )
        # Round-trip probe so a bad key / missing schema fails fast at
        # startup. We retry twice with short backoffs — Supabase's edge
        # can be slow to answer a cold first request during a server boot,
        # and one flaky probe shouldn't silently demote the whole session
        # to InMemory (which would be a very confusing demo failure).
        import time as _time
        last = None
        for delay in (0, 1.0, 2.0):
            if delay:
                _time.sleep(delay)
            try:
                r = self._client.get("/claims", params={"select": "id", "limit": "1"}, timeout=10.0)
                if r.status_code < 400:
                    return
                last = f"HTTP {r.status_code}: {r.text[:300]}"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(
            f"Supabase probe failed after 3 tries — {last}. "
            "Check URL / key / that schema.sql has been applied."
        )

    # -- helpers -------------------------------------------------------

    def _raise_on_error(self, r) -> None:
        if r.status_code >= 400:
            raise RuntimeError(f"supabase HTTP {r.status_code}: {r.text[:400]}")

    def _upsert(self, table: str, row: dict) -> None:
        r = self._client.post(
            f"/{table}",
            json=row,
            headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        self._raise_on_error(r)

    def _select(self, table: str, params: dict) -> list[dict]:
        r = self._client.get(f"/{table}", params=params)
        self._raise_on_error(r)
        return r.json() or []

    # -- writes --------------------------------------------------------

    def save_claim(self, claim: Claim) -> None:
        self._upsert("claims", _claim_to_row(claim))

    def save_evidence_record(self, record: EvidenceRecord, is_seed: bool = False) -> None:
        self._upsert("evidence_records", _record_to_row(record, is_seed))

    # -- reads ---------------------------------------------------------

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        rows = self._select("claims", {"id": f"eq.{claim_id}", "limit": "1"})
        return _row_to_claim(rows[0]) if rows else None

    def get_evidence_record_for_claim(self, claim_id: str) -> Optional[EvidenceRecord]:
        rows = self._select("evidence_records", {
            "claim_id": f"eq.{claim_id}",
            "order":    "created_at.desc",
            "limit":    "1",
        })
        return _row_to_record(rows[0]) if rows else None

    def list_claims(self, limit: int = 50) -> list[tuple[Claim, Optional[EvidenceRecord]]]:
        rows = self._select("claims", {
            "select": "*",
            "order":  "created_at.desc",
            "limit":  str(limit),
        })
        out = []
        for row in rows:
            claim = _row_to_claim(row)
            out.append((claim, self.get_evidence_record_for_claim(claim.id)))
        return out

    def list_evidence_records_for_subject(
        self, subject_id: str, only_verdicts: Optional[list[Verdict]] = None
    ) -> list[EvidenceRecord]:
        # Two-step: find claim ids for the subject, then pull their records.
        claim_rows = self._select("claims", {
            "select":     "id",
            "subject_id": f"eq.{subject_id}",
        })
        ids = [row["id"] for row in claim_rows]
        if not ids:
            return []
        params = {
            "select":   "*",
            "claim_id": f"in.({','.join(ids)})",
            "order":    "created_at.asc",
        }
        if only_verdicts:
            params["verdict"] = f"in.({','.join(v.value for v in only_verdicts)})"
        rows = self._select("evidence_records", params)
        return [_row_to_record(row) for row in rows]

    def reset(self) -> None:
        # PostgREST requires DELETE to have a filter. `id=not.is.null` matches
        # every row (id is a NOT NULL primary key). Cascade FK handles
        # evidence_records via the claim delete, but we clear it explicitly
        # too, in case an orphan record ever existed.
        for table in ("evidence_records", "claims"):
            r = self._client.delete(f"/{table}", params={"id": "not.is.null"})
            self._raise_on_error(r)


# ---------- factory -------------------------------------------------------

_storage_singleton: Optional[Storage] = None


def get_storage() -> Storage:
    """Return a process-wide Storage instance. Supabase if configured and
    the client library imports cleanly, in-memory otherwise.
    """
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton
    if SETTINGS.has_supabase:
        try:
            _storage_singleton = SupabaseStorage()
            return _storage_singleton
        except Exception as exc:
            # Log-and-fall-back so a mis-set env doesn't take down the demo.
            print(f"[storage] Supabase init failed ({exc}); using in-memory backend.")
    _storage_singleton = InMemoryStorage()
    return _storage_singleton


def reset_storage_cache() -> None:
    """Drop the cached storage instance (test hook, and used after seeding
    to force a fresh backend pick if config changes at runtime).
    """
    global _storage_singleton
    _storage_singleton = None
