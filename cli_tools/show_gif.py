#!/usr/bin/env python3
"""Display a GIF on the 64x64 LED panel over direct BLE (no server needed).

Scales the GIF down to fit the panel (default 64x64), preserving any
animation frames, then programs it to loop forever and disconnects.

Usage:
    python3 show_gif.py --file path/to/your.gif
    python3 show_gif.py --file dance.gif --w 64 --h 64 --brightness 12
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

from PIL import Image

from led_matrix_api import LedBleService
from led_matrix_api.config import Settings
from led_matrix_api.commands.high_level import (
    cmd_power_payload,
    cmd_brightness_payload_fixed,
    pkts_program_image_payloads,
)
from led_matrix_api.commands.protocol_builders import build_dispatch_play_payload

DEFAULT_ADDRESS = os.environ.get("DEVICE_ADDRESS", "").strip() or "FF:25:12:09:30:DC"


def send_acked(ble, settings, payloads, *, timeout_s: float = 2.0):
    """Send payloads one at a time, waiting for the panel's ACK on each."""
    acked = 0
    missed = []
    for i, p in enumerate(payloads):
        r = ble.send_and_wait_ack_sync(flags=settings.rt_show_flags,
                                       msg_type=settings.rt_show_type,
                                       payload=p, timeout_s=timeout_s)
        if r.get("acked"):
            acked += 1
        else:
            missed.append(i)
    print(f"  streamed {len(payloads)} payloads: acked={acked}, no-ack={len(missed)}")
    if missed:
        print("  first no-ack indices:", missed[:10])
    return missed


def main() -> int:
    parser = argparse.ArgumentParser(description="Show a GIF on the LED panel (direct BLE)")
    parser.add_argument("--file", required=True, help="Path to the GIF to display")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="BLE address of the panel")
    parser.add_argument("--w", type=int, default=None, help="Target width in px (default: auto-detect)")
    parser.add_argument("--h", type=int, default=None, help="Target height in px (default: auto-detect)")
    parser.add_argument("--brightness", type=int, default=None, help="Panel brightness (1-15) to set")
    parser.add_argument("--power-on", action="store_true",
                        help="Send an explicit power-on command first (off by default)")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="Seconds to hold the BLE link after sending before disconnecting")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    # Quick sanity check that PIL can read it and report frame count / size.
    try:
        img = Image.open(str(path))
        n_frames = getattr(img, "n_frames", 1)
        src = img.size
        img.seek(0)
    except Exception as e:
        print(f"error: not a readable image ({e})", file=sys.stderr)
        return 2

    gif_bytes = path.read_bytes()
    settings = Settings(
        device_address=args.address,
        ble_write_chunk=int(os.environ.get("BLE_WRITE_CHUNK", "180")),
        stream_chunk=int(os.environ.get("STREAM_CHUNK", "960")),
    )

    # ---- Connect first (needed for size auto-detection) ----
    ble = LedBleService(args.address, settings=settings)
    try:
        print(f"Connecting to {args.address} ...")
        ble.connect()
        print("  connected")

        pw, ph = args.w or 64, args.h or 64
        if args.w is None or args.h is None:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=0x03, payload=bytes([0x1B, 0x00]))
            time.sleep(1.2)
            for f in ble.parsed_frames[-20:]:
                if f.msg_type == 0x83 and f.payload and f.payload[0] == 0x1B:
                    d = f.payload[2:]
                    if len(d) >= 5:
                        pw = int.from_bytes(d[1:3], "little")
                        ph = int.from_bytes(d[3:5], "little")
        print(f"Source: {src[0]}x{src[1]}, {n_frames} frame(s) -> will scale to {pw}x{ph}")

        # ---- Build the program ----
        payloads = pkts_program_image_payloads(
            settings=settings,
            image_bytes=gif_bytes,
            mode="gif",  # preserve animated GIF frames, scaled to target
            target_size=(pw, ph),
        )
        dispatch = build_dispatch_play_payload(id_pro=1, play_loop=65535, ignore_pgm_cmd=0)
        print(f"  {len(payloads) + 2} BLE payloads (~{sum(len(p) for p in payloads) // 1024} KB)")

        if args.brightness is not None:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                             payload=cmd_brightness_payload_fixed(args.brightness))
            print(f"  brightness -> {args.brightness}")

        if args.power_on:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                             payload=cmd_power_payload(True))
            print("  power -> on")

        send_acked(ble, settings, payloads)

        ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                         payload=dispatch)
        print("  dispatch play (loop forever)")

        print(f"Holding link {args.settle}s, then disconnecting ...")
        time.sleep(args.settle)
        print("  disconnecting ...")
        ble.disconnect()
        print("  disconnected (panel keeps looping on its own)")
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 130
    finally:
        try:
            if ble.is_connected():
                ble.disconnect()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
