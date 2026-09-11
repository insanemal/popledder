#!/usr/bin/env python3
"""Standalone news-ticker app for the 64x64 LED panel — no Flask server needed.

Connects to the panel over BLE, programs an endlessly-looping scrolling GIF
("I am in a meeting" by default), plays it, then disconnects cleanly and
leaves the panel looping on its own.

Usage:
    python3 meeting_status.py [--text "I am in a meeting"] [--color 0xFFFF00]
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

from PIL import Image, ImageDraw, ImageFont

from led_matrix_api import LedBleService
from led_matrix_api.config import Settings
from led_matrix_api.commands.high_level import (
    cmd_power_payload,
    cmd_brightness_payload_fixed,
    pkts_program_image_payloads,
)
from led_matrix_api.commands.protocol_builders import build_dispatch_play_payload

FONT_CANDIDATES = [
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMNerdFont-Regular.ttf",
    "/usr/share/fonts/TTF/RobotoMonoNerdFontMono-Regular.ttf",
]

DEFAULT_ADDRESS = os.environ.get("DEVICE_ADDRESS", "").strip() or "FF:25:12:09:30:DC"


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("No usable TTF font found; install Noto Sans or pass --font")


def render_ticker_frames(
    text: str,
    *,
    color: int,
    size: int,
    font_path: str,
    panel_w: int = 64,
    panel_h: int = 64,
    gap: int | None = None,
    step: int = 2,
) -> list[Image.Image]:
    """Build one panel-sized frame per horizontal shift of a two-copy text strip."""
    if gap is None:
        gap = panel_w
    font = ImageFont.truetype(font_path, size)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_w = int(probe.textlength(text, font=font))
    if text_w <= 0:
        raise ValueError("Text renders with zero width")

    # Strip layout: copy A at x=0, copy B at x=loop_len, plus one panel width
    # of trailer so the crop window never shows empty space.
    loop_len = text_w + gap  # shifting this far brings copy B into copy A's spot
    strip_w = loop_len + panel_w
    strip = Image.new("RGB", (strip_w, panel_h), (0, 0, 0))
    draw = ImageDraw.Draw(strip)
    fc = (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)
    for x in (0, loop_len):
        draw.text((x, panel_h // 2), text, font=font, fill=fc, anchor="lm")

    frames: list[Image.Image] = []
    for shift in range(0, loop_len, step):
        frames.append(strip.crop((shift, 0, shift + panel_w, panel_h)))
    return frames


def frames_to_gif(frames: list[Image.Image], frame_ms: int = 50) -> bytes:
    """Encode frames as an endlessly-looping GIF, like the panel's own builder."""
    pal = [
        fr.convert("P", palette=Image.Palette.ADAPTIVE, colors=256, dither=Image.Dither.NONE)
        for fr in frames
    ]
    buf = io.BytesIO()
    pal[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pal[1:],
        loop=0,
        duration=frame_ms,
        disposal=2,
        optimize=False,
    )
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="News-ticker message on the LED panel (direct BLE)")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="BLE address of the panel")
    parser.add_argument("--text", default="I am in a meeting", help="Message to scroll (single line)")
    parser.add_argument("--color", type=lambda s: int(s, 0), default=0xFFFF00,
                        help="Text color as decimal or 0xRRGGBB (default yellow)")
    parser.add_argument("--size", type=int, default=28,
                        help="Font em size in px. 28 = ~20px-tall letters, 4-5 chars visible (panel is 64px tall)")
    parser.add_argument("--step", type=int, default=2, help="Pixels shifted per frame (lower = smoother)")
    parser.add_argument("--frame-ms", type=int, default=50,
                        help="Milliseconds per frame (lower = faster scroll; 50 = slow, 35 = snappy)")
    parser.add_argument("--font", default=find_font(), help="TTF font path")
    parser.add_argument("--brightness", type=int, default=None, help="Panel brightness (1-15) to set")
    parser.add_argument("--power-on", action="store_true",
                        help="Send an explicit power-on command first (off by default; matches known-good sends)")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="Seconds to hold the BLE link after sending before disconnecting")
    parser.add_argument("--w", type=int, default=None, help="Panel width (default: auto-detect from device)")
    parser.add_argument("--h", type=int, default=None, help="Panel height (default: auto-detect from device)")
    args = parser.parse_args()

    text = " ".join(str(args.text).split())  # collapse newlines/whitespace

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
        print(f"  panel size: {pw}x{ph}")

        # ---- Build the program ----
        print(f"Rendering ticker: '{text}' ...")
        frames = render_ticker_frames(
            text, color=args.color, size=args.size, font_path=args.font,
            panel_w=pw, panel_h=ph, step=args.step,
        )
        gif = frames_to_gif(frames, frame_ms=args.frame_ms)
        payloads = pkts_program_image_payloads(
            settings=settings,
            image_bytes=gif,
            mode="gif",
            target_size=(pw, ph),
        )
        dispatch = build_dispatch_play_payload(id_pro=1, play_loop=65535, ignore_pgm_cmd=0)
        print(f"  {len(frames)} frames, GIF {len(gif) // 1024} KB, "
              f"{len(payloads) + 2} BLE payloads (~{sum(len(p) for p in payloads) // 1024} KB)")

        if args.brightness is not None:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                             payload=cmd_brightness_payload_fixed(args.brightness))
            print(f"  brightness -> {args.brightness}")

        if args.power_on:
            ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                             payload=cmd_power_payload(True))
            print("  power -> on")

        acked = 0
        missed = []
        for i, p in enumerate(payloads):
            r = ble.send_and_wait_ack_sync(flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
                                           payload=p, timeout_s=2.0)
            if r.get("acked"):
                acked += 1
            else:
                missed.append(i)
        print(f"  streamed {len(payloads)} payloads: acked={acked}, no-ack={len(missed)}")
        if missed:
            print("  first no-ack indices:", missed[:10])

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