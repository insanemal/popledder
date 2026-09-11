#!/usr/bin/env python3
"""Display a GIF via the web API (Flask server must be running).

Mirror of cli_tools/show_gif.py but talks to the HTTP API instead of BLE.

Usage:
    python3 api_tools/show_gif.py --file path/to/gif.gif
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.parse
import uuid
from pathlib import Path
from urllib import error, request

from PIL import Image


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Show a GIF on the panel via the web API")
    parser.add_argument("--file", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--address", default=None,
                        help="Panel BLE address (defaults to server DEVICE_ADDRESS)")
    parser.add_argument("--w", type=int, default=None)
    parser.add_argument("--h", type=int, default=None)
    parser.add_argument("--brightness", type=int, default=None)
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    try:
        img = Image.open(str(path))
        n_frames = getattr(img, "n_frames", 1)
        src = img.size
    except Exception as e:
        print(f"error: not a readable image ({e})", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    extra = {"address": args.address} if args.address else {}

    pw, ph = args.w or 64, args.h or 64
    if args.w is None or args.h is None:
        try:
            q = ("?address=" + urllib.parse.quote(args.address)) if args.address else ""
            req = request.Request(base + "/api/device-info" + q)
            with request.urlopen(req, timeout=30) as resp:
                info = json.loads(resp.read().decode())
            if info.get("size") and info["size"].get("w") and info["size"].get("h"):
                pw, ph = int(info["size"]["w"]), int(info["size"]["h"])
        except Exception as e:
            print(f"  (size auto-detect failed, using {pw}x{ph}: {e})", file=sys.stderr)
    print(f"Source: {src[0]}x{src[1]}, {n_frames} frame(s) -> will scale to {pw}x{ph}")

    if args.brightness is not None:
        req = request.Request(
            f"{base}/api/brightness",
            data=json.dumps({"mode": "fixed", "value": args.brightness, "type": 0, **extra}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=10) as r:
            r.read()

    try:
        result = post_multipart(
            f"{base}/api/image",
            fields={"mode": "gif", "w": pw, "h": ph, **extra},
            file_field="file",
            filename=path.name,
            data=path.read_bytes(),
        )
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"Failed: {result.get('error')}", file=sys.stderr)
        return 1
    sent = result.get("sent", [])
    acked = sum(1 for s in sent if s.get("acked"))
    print(f"Sent {path.name}: {len(sent)} payloads ({acked} acked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())