"""Scripted CAMARA client — deterministic per-phone presets.

Used for demo reliability and as a fallback when the live simulator is down.
Presets mirror the real Nokia Network as Code simulator behavior observed in
live testing:

  * most devices report the same fixed location around Budapest
    (47.486276, 19.079156) — so demo variety comes from varying the CLAIMED
    site, not the device
  * +99999991002 reliably returns NOT_CONNECTED (the INSUFFICIENT scenario)

Unknown phone numbers fall back to the default preset (connected, Budapest,
no swap) so free-text claims always resolve to something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .base import (
    CamaraClient,
    ConnectivityResult,
    LocationResult,
    SwapResult,
    TenureResult,
)

# The fixed location the simulator reports for its test devices.
SIM_LAT = 47.486276
SIM_LON = 19.079156
SIM_RADIUS_M = 1000.0


@dataclass
class DevicePreset:
    connectivity: str = "CONNECTED_DATA"
    latitude: float = SIM_LAT
    longitude: float = SIM_LON
    location_radius_m: float = SIM_RADIUS_M
    swapped: bool = False
    tenure_date_check: bool = True
    contract_type: str = "PAYM"  # pay-monthly
    location_available: bool = True
    notes: str = ""


PRESETS: dict[str, DevicePreset] = {
    # Healthy device at the simulator's fixed Budapest location.
    "+99999991000": DevicePreset(notes="baseline connected device"),
    "+99999991001": DevicePreset(notes="baseline connected device (2nd subject)"),
    # Mirrors the real simulator: this number is reliably NOT_CONNECTED.
    "+99999991002": DevicePreset(
        connectivity="NOT_CONNECTED",
        location_available=False,
        notes="unreachable device — INSUFFICIENT scenario",
    ),
    "+99999991003": DevicePreset(notes="baseline connected device (3rd subject)"),
    # Recently swapped device (mirrors DEVICE_SWAP_CHECK_TRUE preset).
    "+99999991004": DevicePreset(
        swapped=True, tenure_date_check=False, contract_type="PAYG",
        notes="recent device swap — degraded-trust scenario",
    ),
}

DEFAULT_PRESET = DevicePreset(notes="default preset (unknown number)")


class ScriptedCamaraClient(CamaraClient):
    def __init__(self, presets: dict[str, DevicePreset] | None = None):
        self.presets = presets or PRESETS

    def _preset(self, phone: str) -> DevicePreset:
        return self.presets.get(phone, DEFAULT_PRESET)

    def retrieve_location(self, phone: str) -> LocationResult:
        p = self._preset(phone)
        if not p.location_available:
            return LocationResult(
                ok=False,
                error="LOCATION_NOT_AVAILABLE: device not reachable",
                raw={"scripted": True, "phone": phone},
            )
        last_seen = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        return LocationResult(
            ok=True,
            latitude=p.latitude,
            longitude=p.longitude,
            radius_m=p.location_radius_m,
            last_location_time=last_seen,
            raw={
                "scripted": True,
                "lastLocationTime": last_seen,
                "area": {
                    "areaType": "CIRCLE",
                    "center": {"latitude": p.latitude, "longitude": p.longitude},
                    "radius": p.location_radius_m,
                },
            },
        )

    def get_connectivity(self, phone: str) -> ConnectivityResult:
        p = self._preset(phone)
        return ConnectivityResult(
            ok=True,
            status=p.connectivity,
            raw={"scripted": True, "connectivityStatus": p.connectivity},
        )

    def check_device_swap(self, phone: str) -> SwapResult:
        p = self._preset(phone)
        return SwapResult(ok=True, swapped=p.swapped, raw={"scripted": True, "swapped": p.swapped})

    def get_tenure(self, phone: str) -> TenureResult:
        p = self._preset(phone)
        return TenureResult(
            ok=True,
            tenure_date_check=p.tenure_date_check,
            contract_type=p.contract_type,
            raw={
                "scripted": True,
                "tenureDateCheck": p.tenure_date_check,
                "contractType": p.contract_type,
            },
        )
