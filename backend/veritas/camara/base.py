"""Abstract CAMARA client interface.

Every network signal VERITAS can query goes through this interface, so the
verdict engine never knows whether it is talking to the live Nokia Network as
Code simulator or the scripted demo client.

Design note: check_geofence deliberately does NOT use CAMARA's
verifyLocation endpoint (it returns FALSE for every input in the simulator,
including the platform's own documented TRUE example). Instead it pulls
retrieveLocation and does its own haversine distance check against the
claim's stated site. This is the architecture, not a workaround.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LocationResult:
    ok: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_m: Optional[float] = None
    last_location_time: Optional[str] = None
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)
    queried_at: str = field(default_factory=_now_iso)


@dataclass
class ConnectivityResult:
    ok: bool
    status: Optional[str] = None  # e.g. "CONNECTED_DATA" | "CONNECTED_SMS" | "NOT_CONNECTED"
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)
    queried_at: str = field(default_factory=_now_iso)

    @property
    def reachable(self) -> bool:
        return self.ok and self.status is not None and self.status != "NOT_CONNECTED"


@dataclass
class SwapResult:
    ok: bool
    swapped: Optional[bool] = None  # device swapped within the check period
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)
    queried_at: str = field(default_factory=_now_iso)


@dataclass
class TenureResult:
    ok: bool
    tenure_date_check: Optional[bool] = None
    contract_type: Optional[str] = None
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)
    queried_at: str = field(default_factory=_now_iso)


@dataclass
class GeofenceResult:
    """Outcome of VERITAS's own geofence computation (retrieveLocation + haversine)."""

    ok: bool
    inside: Optional[bool] = None
    distance_m: Optional[float] = None
    effective_radius_m: Optional[float] = None
    location: Optional[LocationResult] = None
    error: Optional[str] = None
    queried_at: str = field(default_factory=_now_iso)


class CamaraClient(ABC):
    """Interface to the CAMARA / Open Gateway network APIs."""

    @abstractmethod
    def retrieve_location(self, phone: str) -> LocationResult: ...

    @abstractmethod
    def get_connectivity(self, phone: str) -> ConnectivityResult: ...

    @abstractmethod
    def check_device_swap(self, phone: str) -> SwapResult: ...

    @abstractmethod
    def get_tenure(self, phone: str) -> TenureResult: ...

    def check_geofence(
        self, phone: str, site_lat: float, site_lon: float, radius_m: float
    ) -> GeofenceResult:
        """VERITAS-owned geofence check: retrieveLocation + haversine.

        The device is 'inside' if the distance from its reported position to
        the site center is within (site radius + reported location accuracy
        radius). The network's own location uncertainty is granted in the
        subject's favor.
        """
        loc = self.retrieve_location(phone)
        if not loc.ok or loc.latitude is None or loc.longitude is None:
            return GeofenceResult(ok=False, location=loc, error=loc.error or "no location returned")
        dist = haversine_m(loc.latitude, loc.longitude, site_lat, site_lon)
        effective = radius_m + (loc.radius_m or 0.0)
        return GeofenceResult(
            ok=True,
            inside=dist <= effective,
            distance_m=round(dist, 1),
            effective_radius_m=effective,
            location=loc,
        )
