#!/usr/bin/env python3
"""Display a pre-loaded program slot on loop (direct BLE).

Switches the panel to single mode playing slot N — instant content swap with
zero upload. Pair with load_slots.py.

Usage:
    python3 cli_tools/play_slot.py --slot 3
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


def pgm_play_single(slot: int) -> bytes:
    """pgm_play set: single mode (model 1), queue [slot], index slot-1."""
    slot = max(1, int(slot))
    body = bytes([0x01, (slot - 1) & 0xFF, (slot - 1) & 0xFF])
    return bytes([0x36]) + encode_len(len(body)) + body


def read_play_state(ble, settings) -> dict | None:
    ble.send_payload(flags=settings.rt_show_flags, msg_type=0x03, payload=bytes([0x36, 0x00]))
    time.sleep(1.2)
    for f in ble.parsed_frames[-20:]:
        if f.msg_type == 0x83 and f.payload and f.payload[0] == 0x36:
            d = f.payload[2:]
            if len(d) >= 2:
                return {
                    "model": d[0],
                    "index": d[1],
                    "ids_pro": [b + 1 for b in d[2:]],
                }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Display a slot on loop (direct BLE)")
    parser.add_argument("--slot", type=int, required=True, help="Program slot number to display")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="BLE address of the panel")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="Seconds to hold the BLE link at the end before disconnecting")
    args = parser.parse_args()

    settings = Settings(
        device_address=args.address,
        ble_write_chunk=int(os.environ.get("BLE_WRITE_CHUNK", "180")),
        stream_chunk=int(os.environ.get("STREAM_CHUNK", "960")),
    )

    ble = LedBleService(args.address, settings=settings)
    try:
        print(f"Connecting to {args.address} ...")
        ble.connect()
        print("  connected")
        print(f"  displaying slot {args.slot} (single mode, loop forever)")
        ble.send_payload(flags=settings.rt_show_flags, msg_type=0x02,
                         payload=pgm_play_single(args.slot))
        time.sleep(1.5)
        state = read_play_state(ble, settings)
        print(f"  state: {state}")
        if state and state["model"] != 1:
            print("  (panel may have accepted asynchronously; run --slot again if needed)")
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