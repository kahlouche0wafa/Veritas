"""Live CAMARA client — real HTTP calls to Nokia's Network as Code
simulator on the apihub.nokia.io RapidAPI-backed gateway.

Configured entirely from env (see config.py). Working defaults are the
verified paths returned by clicking Run on the Nokia portal in Sept 2026
— they may drift over time; each path is overridable via env.

    NOKIA_BASE_URL       https://network-as-code.p-eu.apihub.nokia.io
    NOKIA_API_KEY        the RapidAPI key
    NOKIA_AUTH_HEADER    header name for the key (default X-API-Key;
                         set to x-rapidapi-key for the RapidAPI gateway)
    NOKIA_RAPIDAPI_HOST  x-rapidapi-host value when using the RapidAPI
                         gateway (needed because it's not the same as
                         the base URL host on Nokia's routing)

Endpoint paths — the two API families use different shapes on Nokia's
gateway, so no clever "base pattern" is assumed:

    NOKIA_LOCATION_PATH      /location-retrieval/v0/retrieve
    NOKIA_CONNECTIVITY_PATH  /device-status/v0/connectivity
    NOKIA_DEVICE_SWAP_PATH   /passthrough/camara/v1/device-swap/device-swap/v1/check
    NOKIA_TENURE_PATH        /passthrough/camara/v1/kyc-tenure/kyc-tenure/v0.1/check-tenure

The KYC Tenure endpoint alone requires:
  * an x-correlator UUID header per request
  * a `tenureDate` in the body — the reference date the check is anchored
    against. We compute it as today - NOKIA_TENURE_LOOKBACK_DAYS
    (default 365 days), so tenureDateCheck=true means "number has been
    active at least a year."

Responses are parsed defensively — field names vary a little between
CAMARA versions.
"""

from __future__ import annotations

import os
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx


def _make_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that trusts the OS root store — needed on
    Windows because Python's bundled certifi doesn't include every root
    Nokia's apihub cert chain uses, and the OS store does.
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


from .base import (
    CamaraClient,
    ConnectivityResult,
    LocationResult,
    SwapResult,
    TenureResult,
)


DEFAULT_PATHS = {
    "location":     "/location-retrieval/v0/retrieve",
    "connectivity": "/device-status/v0/connectivity",
    "swap":         "/passthrough/camara/v1/device-swap/device-swap/v1/check",
    "tenure":       "/passthrough/camara/v1/kyc-tenure/kyc-tenure/v0.1/check-tenure",
}


class LiveCamaraClient(CamaraClient):
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.base_url = (base_url or os.getenv("NOKIA_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("NOKIA_API_KEY", "")
        if not self.base_url or not self.api_key:
            raise RuntimeError(
                "LiveCamaraClient needs NOKIA_BASE_URL and NOKIA_API_KEY "
                "(set them in .env, or use ScriptedCamaraClient)"
            )
        auth_header = os.getenv("NOKIA_AUTH_HEADER", "X-API-Key")
        base_headers = {auth_header: self.api_key, "Content-Type": "application/json"}
        if auth_header.lower() == "x-rapidapi-key":
            base_headers["X-RapidAPI-Host"] = (
                os.getenv("NOKIA_RAPIDAPI_HOST") or urlparse(self.base_url).netloc
            )
        self._client = httpx.Client(
            base_url=self.base_url, headers=base_headers, timeout=timeout,
            verify=_make_ssl_context(),
        )
        self.paths = {
            "location":     os.getenv("NOKIA_LOCATION_PATH",     DEFAULT_PATHS["location"]),
            "connectivity": os.getenv("NOKIA_CONNECTIVITY_PATH", DEFAULT_PATHS["connectivity"]),
            "swap":         os.getenv("NOKIA_DEVICE_SWAP_PATH",  DEFAULT_PATHS["swap"]),
            "tenure":       os.getenv("NOKIA_TENURE_PATH",       DEFAULT_PATHS["tenure"]),
        }
        self.tenure_lookback_days = int(os.getenv("NOKIA_TENURE_LOOKBACK_DAYS", "365"))

    def _post(
        self, path: str, body: dict, extra_headers: Optional[dict] = None,
    ) -> tuple[Optional[dict], Optional[str]]:
        try:
            resp = self._client.post(path, json=body, headers=extra_headers or None)
        except httpx.HTTPError as exc:
            return None, f"network error: {exc}"
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
        try:
            return resp.json(), None
        except ValueError:
            return None, f"non-JSON response: {resp.text[:300]}"

    # -- individual signals -----------------------------------------------

    def retrieve_location(self, phone: str) -> LocationResult:
        data, err = self._post(
            self.paths["location"],
            {"device": {"phoneNumber": phone}, "maxAge": 60},
        )
        if err:
            return LocationResult(ok=False, error=err)
        area = data.get("area", {}) or {}
        center = area.get("center", {}) or {}
        lat = center.get("latitude")
        lon = center.get("longitude")
        if lat is None or lon is None:
            return LocationResult(ok=False, error="no coordinates in response", raw=data)
        return LocationResult(
            ok=True,
            latitude=float(lat),
            longitude=float(lon),
            radius_m=float(area.get("radius") or 0.0),
            last_location_time=data.get("lastLocationTime"),
            raw=data,
        )

    def get_connectivity(self, phone: str) -> ConnectivityResult:
        data, err = self._post(
            self.paths["connectivity"], {"device": {"phoneNumber": phone}},
        )
        if err:
            return ConnectivityResult(ok=False, error=err)
        status = data.get("connectivityStatus") or data.get("status")
        if status is None:
            return ConnectivityResult(ok=False, error="no connectivityStatus in response", raw=data)
        return ConnectivityResult(ok=True, status=str(status), raw=data)

    def check_device_swap(self, phone: str) -> SwapResult:
        # Verified body: {phoneNumber, maxAge in hours}. Response: {"swapped": bool}.
        data, err = self._post(
            self.paths["swap"], {"phoneNumber": phone, "maxAge": 120},
        )
        if err:
            return SwapResult(ok=False, error=err)
        swapped = data.get("swapped")
        if swapped is None:
            # Older/alternate shapes exposed a date field.
            swapped = data.get("latestDeviceChange") is not None
        return SwapResult(ok=True, swapped=bool(swapped), raw=data)

    def get_tenure(self, phone: str) -> TenureResult:
        # Nokia's kyc-tenure/v0.1 wants a tenureDate (the earliest date the
        # subject's tenure is being CHECKED against) and requires an
        # x-correlator UUID header. Response: {tenureDateCheck, contractType}.
        tenure_date = (
            datetime.now(timezone.utc) - timedelta(days=self.tenure_lookback_days)
        ).date().isoformat()
        data, err = self._post(
            self.paths["tenure"],
            {"phoneNumber": phone, "tenureDate": tenure_date},
            extra_headers={"x-correlator": str(uuid.uuid4())},
        )
        if err:
            return TenureResult(ok=False, error=err)
        return TenureResult(
            ok=True,
            tenure_date_check=data.get("tenureDateCheck"),
            contract_type=data.get("contractType"),
            raw=data,
        )
