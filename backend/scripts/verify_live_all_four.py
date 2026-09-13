"""Verify all four Nokia endpoints hit real data.

Fires ONE call per endpoint (4 total) with a small wait between each so
we don't trigger the RapidAPI rate limit. Prints the raw JSON so it can
be compared to the portal-verified curl output byte for byte.

Stops immediately on any HTTP 429.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from veritas.config import SETTINGS  # noqa: E402  — loads .env
from veritas.camara.live import LiveCamaraClient  # noqa: E402
_ = SETTINGS  # keep referenced


PHONE_MAIN = "+99999991000"
PHONE_UNREACHABLE = "+99999991002"


def dump(label, obj):
    print(f"\n--- {label} ---")
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str)[:800])


def check_429(err):
    if err and "HTTP 429" in err:
        print("\n!!! 429 Too Many Requests — STOPPING per instruction.")
        sys.exit(3)


def main() -> int:
    client = LiveCamaraClient()
    print(f"base_url = {client.base_url}")
    print(f"paths    = {json.dumps(client.paths, indent=2)}")
    print()

    # 1. Location
    print("=== [1/4] retrieveLocation on +99999991000 ===")
    loc = client.retrieve_location(PHONE_MAIN)
    check_429(loc.error)
    dump("LocationResult", {"ok": loc.ok, "latitude": loc.latitude, "longitude": loc.longitude,
                            "radius_m": loc.radius_m, "raw": loc.raw, "error": loc.error})
    time.sleep(3)

    # 2. Connectivity
    print("\n=== [2/4] getConnectivityStatus on +99999991002 ===")
    conn = client.get_connectivity(PHONE_UNREACHABLE)
    check_429(conn.error)
    dump("ConnectivityResult", {"ok": conn.ok, "status": conn.status, "raw": conn.raw, "error": conn.error})
    time.sleep(3)

    # 3. Device Swap
    print("\n=== [3/4] checkDeviceSwap on +99999991000 ===")
    swap = client.check_device_swap(PHONE_MAIN)
    check_429(swap.error)
    dump("SwapResult", {"ok": swap.ok, "swapped": swap.swapped, "raw": swap.raw, "error": swap.error})
    time.sleep(3)

    # 4. KYC Tenure
    print("\n=== [4/4] checkTenure on +99999991000 ===")
    ten = client.get_tenure(PHONE_MAIN)
    check_429(ten.error)
    dump("TenureResult", {"ok": ten.ok, "tenure_date_check": ten.tenure_date_check,
                          "contract_type": ten.contract_type, "raw": ten.raw, "error": ten.error})

    # Summary
    print("\n\n=== SUMMARY ===")
    all_ok = loc.ok and conn.ok and swap.ok and ten.ok
    print(f"  retrieveLocation:       {'PASS' if loc.ok else 'FAIL'}  ({loc.latitude},{loc.longitude})")
    print(f"  getConnectivityStatus:  {'PASS' if conn.ok else 'FAIL'}  ({conn.status})")
    print(f"  checkDeviceSwap:        {'PASS' if swap.ok else 'FAIL'}  (swapped={swap.swapped})")
    print(f"  checkTenure:            {'PASS' if ten.ok else 'FAIL'}  (tenure_date_check={ten.tenure_date_check}, contract={ten.contract_type})")
    print(f"\n  ALL FOUR LIVE: {'YES' if all_ok else 'NO'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
