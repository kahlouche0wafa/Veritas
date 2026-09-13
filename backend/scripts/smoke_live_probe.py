"""Targeted live-simulator probe for Section 1 verification.

Hits ONLY the three cases documented during the idea phase, prints the
raw JSON returned by each, and prints a PASS/FAIL against expectations.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from veritas.camara.live import LiveCamaraClient  # noqa: E402
from veritas.config import SETTINGS  # noqa: E402


EXPECT_LAT, EXPECT_LON = 47.486276, 19.079156
LAT_LON_TOLERANCE_DEG = 0.05  # ~5 km — accounts for the simulator's radius


def dist_deg(lat, lon, target_lat, target_lon) -> float:
    return max(abs(lat - target_lat), abs(lon - target_lon))


def main() -> int:
    print(f"NOKIA_BASE_URL     = {SETTINGS.nokia_base_url}")
    print(f"NOKIA_API_KEY set  = {bool(SETTINGS.nokia_api_key)}")
    print(f"has_live_camara    = {SETTINGS.has_live_camara}\n")

    if not SETTINGS.has_live_camara:
        print("!! Nokia env vars not set — aborting live probe.")
        return 2

    client = LiveCamaraClient()

    # ---- Case 1: retrieveLocation on +99999991000 -------------------
    print("=" * 60)
    print("CASE 1: retrieveLocation on +99999991000")
    print(f"  expected: coordinates near ({EXPECT_LAT}, {EXPECT_LON})")
    loc = client.retrieve_location("+99999991000")
    print(f"  raw: {json.dumps(loc.raw, indent=2)[:400]}")
    if loc.ok and loc.latitude is not None:
        d = dist_deg(loc.latitude, loc.longitude, EXPECT_LAT, EXPECT_LON)
        ok = d <= LAT_LON_TOLERANCE_DEG
        print(f"  parsed: lat={loc.latitude}  lon={loc.longitude}  radius_m={loc.radius_m}")
        print(f"  Δ from expected: {d:.6f} deg (~{d*111:.1f} km)")
        print(f"  RESULT: {'PASS ✓' if ok else 'FAIL ✗ (unexpected location)'}")
    else:
        print(f"  RESULT: FAIL ✗ — error: {loc.error}")

    # ---- Case 2: getConnectivityStatus on +99999991002 --------------
    print("\n" + "=" * 60)
    print("CASE 2: getConnectivityStatus on +99999991002")
    print("  expected: NOT_CONNECTED")
    conn = client.get_connectivity("+99999991002")
    print(f"  raw: {json.dumps(conn.raw, indent=2)[:400]}")
    if conn.ok:
        ok = conn.status == "NOT_CONNECTED"
        print(f"  parsed: status={conn.status!r}")
        print(f"  RESULT: {'PASS ✓' if ok else f'FAIL ✗ (got {conn.status!r})'}")
    else:
        print(f"  RESULT: FAIL ✗ — error: {conn.error}")

    # ---- Case 3: checkDeviceSwap TRUE preset ------------------------
    # Nokia's Network as Code exposes a named preset
    # DEVICE_SWAP_CHECK_TRUE_PHONE_NUMBER; different tenants use different
    # numbers for it. We try the common ones and report which returned TRUE.
    print("\n" + "=" * 60)
    print("CASE 3: checkDeviceSwap — expecting swapped=true on TRUE preset")
    candidates = ["+3637123456", "+36701234567", "+99999991004", "+3637020304"]
    for phone in candidates:
        sw = client.check_device_swap(phone)
        print(f"  {phone}: ok={sw.ok} swapped={sw.swapped} raw={json.dumps(sw.raw)[:120]}"
              + (f"  error={sw.error}" if sw.error else ""))
    print("\n  RESULT: look for one of the above with swapped=true (that's your TRUE preset number)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
