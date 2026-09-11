#!/usr/bin/env python3
"""Read-only probe: ask the 64x64 panel about itself (no display changes).

Sends only "get" commands (dev_info, param_dev, power, light, rotate,
pgm_play, pgm_key) and prints what the panel replies. Derived from the
official LOY SPACE app (beautified app-service.js in APK/.../www.beautified).

Usage:
    python3 cli_tools/panel_probe.py [--address XX:...] [--with-pgm-key]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

from led_matrix_api import LedBleService
from led_matrix_api.config import Settings

DEFAULT_ADDRESS = os.environ.get("DEVICE_ADDRESS", "").strip() or "FF:25:12:09:30:DC"

# Tag -> ack parser name (frame type 0x83 payloads)
TAGS = {
    4: "power", 6: "light", 10: "dev_info", 23: "param_io", 27: "param_dev",
    45: "pgm_flicker", 47: "rotate", 51: "ani_num", 52: "bluetooth",
    53: "pgm_key", 54: "pgm_play", 55: "pwd", 56: "global_ani",
    57: "channel_color", 58: "mirror", 62: "sensor",
}

# get-command payloads (from the official app's ge() builder): [tag, 0]
GETS = {
    "dev_info": bytes([10, 0]),
    "param_dev": bytes([27, 0]),
    "power": bytes([4, 0]),
    "light": bytes([6, 0]),
    "rotate": bytes([47, 0]),
    "pgm_play": bytes([54, 0]),
    "pgm_key": bytes([53, 0]),
    "pgm_flicker": bytes([45, 0]),
}


def decode_tag(tag: int, data: bytes) -> str:
    if tag == 54:  # pgm_play: model, index, ids_pro (1-based)
        if len(data) < 2:
            return "no data"
        ids = [b + 1 for b in data[2:]]
        return f"model={data[0]} index={data[1]} ids_pro={ids}"
    if tag == 53:  # pgm_key: ts_update (4B) or rows of [id_pro(1-based), 20-byte key]
        if len(data) == 4:
            return f"ts_update={int.from_bytes(data, 'big')}"
        if len(data) < 21:
            return "empty"
        rows = []
        i = 0
        while i + 21 <= len(data):
            rows.append((data[i] + 1, data[i + 1:i + 21].hex()))
            i += 21
        return f"{len(rows)} programs: " + ", ".join(f"pro={p}" for p, _ in rows)
    if tag == 10:  # dev_info: comma string
        return data.decode("utf-8", errors="replace")
    if tag == 27:  # param_dev: w u16le, h u16le
        if len(data) >= 4:
            return f"width={int.from_bytes(data[1:3], 'little')} height={int.from_bytes(data[3:5], 'little')}"
        return f"raw={data.hex()}"
    if tag == 4 and len(data) >= 1:
        return f"type={data[0]}"
    if tag == 6 and len(data) >= 1:
        return f"type={data[0]}"
    if tag == 47 and len(data) >= 1:
        return f"rotate={90 * data[0]}"
    if tag == 45 and len(data) >= 3:
        return f"count_pgm={data[1]} time={int.from_bytes(data[2:6], 'little')}"
    return f"raw={data.hex()}"


def varint_len(b: int) -> int:
    """d() from the app: 129 -> 2-byte len, 130 -> 3-byte, else 1-byte."""
    if b == 129:
        return 2
    if b == 130:
        return 3
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only panel probe")
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--gets", default="dev_info,param_dev,power,light,rotate,pgm_play",
                        help="Comma list of gets (see GETS keys; add pgm_key for full inventory)")
    parser.add_argument("--wait", type=float, default=2.0,
                        help="Seconds to listen for replies after each get")
    args = parser.parse_args()

    settings = Settings(device_address=args.address, ble_write_chunk=180)
    ble = LedBleService(args.address, settings=settings)

    try:
        print(f"Connecting to {args.address} ...")
        ble.connect()
        print("  connected\n")

        # pgm_key needs ids to fetch keys; parse "name=1,2,3" or "name" (empty) or "name=all"
        for g in [g.strip() for g in args.gets.split(",") if g.strip()]:
            name = g.split("=", 1)[0].strip()
            ids = None
            if "=" in g:
                spec = g.split("=", 1)[1].strip()
                if spec.lower() == "all":
                    ids = list(range(1, 61))
                elif spec:
                    ids = [int(x) for x in spec.split(",") if x.strip()]
            if name not in GETS:
                print(f"  (unknown get '{name}', skipping)")
                continue
            payload = GETS[name]
            if ids is not None:
                if len(ids) > 255:
                    print("  (too many ids, capping at 255)")
                    ids = ids[:255]
                payload = bytes([53, len(ids)]) + bytes(i - 1 for i in ids)
                name = "pgm_key(ids)"
            # Commands (get included) are sent as msg_type 0x03 per the official
            # app (ye(): w=3 for 'get'); rt_show programs use type 0x02.
            ble.send_payload(flags=settings.rt_show_flags, msg_type=0x03,
                             payload=payload)
            time.sleep(args.wait)
            print(f"--- get: {name} ({payload.hex()}) ---")
            found = 0
            match_name = name.split("(")[0]
            for f in ble.parsed_frames[-30:]:
                if f.msg_type != 0x83 or len(f.payload) < 2:
                    continue
                tag = f.payload[0]
                if tag not in TAGS or TAGS[tag] != match_name:
                    continue
                ll = varint_len(f.payload[1]) if len(f.payload) > 1 else 1
                off = 1 + ll
                data = f.payload[off:off + 256]
                print(f"  [{TAGS[tag]}] {decode_tag(tag, data)}")
                found += 1
            if not found:
                print("  (no ack seen)")
            print()
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 130
    finally:
        try:
            ble.disconnect()
        except Exception:
            pass
        print("disconnected")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())