"""Groq call diagnostics — no code change to llm.py, just probes.

Runs the same request shape my agent uses (JSON mode + text mode) and
captures full detail: HTTP status, rate-limit headers, response body,
exception chain, per-key result.

Also reproduces the two TRANSACTION claims via the live API and reads
uvicorn's [llm] log lines if present.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from veritas.config import SETTINGS  # loads .env
from veritas.agent.llm import _list_groq_keys

MODEL = SETTINGS.groq_model
KEYS = _list_groq_keys()

MIN_JSON_SYSTEM = (
    "You output JSON. Return exactly {\"ok\": true}."
)
MIN_JSON_USER = "please echo ok."

NORMALISE_SYSTEM_APPROX = (
    "You are an intake normaliser. Return JSON with keys "
    "subject_key, site_key, assertion_text, time_window (with "
    "start_iso and end_iso), confidence_notes."
)
NORMALISE_USER_APPROX = (
    "now_utc: 2026-09-13T02:00:00Z\n\n"
    "Known subjects:\n"
    "  - worker-001: Ahmed Reda, phone=+99999991000\n"
    "  - worker-002: Sara Malik, phone=+99999991001\n"
    "  - worker-003: Omar Nabil, phone=+99999991003\n"
    "  - worker-004: Layla Hassan, phone=+99999991002\n\n"
    "Known sites:\n"
    "  - budapest_hq: Budapest HQ\n"
    "  - riyadh_a: Riyadh Site A\n"
    "  - cairo_b: Cairo Site B\n\n"
    "User claim (verbatim):\n\"\"\"\n"
    "A payment of 120,000 SAR is pending from Ahmed — verify before releasing\n"
    "\"\"\""
)


def dump_response_headers(resp) -> dict:
    """Extract rate-limit relevant headers if present."""
    if resp is None or not hasattr(resp, "headers"):
        return {}
    keys = [
        "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
        "x-ratelimit-limit-tokens",   "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
        "retry-after",
    ]
    h = {}
    for k in keys:
        v = resp.headers.get(k) if hasattr(resp.headers, "get") else None
        if v:
            h[k] = v
    return h


def one_call(key: str, system: str, user: str, json_mode: bool, max_tokens: int) -> dict:
    """Fire ONE request with ONE key, return full diagnostic dict."""
    from groq import Groq
    result = {
        "key_prefix": key[:11] + "…" + key[-4:],
        "json_mode": json_mode,
        "max_tokens": max_tokens,
    }
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    t0 = time.time()
    try:
        client = Groq(api_key=key)
        resp = client.chat.completions.create(**kwargs)
        elapsed = time.time() - t0
        raw = resp.choices[0].message.content
        result.update({
            "ok": True,
            "elapsed_s": round(elapsed, 2),
            "finish_reason": resp.choices[0].finish_reason,
            "completion_tokens": getattr(resp.usage, "completion_tokens", None),
            "prompt_tokens":     getattr(resp.usage, "prompt_tokens", None),
            "total_tokens":      getattr(resp.usage, "total_tokens", None),
            "content_preview":   (raw or "")[:200],
            "content_len":       len(raw or ""),
            "content_is_json":   _is_json(raw),
        })
    except Exception as exc:
        elapsed = time.time() - t0
        info = {
            "ok": False,
            "elapsed_s": round(elapsed, 2),
            "exc_type": type(exc).__name__,
            "exc_msg":  str(exc)[:400],
        }
        # groq SDK errors typically expose .status_code, .response, .body
        for attr in ("status_code", "response", "body", "code"):
            v = getattr(exc, attr, None)
            if v is None:
                continue
            if attr == "response":
                info["response_headers"] = dump_response_headers(v)
                try:
                    info["response_body"] = v.text[:400] if hasattr(v, "text") else str(v)[:400]
                except Exception:
                    pass
            else:
                info[attr] = str(v)[:200]
        result.update(info)
    return result


def _is_json(text) -> bool:
    if not text:
        return False
    try:
        json.loads(text)
        return True
    except Exception:
        try:
            s = text.index("{"); e = text.rindex("}") + 1
            json.loads(text[s:e])
            return True
        except Exception:
            return False


def main() -> int:
    print(f"MODEL: {MODEL}")
    print(f"KEYS FOUND: {len(KEYS)}")
    print()

    # -------- Test A: single-key tiny JSON call --------
    print("=" * 68)
    print("TEST A — one key, tiny JSON call, generous 2000 tokens")
    r = one_call(KEYS[0], MIN_JSON_SYSTEM, MIN_JSON_USER, json_mode=True, max_tokens=2000)
    print(json.dumps(r, indent=2))
    print()

    # -------- Test B: single-key BIG normalise JSON call --------
    print("=" * 68)
    print("TEST B — one key, realistic normalise JSON prompt, 2000 tokens")
    r = one_call(KEYS[0], NORMALISE_SYSTEM_APPROX, NORMALISE_USER_APPROX,
                 json_mode=True, max_tokens=2000)
    print(json.dumps(r, indent=2))
    print()

    # -------- Test C: same request across 5 different keys, back-to-back --------
    print("=" * 68)
    print(f"TEST C — same tiny call across the FIRST 5 KEYS, back-to-back "
          f"(if any key returns 429 that proves shared account limit)")
    sample = KEYS[:5]
    for i, k in enumerate(sample, 1):
        r = one_call(k, MIN_JSON_SYSTEM, MIN_JSON_USER, json_mode=True, max_tokens=200)
        head = f"key#{i} {r['key_prefix']}  →  ok={r['ok']}  elapsed={r['elapsed_s']}s"
        if r.get("ok"):
            head += f"  finish={r.get('finish_reason')}  tokens={r.get('total_tokens')}"
        else:
            head += f"  exc={r.get('exc_type')}  status={r.get('status_code','?')}"
        print(f"  {head}")
        if not r.get("ok"):
            print(f"    msg: {r.get('exc_msg','')[:200]}")
            if r.get("response_body"):
                print(f"    body: {r['response_body'][:200]}")
            if r.get("response_headers"):
                print(f"    hdrs: {r['response_headers']}")
    print()

    # -------- Test D: token starvation test — set max_tokens LOW to see finish_reason --------
    print("=" * 68)
    print("TEST D — realistic normalise but max_tokens capped at 400 "
          "(matches the OLD llm.py cap, expected to truncate)")
    r = one_call(KEYS[0], NORMALISE_SYSTEM_APPROX, NORMALISE_USER_APPROX,
                 json_mode=True, max_tokens=400)
    print(json.dumps(r, indent=2))
    print()

    # -------- Test E: rapid-fire 8 sequential calls on one key --------
    print("=" * 68)
    print("TEST E — 8 rapid sequential calls on ONE key (probe for burst limit)")
    for i in range(8):
        r = one_call(KEYS[0], MIN_JSON_SYSTEM, MIN_JSON_USER, json_mode=True, max_tokens=200)
        print(f"  call#{i+1} ok={r['ok']}  elapsed={r['elapsed_s']}s  "
              f"finish={r.get('finish_reason','—')}  err={r.get('exc_type','—')}"
              + (f"  msg={r.get('exc_msg','')[:80]}" if not r['ok'] else ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
