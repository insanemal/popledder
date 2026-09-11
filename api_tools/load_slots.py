#!/usr/bin/env python3
"""Pre-load multiple GIFs into the panel, one per program slot (via web API).

Mirror of cli_tools/load_slots.py but talks to the API server. Uploads each
file as its own program (slot = start_slot + index) WITHOUT switching the
display (sends play=0), so you can flip between them with play_slot.py.

Usage:
    python3 api_tools/load_slots.py --files a.gif b.gif c.gif --start-slot 1
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from pathlib import Path
from urllib import error, request

import urllib.parse


def post_multipart(url: str, fields: dict, file_field: str, filename: str,
                   data: bytes, timeout_s: float = 300.0) -> dict:
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(
            (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
             f"{v}\r\n").encode()
        )
    body.write(
        (f"--{boundary}\r\n"
         f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
         f"Content-Type: image/gif\r\n\r\n").encode()
    )
    body.write(data)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = request.Request(url, data=body.getvalue(),
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                          method="POST")
    with request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def api_get(base: str, path: str, timeout_s: float = 30.0) -> dict:
    with request.urlopen(base + path, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Load GIFs into panel slots (web API)")
    parser.add_argument("--files", nargs="+", required=True, help="GIF files, in slot order")
    parser.add_argument("--start-slot", type=int, default=1, help="First slot number (default 1)")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--address", default=None,
                        help="Panel BLE address (defaults to server DEVICE_ADDRESS)")
    parser.add_argument("--w", type=int, default=None, help="Panel width (default: auto-detect)")
    parser.add_argument("--h", type=int, default=None, help="Panel height (default: auto-detect)")
    parser.add_argument("--brightness", type=int, default=None, help="Panel brightness (1-15)")
    args = parser.parse_args()

    files = [Path(f) for f in args.files]
    for p in files:
        if not p.is_file():
            print(f"error: file not found: {p}", file=sys.stderr)
            return 2

    base = args.base_url.rstrip("/")
    q = ("?address=" + urllib.parse.quote(args.address)) if args.address else ""
    extra = {"address": args.address} if args.address else {}

    # Auto-detect panel size via the API (server caches it).
    pw, ph = args.w or 64, args.h or 64
    if args.w is None or args.h is None:
        try:
            info = api_get(base, "/api/device-info" + q)
            if info.get("size") and info["size"].get("w") and info["size"].get("h"):
                pw, ph = int(info["size"]["w"]), int(info["size"]["h"])
        except Exception as e:
            print(f"  (size auto-detect failed, using {pw}x{ph}: {e})", file=sys.stderr)
    print(f"  panel size: {pw}x{ph}\n")

    if args.brightness is not None:
        req = request.Request(
            f"{base}/api/brightness",
            data=json.dumps({"mode": "fixed", "value": args.brightness, "type": 0, **extra}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=10):
            pass

    results = []
    for i, path in enumerate(files):
        slot = args.start_slot + i
        try:
            result = post_multipart(
                f"{base}/api/image",
                fields={"mode": "gif", "w": pw, "h": ph, "id_pro": slot, "play": "0", **extra},
                file_field="file",
                filename=path.name,
                data=path.read_bytes(),
            )
        except error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"slot {slot:>2} <- {path.name}: HTTP {e.code} {body}", file=sys.stderr)
            return 1
        if not result.get("ok"):
            print(f"slot {slot:>2} <- {path.name}: FAILED {result.get('error')}", file=sys.stderr)
            return 1
        sent = result.get("sent", [])
        acked = sum(1 for s in sent if s.get("acked"))
        results.append({"slot": slot, "file": path.name, "acked": acked, "total": len(sent)})
        print(f"slot {slot:>2} <- {path.name}: {acked}/{len(sent)} acked")

    print("\nLoaded. Use play_slot.py --slot N to display any slot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())