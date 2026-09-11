#!/usr/bin/env python3
"""Read-only panel probe via the web API (Flask server must be running).

Mirror of cli_tools/panel_probe.py but uses the API endpoints.

Usage:
    python3 api_tools/panel_probe.py [--base-url http://127.0.0.1:5000]
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib import error, request

import urllib.parse


def api_get(base: str, path: str, timeout_s: float = 30.0) -> dict:
    with request.urlopen(base + path, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only panel probe via web API")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--address", default=None,
                        help="Panel BLE address (defaults to server DEVICE_ADDRESS)")
    parser.add_argument("--programs", action="store_true", help="Also dump per-slot keys")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    q = ("?address=" + urllib.parse.quote(args.address)) if args.address else ""
    try:
        dev = api_get(base, "/api/device-info" + q)
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 1

    print("=== device-info ===")
    print(json.dumps(dev, indent=2))

    try:
        mode = api_get(base, "/api/play-mode" + q)
        print("\n=== play-mode ===")
        print(json.dumps(mode, indent=2))
    except Exception as e:
        print(f"\nplay-mode failed: {e}")

    if args.programs:
        try:
            progs = api_get(base, "/api/programs" + q)
            print("\n=== programs (pgm_key) ===")
            print(json.dumps(progs, indent=2))
        except Exception as e:
            print(f"\nprograms failed: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())