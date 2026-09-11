#!/usr/bin/env python3
"""Pre-load multiple GIFs into the panel, one per program slot (direct BLE).

Uploads each file as its own program (slot = start_slot + index) WITHOUT
switching the display, so you can flip between them instantly afterwards with
play_slot.py instead of re-uploading every time.

Usage:
    python3 cli_tools/load_slots.py --files a.gif b.gif c.gif --start-slot 1
    python3 cli_tools/load_slots.py --files a.gif b.gif --start-slot 5 --w 64 --h 64
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
from led_matrix_api.commands.high_level import (
    cmd_power_payload,
    cmd_brightness_payload_fixed,
    pkts_program_image_payloads,
)

DEFAULT_ADDRESS = os.environ.get("DEVICE_ADDRESS", "").strip() or "FF:25:12:09:30:DC"


def main() -> int:
    parser = argparse.ArgumentParser(description="Load GIFs into panel slots (direct BLE)")
    parser.add_argument("--files", nargs="+", required=True, help="GIF files, in slot order")
    parser.add_argument("--start-slot", type=int, default=1, help="First slot number (default 1)")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="BLE address of the panel")
    parser.add_argument("--w", type=int, default=None, help="Panel width (default: auto-detect)")
    parser.add_argument("--h", type=int, default=None, help="Panel height (default: auto-detect)")
    parser.add_argument("--brightness", type=int, default=None, help="Panel brightness (1-15)")
    parser.add_argument("--power-on", action="store_true", help="Send power-on first (default off)")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="Seconds to hold the BLE link at the end before disconnecting")
    args = parser.parse_args()

    files = [Path(f) for f in args.files]
    for p in files:
        if not p.is_file():
            print(f"error: file not found: {p}", file=sys.stderr)
            return 2

    settings = Settings(
        device_address=args.address,
        ble_write_chunk=int(os.environ.get("BLE_WRITE_CHUNK", "180")),
        stream_chunk=int(os.environ.get("STREAM_CHUNK", "960")),
    )

    ble = LedBleService(args.address, settings=settings)
    results = []
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
        print(f"  panel size: {pw}x{ph}\n")

        if args.brightness is not None:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                             payload=cmd_brightness_payload_fixed(args.brightness))
        if args.power_on:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                             payload=cmd_power_payload(True))

        for i, path in enumerate(files):
            slot = args.start_slot + i
            gif = path.read_bytes()
            payloads = pkts_program_image_payloads(
                settings=settings,
                image_bytes=gif,
                mode="gif",
                target_size=(pw, ph),
                id_pro=slot,
            )
            acked = 0
            missed = []
            for idx, pk in enumerate(payloads):
                r = ble.send_and_wait_ack_sync(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                                               payload=pk, timeout_s=2.0)
                if r.get("acked"):
                    acked += 1
                else:
                    missed.append(idx)
            results.append({"slot": slot, "file": path.name, "acked": acked, "total": len(payloads)})
            status = "OK" if not missed else f"WARN ({len(missed)} no-ack)"
            print(f"slot {slot:>2} <- {path.name}: {acked}/{len(payloads)} acked {status}")

        # No dispatch here on purpose: loading must not switch the display.
        print("\nLoaded. Use play_slot.py --slot N to display any slot.")
        print(f"Holding link {args.settle}s, then disconnecting ...")
        time.sleep(args.settle)
        ble.disconnect()
        print("  disconnected")
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