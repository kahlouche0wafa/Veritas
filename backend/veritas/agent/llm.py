"""LLM client with Groq key rotation.

You have many Groq keys in .env — we rotate through them on rate-limit or
auth errors so the demo doesn't stall on a single tripped key. If every
key fails, callers get None and the fallback deterministic rationale is
used instead. The point is: no LLM outage can turn into a demo outage.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import Optional


def _make_http_client():
    """httpx.Client that trusts the OS root store — needed on Windows
    because certifi's bundle doesn't cover Groq's cert chain, which
    silently manifested as APIConnectionError('Connection error.') on
    every request regardless of key validity.
    """
    import httpx
    try:
        import truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return httpx.Client(verify=ctx, timeout=30.0)
    except Exception:
        return httpx.Client(timeout=30.0)


def _list_groq_keys() -> list[str]:
    keys: list[str] = []
    for i in range(1, 64):
        v = os.getenv(f"GROQ_API_KEY_{i}")
        if v:
            keys.append(v)
    if not keys:
        v = os.getenv("GROQ_API_KEY")
        if v:
            keys.append(v)
    return keys


class GroqLLM:
    """Thin wrapper around the groq SDK with rotation + JSON mode helpers."""

    def __init__(self, model: str = "openai/gpt-oss-20b", keys: list[str] | None = None):
        self.model = model
        self.keys = keys if keys is not None else _list_groq_keys()
        self._i = 0
        self._http = _make_http_client()

    @property
    def available(self) -> bool:
        return bool(self.keys)

    def _client(self):
        from groq import Groq  # lazy import so the whole app runs without groq installed
        return Groq(api_key=self.keys[self._i % len(self.keys)], http_client=self._http)

    def _advance_key(self) -> None:
        self._i += 1

    def _call(self, messages: list[dict], response_format: dict | None, max_retries: int) -> Optional[str]:
        """Return content text, or None if every key was exhausted."""
        from groq import APIError, APIConnectionError, RateLimitError  # types
        tried = 0
        last_err: Optional[Exception] = None
        while tried < max_retries and self.keys:
            try:
                # 2000-token ceiling: gpt-oss models emit reasoning tokens
                # that count against completion; 800 was too tight and made
                # the JSON call time-out mid-generation.
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 2000,
                }
                if response_format is not None:
                    kwargs["response_format"] = response_format
                resp = self._client().chat.completions.create(**kwargs)
                return resp.choices[0].message.content
            except (RateLimitError, APIError, APIConnectionError) as exc:
                last_err = exc
                self._advance_key()
                tried += 1
            except Exception as exc:
                # Any other error — one rotation attempt, then give up.
                last_err = exc
                self._advance_key()
                tried += 1
        # Every key exhausted (or none configured). Callers handle None.
        if last_err is not None:
            cause = getattr(last_err, "__cause__", None)
            cause_str = f" (cause: {type(cause).__name__}: {cause})" if cause else ""
            # DIAGNOSTIC: pull whatever Groq's exception object exposes so we
            # can distinguish 429 rate limits from transport errors etc.
            extra = {}
            for attr in ("status_code", "code", "body"):
                v = getattr(last_err, attr, None)
                if v is not None:
                    extra[attr] = str(v)[:250]
            resp = getattr(last_err, "response", None)
            if resp is not None:
                for h in ("x-ratelimit-remaining-requests",
                          "x-ratelimit-remaining-tokens",
                          "x-ratelimit-reset-requests",
                          "x-ratelimit-reset-tokens",
                          "retry-after"):
                    try:
                        v = resp.headers.get(h) if hasattr(resp, "headers") else None
                        if v: extra[h] = v
                    except Exception:
                        pass
            print(f"[llm] all Groq keys failed: {type(last_err).__name__}: {last_err}{cause_str}")
            if extra:
                print(f"[llm] error detail: {extra}")
        return None

    def complete_json(self, system: str, user: str, max_retries: int = 3) -> Optional[dict]:
        # Groq's JSON mode requires the prompt to mention "JSON" — enforce it.
        if "JSON" not in system and "json" not in system:
            system = system + "\n\nYou MUST respond with a valid JSON object."
        raw = self._call(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_retries=max_retries,
        )
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # One last-ditch strip of anything before the first '{' / after the last '}'.
            try:
                start, end = raw.index("{"), raw.rindex("}") + 1
                return json.loads(raw[start:end])
            except Exception:
                print(f"[llm] response was not valid JSON: {raw[:200]}")
                return None

    def complete_text(self, system: str, user: str, max_retries: int = 3) -> Optional[str]:
        return self._call(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=None,
            max_retries=max_retries,
        )
