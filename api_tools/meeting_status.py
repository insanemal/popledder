#!/usr/bin/env python3
"""News-ticker message via the web API (Flask server must be running).

Mirror of cli_tools/meeting_status.py but talks to the HTTP API instead of BLE.

Usage:
    python3 api_tools/meeting_status.py [--base-url http://127.0.0.1:5000]
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

# Reuse the renderer from the CLI tool (importing it is side-effect free).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli_tools"))
from meeting_status import render_ticker_frames, frames_to_gif, find_font  # noqa: E402


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


def post_json(url: str, payload: dict, timeout_s: float = 10.0) -> dict:
    req = request.Request(url, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="News-ticker message via the web API")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--address", default=None,
                        help="Panel BLE address (defaults to server DEVICE_ADDRESS)")
    parser.add_argument("--w", type=int, default=None, help="Panel width (default: auto-detect via API)")
    parser.add_argument("--h", type=int, default=None, help="Panel height (default: auto-detect via API)")
    parser.add_argument("--text", default="I am in a meeting")
    parser.add_argument("--color", type=lambda s: int(s, 0), default=0xFFFF00)
    parser.add_argument("--size", type=int, default=28)
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--frame-ms", type=int, default=50)
    parser.add_argument("--font", default=find_font())
    parser.add_argument("--brightness", type=int, default=None)
    args = parser.parse_args()

    text = " ".join(str(args.text).split())

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
    print(f"  panel size: {pw}x{ph}")

    frames = render_ticker_frames(text, color=args.color, size=args.size,
                                  font_path=args.font, panel_w=pw, panel_h=ph, step=args.step)
    gif = frames_to_gif(frames, frame_ms=args.frame_ms)

    if args.brightness is not None:
        post_json(f"{base}/api/brightness", {"mode": "fixed", "value": args.brightness, "type": 0, **extra})

    try:
        result = post_multipart(
            f"{base}/api/image",
            fields={"mode": "gif", "w": pw, "h": ph, **extra},
            file_field="file",
            filename="ticker.gif",
            data=gif,
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
    print(f"Sent ticker: {len(frames)} frames, {len(gif) // 1024} KB, "
          f"{len(sent)} payloads ({acked} acked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())