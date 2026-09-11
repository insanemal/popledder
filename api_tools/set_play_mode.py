#!/usr/bin/env python3
"""Set/query the panel play mode via the web API (Flask server must be running).

Mirror of cli_tools/set_play_mode.py but uses the API endpoints.

Usage:
    python3 api_tools/set_play_mode.py --get
    python3 api_tools/set_play_mode.py --mode single --index 0 --ids 1
    python3 api_tools/set_play_mode.py --mode list --ids 1
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from urllib import error, request


def api_get(base: str, path: str, timeout_s: float = 20.0) -> dict:
    with request.urlopen(base + path, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def api_post(base: str, path: str, payload: dict, timeout_s: float = 30.0) -> dict:
    req = request.Request(base + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Panel play-mode control via web API")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--address", default=None,
                        help="Panel BLE address (defaults to server DEVICE_ADDRESS)")
    parser.add_argument("--get", action="store_true", help="Just query current play mode")
    parser.add_argument("--mode", choices=["single", "loop", "list"])
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()

    if not args.get and args.mode is None:
        print("error: pass --get and/or --mode ...", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    q = ("?address=" + urllib.parse.quote(args.address)) if args.address else ""
    try:
        if args.get:
            r = api_get(base, "/api/play-mode" + q)
            print(json.dumps(r, indent=2))
        if args.mode:
            payload: dict = {"mode": args.mode}
            if args.address:
                payload["address"] = args.address
            if args.index is not None:
                payload["index"] = args.index
            if args.ids:
                payload["ids"] = [int(x) for x in args.ids.split(",") if x.strip()]
            print(f"--- set {args.mode} ---")
            r = api_post(base, "/api/play-mode", payload)
            print(json.dumps(r, indent=2))
            if not r.get("ok"):
                return 1
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())