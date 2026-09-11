#!/usr/bin/env python3
"""Display a pre-loaded program slot on loop (via web API).

Mirror of cli_tools/play_slot.py but talks to the API server. Instant content
swap with zero upload — pair with load_slots.py.

Usage:
    python3 api_tools/play_slot.py --slot 3
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from urllib import error, request


def api_post(base: str, path: str, payload: dict, timeout_s: float = 30.0) -> dict:
    req = request.Request(base + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Display a slot on loop (web API)")
    parser.add_argument("--slot", type=int, required=True, help="Program slot number to display")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--address", default=None,
                        help="Panel BLE address (defaults to server DEVICE_ADDRESS)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    payload: dict = {"mode": "single", "index": args.slot - 1, "ids": [args.slot]}
    if args.address:
        payload["address"] = args.address

    try:
        r = api_post(base, "/api/play-mode", payload)
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 1

    print(json.dumps(r, indent=2))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())