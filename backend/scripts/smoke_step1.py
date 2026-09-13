"""Step 1 smoke test — exercise every adapter method against the scripted
client, then (if NOKIA keys are set) do the same against the live simulator.

Run:
    python -m scripts.smoke_step1        # from C:\\veritas\\backend
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

# Make backend/ importable when run as `python scripts/smoke_step1.py` too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veritas.camara import ScriptedCamaraClient  # noqa: E402
from veritas.camara.factory import get_camara_client  # noqa: E402
from veritas.camara.live import LiveCamaraClient  # noqa: E402
from veritas.config import SETTINGS  # noqa: E402


# A few real sites so we can also exercise a geofence miss.
SITES = {
    "Budapest HQ (simulator's home)": (47.486276, 19.079156, 500.0),
    "Riyadh Site A":                  (24.7136,  46.6753,  500.0),
    "Cairo Site B":                   (30.0444,  31.2357,  500.0),
}

PHONES = ["+99999991000", "+99999991001", "+99999991002", "+99999991004"]


def dump(label: str, obj) -> None:
    print(f"\n--- {label} ---")
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    print(json.dumps(obj, indent=2, default=str))


def exercise(client, tag: str) -> None:
    print(f"\n================ {tag} ================")
    for phone in PHONES:
        print(f"\n### phone={phone}")
        dump("retrieve_location", client.retrieve_location(phone))
        dump("get_connectivity", client.get_connectivity(phone))
        dump("check_device_swap", client.check_device_swap(phone))
        dump("get_tenure",       client.get_tenure(phone))
        for name, (lat, lon, radius) in SITES.items():
            gf = client.check_geofence(phone, lat, lon, radius)
            print(
                f"geofence vs {name!r}: ok={gf.ok}, inside={gf.inside}, "
                f"distance_m={gf.distance_m}, effective_radius_m={gf.effective_radius_m}"
            )


def main() -> int:
    print("Settings:")
    print(f"  camara_mode      = {SETTINGS.camara_mode}")
    print(f"  has_live_camara  = {SETTINGS.has_live_camara}")
    print(f"  has_supabase     = {SETTINGS.has_supabase}")
    print(f"  has_llm          = {SETTINGS.has_llm}")

    exercise(ScriptedCamaraClient(), "ScriptedCamaraClient")

    if SETTINGS.has_live_camara:
        try:
            exercise(LiveCamaraClient(), "LiveCamaraClient (Nokia Network as Code)")
        except Exception as exc:  # pragma: no cover — surfaced to user
            print(f"\n!!! LiveCamaraClient failed to initialize: {exc}")
            return 2
    else:
        print(
            "\n(skipping live test — set NOKIA_BASE_URL and NOKIA_API_KEY in "
            ".env to run against the simulator)"
        )

    default = get_camara_client()
    print(f"\nget_camara_client() -> {type(default).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
