# VERITAS

> Claim verification engine. A system asserts a claim about the physical world;
> VERITAS picks the network signals that would prove or disprove it, queries
> them, and returns one of four verdicts with a full audit trail.
>
> **One sentence:** the verdict logic is a 60-line pure Python function; the
> LLM only normalises free-text intake and writes the human-readable rationale.

Built for the GSMA MENA Ignite Hackathon prototype phase.

---

## The four verdicts (fixed)

| Verdict | When it fires |
|---|---|
| **CONSISTENT** | Evidence aligns with the claim. Log it. |
| **REASSIGNMENT** | Device matches the claim's new site, but historical pattern was elsewhere. A pattern break the claim itself declares — not a lie. |
| **CONTRADICTED** | Device is not at the claimed site. Escalates to a stubbed liveness challenge. |
| **INSUFFICIENT** | Device unreachable / signal missing. VERITAS declines to guess. |

Plus a fifth non-verdict — `NO_VERDICT_NO_CONSENT` — returned when the
consent gate is closed and VERITAS deliberately queries no network signals.

## Architecture

```
free-text claim
      │
      ▼
[ Agent.resolve_claim ]  ─── LLM (Groq/llama or gpt-oss) → structured Claim
      │                      NEVER decides verdict
      ▼
[ Agent.select_profile ] ─── PRESENCE | IDENTITY | TRANSACTION
      │
      ▼
[ run_verification ] ────── gather CAMARA signals via adapter
      │                     (live simulator OR scripted)
      ▼
[ decide_verdict ] ──────── PURE FUNCTION — the whole compliance surface
      │                     lives here, 60 lines, no side effects
      ▼
[ Agent.write_rationale ] ─ LLM writes human prose for the record
      │
      ▼
[ Storage ] ────────────── Supabase OR in-memory (auto-fallback)
```

## Run it

Backend:
```bash
cd C:/veritas/backend
python -m pip install -r requirements.txt
python -m uvicorn veritas.api:app --host 127.0.0.1 --port 8765
```

Frontend:
```bash
cd C:/veritas/frontend
npm install
npm run dev -- --port 5174
```

Open `http://localhost:5174`. Interactive API docs at `http://127.0.0.1:8765/docs`.

## Configuration (`.env` at `C:/veritas/.env`)

| Var | Purpose | Required |
|---|---|---|
| `GROQ_API_KEY_1..N` | Rotated across on rate limit / auth error | No (falls back to deterministic rationale) |
| `GROQ_MODEL` | Default `openai/gpt-oss-20b`. Try `openai/gpt-oss-120b` for bigger rationales | No |
| `NOKIA_BASE_URL` | Nokia Network as Code base URL | No (falls back to ScriptedCamaraClient) |
| `NOKIA_API_KEY` | Nokia Network as Code key | No |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Persistent storage | No (falls back to in-memory) |
| `CAMARA_MODE` | `live` \| `scripted` \| `auto` (default) | No |

Everything degrades gracefully — a cold server with no keys at all still
serves all 4 verdicts against the scripted client with a deterministic
rationale.

## Layout

```
veritas/
  backend/
    veritas/
      camara/          # CAMARA API adapters (live + scripted)
      db/              # storage (Supabase + in-memory) + seed data + schema.sql
      agent/           # LLM wrapper, catalog, prompts, agent
      models.py        # Claim, EvidenceRecord, Verdict, ...
      verdict.py       # decide_verdict — the pure rule engine
      presence.py      # PRESENCE profile
      profiles.py      # IDENTITY + TRANSACTION + dispatcher
      config.py        # settings loader
      api.py           # FastAPI app
    scripts/           # smoke_step1..6, seed_demo
  frontend/            # Vite + React single page
  docs/
```

## Agent trace

Every `POST /claims` response now includes an `agent_trace` field — an
ordered list of steps the agent took, each with a timestamp. Step actions:

| Action | Meaning |
|---|---|
| `normalise` | LLM (or keyword fallback) parsed free text into a Claim |
| `select_profile` | Agent picked PRESENCE / IDENTITY / TRANSACTION |
| `plan_evidence` | Decided which signals to query (risk-scaled for TRANSACTION) |
| `gather` | About to call a network API |
| `result` | Response received |
| `skip` | Signal deliberately not queried, with reason |
| `history` | Accumulated evidence loaded from storage |
| `geofence_check` | VERITAS-computed geofence (own haversine) |
| `verdict` | `decide_verdict()` returned — pure function |
| `explain` | LLM asked to write the human rationale |
| `complete` | Record persisted |

The trace is generated alongside — never inside — `decide_verdict`. The
verdict function's signature and body never see the trace.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health` | Backend + LLM status |
| `GET`  | `/catalog` | Subjects + sites the agent can resolve |
| `GET`  | `/demo/scenarios` | 4 pre-built claims (one per verdict) |
| `POST` | `/demo/seed` | Reset + reseed demo history |
| `POST` | `/claims` | Submit a claim (free text OR structured) |
| `GET`  | `/claims/{id}` | Get one claim + its evidence record |
| `GET`  | `/claims` | List recent claims + verdicts |

## Deliberate scope decisions (per the brief)

- **Number Verification** — logged in the evidence record as `"not queried
  — requires production OIDC flow"`. Not stubbed with a fake response.
- **Liveness / SMS** — stubbed with a deterministic `sent → replied at →
  responder_zone` trace, clearly labelled `"stub": true` inside the record.
- **Location verification** — CAMARA `verifyLocation` returns `FALSE` for
  every input in the simulator (verified in live testing). VERITAS
  computes its own geofence: `retrieveLocation` + haversine against the
  claim's stated site. This is the architecture, not a workaround.
- **Geofencing webhooks** — kept as point-in-time `retrieveLocation`
  checks so the demo doesn't depend on webhook delivery.
- **Authentication** — none. Out of scope.

## Live CAMARA endpoints

All four live and verified against Nokia's Network as Code simulator
via `apihub.nokia.io` (RapidAPI-backed):

| API | Path |
|---|---|
| Location Retrieval | `POST /location-retrieval/v0/retrieve` |
| Device Status | `POST /device-status/v0/connectivity` |
| Device Swap | `POST /passthrough/camara/v1/device-swap/device-swap/v1/check` |
| KYC Tenure | `POST /passthrough/camara/v1/kyc-tenure/kyc-tenure/v0.1/check-tenure` (needs `x-correlator` UUID header) |

Two API families, two path shapes — Nokia doesn't route them the same way.
The RapidAPI gateway needs both `x-rapidapi-key` and `x-rapidapi-host`;
the host header value is `network-as-code.nokia.rapidapi.com`, which is
different from the base URL host `network-as-code.p-eu.apihub.nokia.io`.
Both are set via env, see `.env.example`.

**TLS note (Windows):** we route `httpx` through `truststore` (see
requirements.txt) so it uses the OS certificate store. Python's bundled
`certifi` roots don't cover every intermediate in Nokia's apihub cert
chain on Windows, which was silently causing `SSL: CERTIFICATE_VERIFY_FAILED`
on live calls.

## Demo scripts

Run any of these from `C:/veritas/backend`:

| Script | Proves |
|---|---|
| `python -m scripts.smoke_step1` | CAMARA adapter (live + scripted) |
| `python -m scripts.smoke_step2` | All 4 verdicts + no-consent gate |
| `python -m scripts.smoke_step3` | Storage + computed HistoricalSite |
| `python -m scripts.smoke_step4` | End-to-end with LLM normalise + LLM rationale |
| `python -m scripts.smoke_step5` | All 3 profiles routed through decide_verdict |
| `python -m scripts.smoke_step6` | Every HTTP endpoint |
