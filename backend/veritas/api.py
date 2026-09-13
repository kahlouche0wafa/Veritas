"""VERITAS HTTP API — FastAPI.

Endpoints (per the build brief):
  POST /claims          submit a claim (free text OR structured)
  GET  /claims/{id}     get one claim + its evidence record
  GET  /claims          list recent claims + verdicts (for dashboard)
  POST /demo/seed       reset + reseed demo scenarios
  GET  /demo/scenarios  the four pre-built demo claim texts (frontend uses
                        these as its verdict-per-button set)
  GET  /health          liveness + backend-status snapshot
  GET  /catalog         subjects + sites the agent knows about

The API is a thin layer on top of the agent + profiles + storage modules.
It NEVER decides a verdict on its own — every /claims POST goes through
Agent.resolve_claim → select_profile → run_verification → decide_verdict.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import Agent, ClaimNormalisationError
from .agent.catalog import SITES, SUBJECTS
from .camara.base import CamaraClient
from .camara.factory import get_camara_client
from .config import SETTINGS
from .db import get_storage, reset_storage_cache
from .db.seed import DEMO_SUBJECTS, seed_demo
from .db.storage import Storage
from .models import Claim, ClaimedSite, ConsentStatus
from .profiles import ALL_PROFILES, run_verification
from .trace import TraceLogger


# ---------------------------------------------------------------------
# Singletons — one Agent + one CamaraClient per process.
# ---------------------------------------------------------------------

_agent: Optional[Agent] = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


_camara: Optional[CamaraClient] = None


def get_camara() -> CamaraClient:
    global _camara
    if _camara is None:
        _camara = get_camara_client()
    return _camara


# ---------------------------------------------------------------------
# Lifespan — auto-seed the in-memory backend so a cold server still has
# history to compare REASSIGNMENT claims against. We deliberately do NOT
# auto-seed Supabase — that stays a manual /demo/seed decision.
# ---------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = get_storage()
    if storage.backend_name == "InMemory" and not storage.list_claims(limit=1):
        seed_demo(storage)
        print(f"[api] auto-seeded {storage.backend_name} storage on startup")
    yield


app = FastAPI(
    title="VERITAS API",
    version="0.1.0",
    description=(
        "Claim verification engine. Every POST /claims runs the same "
        "deterministic decide_verdict() function — the LLM only normalises "
        "the intake and writes the rationale."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # prototype — tighten before shipping
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pure ASGI middleware that strips a leading /api from incoming paths
# before FastAPI's router sees them. This lets the same app serve both
# local dev (Vite proxy already strips /api → app receives /health → noop)
# AND Vercel (which forwards /api/health unchanged → this strips → /health).
# Additive only; no existing route decorator changes.
class StripApiPrefix:
    def __init__(self, app, prefix: str = "/api"):
        self.app = app
        self.prefix = prefix
        self._plen = len(prefix)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                scope["path"] = path[self._plen:] or "/"
                raw = scope.get("raw_path") or b""
                pfx = self.prefix.encode()
                if raw == pfx or raw.startswith(pfx + b"/"):
                    scope["raw_path"] = raw[self._plen:] or b"/"
        await self.app(scope, receive, send)


app.add_middleware(StripApiPrefix, prefix="/api")


# ---------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------

class ClaimedSiteIn(BaseModel):
    lat: float
    lon: float
    radius_m: float = 500.0
    label: Optional[str] = None


class SubmitClaimRequest(BaseModel):
    """Either supply `text` (free text — the agent normalises it) OR the
    fully structured fields (bypasses the LLM). If both are present the
    structured fields win; `text` becomes assertion_text.
    """

    text: Optional[str] = Field(
        default=None,
        description="Free-text claim; the agent will normalise it.",
    )
    subject_id: Optional[str] = None
    phone: Optional[str] = None
    assertion_text: Optional[str] = None
    claimed_site: Optional[ClaimedSiteIn] = None
    time_window_start: Optional[str] = None
    time_window_end: Optional[str] = None
    profile: Optional[str] = Field(
        default=None,
        description=f"Verification profile. One of {ALL_PROFILES}. "
                    "If omitted, the agent picks.",
    )
    consent_status: str = "ACTIVE"
    source: str = "api"


class HealthResponse(BaseModel):
    status: str
    backends: dict[str, Any]
    catalog: dict[str, int]


# ---------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------

def _serialize(claim: Claim, record, trace: Optional[TraceLogger] = None) -> dict:
    out = {
        "claim": claim.to_dict(),
        "evidence_record": record.to_dict() if record else None,
    }
    if trace is not None:
        out["agent_trace"] = trace.to_list()
    return out


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        backends={
            "camara": type(get_camara()).__name__,
            "storage": get_storage().backend_name,
            "llm_available": get_agent().llm.available,
            "llm_model": SETTINGS.groq_model if get_agent().llm.available else None,
        },
        catalog={"subjects": len(SUBJECTS), "sites": len(SITES)},
    )


@app.get("/catalog")
def catalog() -> dict:
    return {
        "subjects": [
            {"subject_id": s.subject_id, "phone": s.phone, "display_name": s.display_name,
             "aliases": list(s.aliases)}
            for s in SUBJECTS.values()
        ],
        "sites": [
            {"key": s.key, "label": s.label, "lat": s.lat, "lon": s.lon,
             "radius_m": s.radius_m, "aliases": list(s.aliases)}
            for s in SITES.values()
        ],
    }


@app.post("/claims")
def submit_claim(req: SubmitClaimRequest = Body(...)) -> dict:
    """Submit a claim for verification.

    Two ways to call:
      1. Free text: `{"text": "Ahmed was at Budapest HQ today"}`
      2. Structured: send subject_id, phone, claimed_site, etc. explicitly.

    Returns the assembled Claim and the EvidenceRecord together.
    """
    agent = get_agent()
    storage = get_storage()
    camara = get_camara()
    trace = TraceLogger()

    # --- 0. Input validation -----------------------------------------
    # Empty/whitespace-only text with no structured fields → 400 fast, no
    # agent work. Prevents a confusing "structured claim needs subject_id"
    # error for what is really just an empty submit.
    has_structured = bool(req.subject_id and (req.claimed_site or req.assertion_text))
    if not has_structured and (not req.text or not req.text.strip()):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_claim_text",
                "message": "No claim text provided. Type a claim (e.g. \"Ahmed was at Budapest HQ today\") or send a structured payload with subject_id + claimed_site.",
            },
        )

    # --- 1. Build the Claim ------------------------------------------
    if req.text and not (req.subject_id and req.claimed_site):
        try:
            claim = agent.resolve_claim(req.text, storage, source=req.source, trace=trace)
        except ClaimNormalisationError as exc:
            trace.add("normalise", f"Failed to resolve: {exc}")
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc), "agent_trace": trace.to_list()},
            )
        if req.consent_status:
            claim.consent_status = ConsentStatus(req.consent_status)
    else:
        # Structured path — requires subject_id and either claimed_site or
        # ability to derive one from history.
        if not req.subject_id:
            raise HTTPException(422, "structured claim needs subject_id")
        subject = SUBJECTS.get(req.subject_id)
        if not subject:
            raise HTTPException(422, f"unknown subject_id: {req.subject_id!r}")
        phone = req.phone or subject.phone

        if req.claimed_site:
            site = ClaimedSite(
                lat=req.claimed_site.lat, lon=req.claimed_site.lon,
                radius_m=req.claimed_site.radius_m, label=req.claimed_site.label,
            )
        else:
            hs = storage.compute_historical_site(subject.subject_id)
            if hs is None:
                raise HTTPException(422, "no claimed_site and no history to fall back to")
            site = ClaimedSite(
                lat=hs.lat, lon=hs.lon, radius_m=hs.radius_m,
                label=(hs.label or "expected zone") + " (from history)",
            )

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        claim = Claim(
            subject_id=subject.subject_id,
            phone=phone,
            assertion_text=req.assertion_text or req.text or f"structured claim for {subject.display_name}",
            claimed_site=site,
            time_window_start=req.time_window_start or (now - timedelta(hours=1)).isoformat(),
            time_window_end=req.time_window_end or (now + timedelta(hours=8)).isoformat(),
            source=req.source,
            consent_status=ConsentStatus(req.consent_status),
        )

    # --- 2. Pick profile ---------------------------------------------
    if req.profile:
        profile = req.profile.upper()
        trace.select_profile(
            f"Profile forced via API request: {profile}",
            {"profile": profile, "source": "request"},
        )
    else:
        profile = agent.select_profile(claim, trace=trace).upper()
    if profile not in ALL_PROFILES:
        raise HTTPException(422, f"unknown profile {profile!r}; known: {ALL_PROFILES}")

    # --- 3. Run verification -----------------------------------------
    hs = storage.compute_historical_site(claim.subject_id)
    if hs is not None:
        trace.history(
            f"Loaded {hs.sample_count} prior CONSISTENT records for "
            f"{claim.subject_id}; dominant site = {hs.label or 'unnamed'}.",
            {"sample_count": hs.sample_count, "label": hs.label},
        )
    else:
        trace.history(
            f"No prior CONSISTENT records for {claim.subject_id} — first-time subject.",
        )
    record = run_verification(
        profile, claim, camara,
        historical_site=hs,
        rationale_writer=agent.write_rationale,
        trace=trace,
    )

    # --- 4. Persist ---------------------------------------------------
    storage.save_claim(claim)
    storage.save_evidence_record(record)

    return _serialize(claim, record, trace)


@app.get("/claims/{claim_id}")
def get_claim(claim_id: str) -> dict:
    storage = get_storage()
    claim = storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(404, f"claim not found: {claim_id}")
    record = storage.get_evidence_record_for_claim(claim_id)
    return _serialize(claim, record)


@app.get("/claims")
def list_claims(
    limit: int = Query(50, ge=1, le=200),
    subject_id: Optional[str] = None,
    verdict: Optional[str] = None,
    include_seed: bool = False,
) -> dict:
    storage = get_storage()
    pairs = storage.list_claims(limit=limit)
    items = []
    for claim, record in pairs:
        if subject_id and claim.subject_id != subject_id:
            continue
        if verdict and (not record or record.verdict.value != verdict):
            continue
        if not include_seed and claim.source == "seed":
            continue
        items.append(_serialize(claim, record))
    return {"count": len(items), "items": items}


@app.post("/demo/seed")
def demo_seed(reset: bool = Query(True)) -> dict:
    storage = get_storage()
    counts = seed_demo(storage, reset_first=reset)
    return {"backend": storage.backend_name, "reset": reset, "seeded": counts}


@app.get("/demo/scenarios")
def demo_scenarios() -> dict:
    """The four pre-built demo claim texts, one per verdict. The frontend
    renders these as four buttons; POSTing any of them to /claims yields
    the labelled verdict.
    """
    return {
        "scenarios": [
            {
                "id": "consistent",
                "label": "CONSISTENT",
                "description": "Worker is at the site the claim states, matches history.",
                "text": "Ahmed was on shift at Budapest HQ today from 09:00 to 17:00.",
            },
            {
                "id": "reassignment",
                "label": "REASSIGNMENT",
                "description": (
                    "Claim says a new site; device really is at that new site; "
                    "history was elsewhere. Pattern break the claim itself declares."
                ),
                "text": "Sara has been reassigned to the Budapest office this week and is there today.",
            },
            {
                "id": "contradicted",
                "label": "CONTRADICTED",
                "description": (
                    "Claim states one site; device is somewhere else entirely. "
                    "Escalates to a stubbed liveness challenge."
                ),
                "text": "Omar clocked in at Riyadh Site A this morning at 08:00.",
            },
            {
                "id": "insufficient",
                "label": "INSUFFICIENT",
                "description": (
                    "Device is unreachable. VERITAS declines to guess."
                ),
                "text": "Layla was on site at the Cairo site during the morning shift.",
            },
        ]
    }
