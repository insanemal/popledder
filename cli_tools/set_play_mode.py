#!/usr/bin/env python3
"""Set or query the panel's play mode over direct BLE (no server needed).

Play modes map to the pgm_play command (tag 54), derived from the official
LOY SPACE app:

    single --index N  -> model 1: play ONLY the program at queue index N
    loop   [--index]  -> model 0: cycle the whole queue (glyph: gif once,
                        then empty slots/attract, then gif again, ...)
    list   --ids A,B  -> model 2: loop a custom subset of programs

Usage:
    python3 cli_tools/set_play_mode.py --get
    python3 cli_tools/set_play_mode.py --mode single --index 0 --ids 1
    python3 cli_tools/set_play_mode.py --mode list --ids 1
    python3 cli_tools/set_play_mode.py --mode loop --ids 1
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
from led_matrix_api.protocol.encoding import encode_len

DEFAULT_ADDRESS = os.environ.get("DEVICE_ADDRESS", "").strip() or "FF:25:12:09:30:DC"

MODELS = {"single": 1, "loop": 0, "list": 2}


def pgm_play_payload(model: int, index: int, ids: list[int]) -> bytes:
    """Build a pgm_play set payload: [54, len, model, index, (id-1)...]."""
    ids = [max(1, int(x)) for x in ids]
    body = bytearray([model & 0xFF, index & 0xFF])
    for p in ids:
        body.append((p - 1) & 0xFF)
    return bytes([54]) + encode_len(len(body)) + bytes(body)


def decode_pgm_play(data: bytes) -> str:
    if len(data) < 2:
        return "no data"
    ids = [b + 1 for b in data[2:]]
    return (f"model={data[0]} index={data[1]} ids_pro={ids} "
            f"({len(ids)} slots in queue)")


def do_get(ble, settings, wait: float) -> None:
    ble.send_payload(flags=settings.rt_show_flags, msg_type=0x03,
                     payload=bytes([54, 0]))
    time.sleep(wait)
    for f in ble.parsed_frames[-30:]:
        if f.msg_type == 0x83 and f.payload and f.payload[0] == 54:
            print(f"  current: {decode_pgm_play(f.payload[2:])}")
            return
    print("  (no pgm_play reply)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Panel play-mode control (direct BLE)")
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--get", action="store_true", help="Just query current play mode")
    parser.add_argument("--mode", choices=list(MODELS), help="single | loop | list")
    parser.add_argument("--index", type=int, default=None,
                        help="Queue index for single (0 = first program)")
    parser.add_argument("--ids", default="",
                        help="Comma program ids for the queue/ids_pro (e.g. 1 or 1,3,5)")
    parser.add_argument("--wait", type=float, default=2.0,
                        help="Seconds to listen for the reply")
    args = parser.parse_args()

    if not args.get and args.mode is None:
        print("error: pass --get and/or --mode ...", file=sys.stderr)
        return 2

    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else []

    settings = Settings(device_address=args.address, ble_write_chunk=180)
    ble = LedBleService(args.address, settings=settings)

    try:
        print(f"Connecting to {args.address} ...")
        ble.connect()
        print("  connected\n")

        if args.get:
            print("--- get pgm_play ---")
            do_get(ble, settings, args.wait)

        if args.mode:
            model = MODELS[args.mode]
            index = args.index if args.index is not None else 0
            payload = pgm_play_payload(model, index, ids or [1])
            print(f"--- set pgm_play model={model} ({args.mode}) index={index} ids={ids or [1]} ---")
            print(f"  payload: {payload.hex()}")
            # SET commands go out as frame type 0x02 (only gets use 0x03)
            ble.send_payload(flags=settings.rt_show_flags, msg_type=0x02,
                             payload=payload)
            time.sleep(args.wait)
            print("  ack:")
            do_get(ble, settings, 0.5)
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