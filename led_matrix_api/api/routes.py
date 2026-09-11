from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List
from pathlib import Path

from flask import Blueprint, jsonify, request, render_template
from types import SimpleNamespace
from werkzeug.utils import secure_filename

from ..ble.service import LedBleService
from ..config import Settings
from ..commands.high_level import (
    cmd_power_payload,
    cmd_brightness_payload_fixed,
    cmd_brightness_payload_schedule,
    rt_show_text_payloads,
    pkts_program_gif_payloads,
    pkts_program_image_payloads,
)
from ..commands.protocol_builders import build_dispatch_play_payload
from ..protocol.encoding import encode_len
from ..models.animations import TextAnimation, TEXT_ANIMATION_ALIASES, TEXT_ANIMATION_DESCRIPTIONS, parse_text_animation, clamp_anim_speed
from ..utils.hex import ack_to_dict

PRESET_IMAGE_DIR = Path(__file__).resolve().parents[1] / "static" / "presets"
PRESET_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# --------------------------------------------------------------------------
# Connection model
# --------------------------------------------------------------------------
# Every BLE-touching endpoint follows: resolve address -> connect -> act ->
# disconnect (always, via _ble_session). We never hold a link between
# requests; holding one wedges the OS BLE stack (panel becomes unreachable
# by both this server AND the CLI tools).
# --------------------------------------------------------------------------
_LOCK = threading.Lock()
_RECENT_FRAMES: List = []  # protocol.frame.ParsedFrame (for /api/debug/frames)
_RECENT_ACKS: List = []    # ble.acks.AckEvent (for /api/debug/acks)

# Per-address panel size cache: {address: (w, h, timestamp)}. TTL from
# Settings.panel_size_cache_seconds (0 = unlimited). Stateless API, but panels
# don't change size, so re-querying every request is wasteful.
_SIZE_CACHE: Dict[str, tuple] = {}


def _panel_size(settings: Settings, ble: LedBleService, address: str, w: int | None = None, h: int | None = None) -> tuple:
    """Resolve a panel's (width, height): explicit w/h win, then cache,
    then one param_dev query. Falls back to 64x64 on failure."""
    if w is not None and h is not None and w > 0 and h > 0:
        return int(w), int(h)
    key = address
    ttl = settings.panel_size_cache_seconds
    now = time.time()
    with _LOCK:
        hit = _SIZE_CACHE.get(key)
        if hit and (ttl == 0 or now - hit[2] < ttl):
            return hit[0], hit[1]
    pw = ph = 64
    p = _send_get(ble, settings, 27, bytes([0x1B, 0x00]), wait_s=1.2)
    if p:
        d = _payload_data(p)
        if len(d) >= 5:
            pw, ph = int.from_bytes(d[1:3], "little"), int.from_bytes(d[3:5], "little")
    if pw <= 0 or ph <= 0:
        pw, ph = 64, 64
    with _LOCK:
        _SIZE_CACHE[key] = (pw, ph, now)
    return pw, ph


def _record_session(svc: LedBleService) -> None:
    with _LOCK:
        _RECENT_FRAMES.extend(svc.parsed_frames[-50:])
        _RECENT_ACKS.extend(svc.ack_events[-50:])
        del _RECENT_FRAMES[:-250]
        del _RECENT_ACKS[:-250]


@contextmanager
def _ble_session(g: Any, *, address: str | None = None) -> Iterator[LedBleService | None]:
    """Yield a connected LedBleService for the duration of one request."""
    addr = (address or g.device_address or "").strip()
    if not addr:
        yield None
        return
    svc = LedBleService(addr, settings=g.settings)
    try:
        svc.connect()
        yield svc
    finally:
        try:
            if svc.is_connected():
                svc.disconnect()
        except Exception:
            pass
        _record_session(svc)


def _request_address(g: Any) -> str | None:
    """Address parameter: query arg, form field, or JSON 'address'."""
    addr = request.args.get("address")
    if addr:
        return addr.strip()
    if request.method == "POST":
        addr = request.form.get("address")
        if addr:
            return addr.strip()
        body = request.get_json(force=True, silent=True) or {}
        addr = body.get("address")
        if addr:
            return str(addr).strip()
    return (g.device_address or "").strip() or None


# --------------------------------------------------------------------------
# Preset image helpers
# --------------------------------------------------------------------------
def _list_preset_image_names() -> List[str]:
    if not PRESET_IMAGE_DIR.is_dir():
        return []
    names: List[str] = []
    for p in PRESET_IMAGE_DIR.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() in PRESET_IMAGE_EXTENSIONS:
            names.append(p.name)
    names.sort()
    return names


def _resolve_preset_image(name: str) -> Path | None:
    safe_name = secure_filename(name or "")
    if not safe_name:
        return None
    candidate = (PRESET_IMAGE_DIR / safe_name).resolve()
    try:
        candidate.relative_to(PRESET_IMAGE_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix.lower() not in PRESET_IMAGE_EXTENSIONS:
        return None
    return candidate


# --------------------------------------------------------------------------
# Low-level command helpers (learned from the official LOY SPACE app)
# --------------------------------------------------------------------------
# Rule: GET queries go out as frame type 0x03; SET commands as type 0x02.

_GET_TYPE = 0x03
_SET_TYPE = 0x02


def _last_tag_payload(ble: LedBleService, tag: int) -> bytes | None:
    """Return the payload of the most recent inbound 0x83 frame with this tag."""
    found: bytes | None = None
    for f in ble.parsed_frames[-30:]:
        if f.msg_type == 0x83 and len(f.payload) >= 2 and f.payload[0] == tag:
            found = f.payload
    return found


def _send_get(ble: LedBleService, settings: Settings, tag: int, payload: bytes, wait_s: float = 1.2) -> bytes | None:
    """Send a get command (type 0x03) and return the latest matching tag payload."""
    ble.send_payload(flags=settings.rt_show_flags, msg_type=_GET_TYPE, payload=payload)
    time.sleep(wait_s)
    return _last_tag_payload(ble, tag)


def _send_set(ble: LedBleService, settings: Settings, payload: bytes, wait_s: float = 1.0) -> None:
    """Send a set command (type 0x02); ack is asynchronous, so settle before reading."""
    ble.send_payload(flags=settings.rt_show_flags, msg_type=_SET_TYPE, payload=payload)
    time.sleep(wait_s)


def _send_acked_many(ble: LedBleService, settings: Settings, payloads: List[bytes], timeout_s: float = 2.0):
    """Send a list of payloads waiting for each ACK (un-paced sends drop frames)."""
    res: List[Dict[str, Any]] = []
    acked = 0
    for p in payloads:
        r = ble.send_and_wait_ack_sync(
            flags=settings.rt_show_flags, msg_type=settings.rt_show_type,
            payload=p, timeout_s=timeout_s,
        )
        res.append({"sno": r.get("sno"), "acked": bool(r.get("acked"))})
        if r.get("acked"):
            acked += 1
    return res, acked


def _pgm_play_set_payload(model: int, index: int, ids: List[int]) -> bytes:
    """pgm_play set: [54, len, model, index, (id-1)...] (mirrors app me())."""
    body = bytearray([int(model) & 0xFF, int(index) & 0xFF])
    for p in ids:
        body.append((max(1, int(p)) - 1) & 0xFF)
    return bytes([0x36]) + encode_len(len(body)) + bytes(body)


def _payload_data(fp: bytes) -> bytes:
    """Strip [tag][varint-len] from a 0x83 frame payload -> the data bytes.
    Len: plain byte < 128; 0x81 -> 2-byte len; 0x82 -> 3-byte len."""
    if len(fp) < 2:
        return b""
    n = fp[1]
    if n == 0x81:
        off = 3
    elif n == 0x82:
        off = 4
    else:
        off = 2
    return fp[off:]


def _decode_pgm_play(data: bytes) -> Dict[str, Any]:
    if len(data) < 2:
        return {"model": None, "index": None, "ids_pro": []}
    return {
        "model": data[0],
        "index": data[1],
        "ids_pro": [b + 1 for b in data[2:]],
    }


def _decode_pgm_key(data: bytes) -> List[Dict[str, Any]]:
    """pgm_key reply: 4-byte ts_update, or 21-byte rows [id_pro(1-based), 20-byte key]."""
    if len(data) == 4:
        return []
    rows: List[Dict[str, Any]] = []
    i = 0
    while i + 21 <= len(data):
        key = data[i + 1:i + 21]
        rows.append({
            "program": data[i] + 1,
            "key": key.hex(),
            "has_content": key != bytes(20),
        })
        i += 21
    return rows


def _decode_dev_info(data: bytes) -> Dict[str, Any]:
    parts = data.decode("utf-8", errors="replace").split(",")
    out: Dict[str, Any] = {"id_dev": parts[0] if parts else None}
    if len(parts) > 1:
        out["model"] = parts[1]
    if len(parts) > 2:
        out["firmware"] = parts[2]
    if len(parts) > 3:
        out["type"] = parts[3]
    if len(parts) > 4:
        out["expend"] = parts[4]
    if len(parts) > 5:
        out["cid"] = parts[5]
    if len(parts) > 6:
        out["features"] = parts[6:]
    return out


def _decode_param_dev(data: bytes) -> Dict[str, Any]:
    if len(data) >= 5:
        return {
            "width": int.from_bytes(data[1:3], "little"),
            "height": int.from_bytes(data[3:5], "little"),
        }
    return {}


def make_blueprint(*, settings: Settings | None = None) -> Blueprint:
    settings = settings or Settings.from_env()
    g = type("G", (), {"settings": settings, "device_address": settings.device_address})()
    bp = Blueprint("matrix", __name__)

    @bp.get("/api/status")
    def api_status():
        """Server/config status. Does NOT connect to the panel (see design)."""
        with _LOCK:
            last_notif = _RECENT_FRAMES[-1].raw.hex() if _RECENT_FRAMES else None
        return jsonify({
            "ok": True,
            "connection_model": "per-request (connect -> act -> disconnect)",
            "device": g.device_address or None,
            "notify_uuid": "0000FFF1-0000-1000-8000-00805F9B34FB",
            "write_uuid": "0000FFF2-0000-1000-8000-00805F9B34FB",
            "last_frame_hex": last_notif,
            "last_frames_count": len(_RECENT_FRAMES),
        })

    @bp.get("/api/discover")
    def api_discover():
        """Scan for compatible panels. ?timeout=6, ?include_all=1 to list
        everything, ?with_size=1 to connect to each and read its dimensions."""
        timeout = float(request.args.get("timeout", 6))
        include_all = request.args.get("include_all", "0") == "1"
        include_size = request.args.get("with_size", "0") == "1"
        try:
            devices = LedBleService.discover(timeout=timeout, include_all=include_all)
            if include_size:
                for d in devices:
                    d["size"] = None
                    svc = LedBleService(d["address"], settings=settings)
                    try:
                        svc.connect()
                        p = _send_get(svc, settings, 27, bytes([0x1B, 0x00]), wait_s=1.0)
                        if p:
                            dd = _payload_data(p)
                            if len(dd) >= 5:
                                d["size"] = [int.from_bytes(dd[1:3], "little"), int.from_bytes(dd[3:5], "little")]
                    except Exception:
                        pass
                    finally:
                        try:
                            svc.disconnect()
                        except Exception:
                            pass
            return jsonify({"ok": True, "count": len(devices),
                            "matched_by": {"service_uuid": LedBleService.TARGET_SERVICE_UUID,
                                           "name_prefixes": list(LedBleService.NAME_PREFIXES)},
                            "devices": devices})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.post("/api/connect")
    def api_connect():
        """Deprecated: connections are now automatic, per request. Kept so the
        old control panel doesn't 500."""
        return jsonify({"ok": True, "deprecated": True,
                        "note": "connections are automatic and per-request; "
                                "pass ?address= or body 'address' to target a device"})

    @bp.post("/api/disconnect")
    def api_disconnect():
        return jsonify({"ok": True, "deprecated": True,
                        "note": "connections are never held between requests"})

    @bp.post("/api/power")
    def api_power():
        body = request.get_json(force=True, silent=True) or {}
        on = bool(body.get("on", True))
        wait_ack = bool(body.get("wait_ack", False))
        ack_timeout_s = float(body.get("ack_timeout_s", 2.0))
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                payload = cmd_power_payload(on)
                if wait_ack:
                    res = ble.send_and_wait_ack_sync(flags=settings.rt_show_flags, msg_type=settings.rt_show_type, payload=payload, timeout_s=ack_timeout_s)
                else:
                    res = ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type, payload=payload)
                return jsonify({"ok": True, "sent": res, "payload_hex": payload.hex()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.post("/api/brightness")
    def api_brightness():
        body = request.get_json(force=True, silent=True) or {}
        mode = body.get("mode", "fixed")
        wait_ack = bool(body.get("wait_ack", False))
        ack_timeout_s = float(body.get("ack_timeout_s", 2.0))
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                if mode == "schedule":
                    payload = cmd_brightness_payload_schedule(body.get("entries", []))
                else:
                    payload = cmd_brightness_payload_fixed(body.get("value", 15), type_=body.get("type", 0))
                if wait_ack:
                    res = ble.send_and_wait_ack_sync(flags=settings.rt_show_flags, msg_type=0x04, payload=payload, timeout_s=ack_timeout_s)
                else:
                    res = ble.send_payload(flags=settings.rt_show_flags, msg_type=0x04, payload=payload)
                return jsonify({"ok": True, "sent": res, "payload_hex": payload.hex()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.post("/api/text")
    def api_text():
        body = request.get_json(force=True, silent=True) or {}
        text = str(body.get("text", ""))
        blocks = body.get("blocks") or body.get("list_text")
        if (text is None or text == "") and not blocks:
            return jsonify({"ok": False, "error": "text is required"}), 400
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                wf = body.get("w")
                hf = body.get("h")
                pw, ph = _panel_size(settings, ble, ble.address,
                                     w=int(wf) if wf else None, h=int(hf) if hf else None)
                payloads = rt_show_text_payloads(
                    settings=settings,
                    text=text,
                    id_pro=int(body.get("id_pro", 1)),
                    id_rect=int(body.get("id_rect", 1)),
                    id_item=int(body.get("id_item", 1)),
                    data_save=int(body.get("data_save", 0)),
                    w=pw,
                    h=ph,
                    font_color=(body.get("font_color") if "font_color" in body else body.get("color")),
                    bg_color=(body.get("bg_color") if "bg_color" in body else body.get("color_bg")),
                    code=int(body.get("code", 1)),
                    font=int(body.get("font", 0)),
                    size=int(body.get("size", 16)),
                    align_horizontal=body.get("align_horizontal") or body.get("alignHorizontal"),
                    align_vertical=body.get("align_vertical") or body.get("alignVertical"),
                    rotate=int(body.get("rotate", 0)),
                    space_line=int(body.get("space_line", 0)),
                    space_font=int(body.get("space_font", 0)),
                    blocks=blocks,
                    control_mode=str(body.get("control_mode", "loop")),
                    control_value=int(body.get("control_value", 0)),
                    anim_type=parse_text_animation(body.get("anim") or body.get("animation") or body.get("anim_type") or body.get("type_ani") or body.get("typeAni"), default=1),
                    anim_speed=clamp_anim_speed(body.get("anim_speed") or body.get("speed") or body.get("speed_raw") or (int(body.get("speed_ui")) if "speed_ui" in body else None) or 9, default=9),
                    anim_time_stay=int(body.get("anim_time_stay", body.get("time_stay", 3))),
                    include_anim=bool(body.get("include_anim", True)),
                    interval=int(body.get("interval", 0)),
                )
                res, _acked = _send_acked_many(ble, settings, payloads)

                play_loop = int(body.get("play_loop", 1)) if str(body.get("play_loop", "")).strip() != "" else 1
                dispatch_payload = build_dispatch_play_payload(
                    id_pro=int(body.get("id_pro", 1)),
                    play_loop=play_loop,
                    ignore_pgm_cmd=int(body.get("ignore_pgm_cmd", 0)),
                )
                ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type, payload=dispatch_payload)

                return jsonify({"ok": True, "sent_count": len(res) + 1, "sent": res + [{"sno": None, "frame_len": len(dispatch_payload), "kind": "dispatch_play"}]})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.get("/api/text/animations")
    def api_text_animations():
        animations = []
        aliases_by_id: Dict[int, List[str]] = {}
        for k, v in TEXT_ANIMATION_ALIASES.items():
            aliases_by_id.setdefault(int(v), []).append(k)
        for anim in TextAnimation:
            aid = int(anim.value)
            animations.append({
                "id": aid,
                "name": anim.name,
                "description": TEXT_ANIMATION_DESCRIPTIONS.get(aid, ""),
                "aliases": sorted(set(aliases_by_id.get(aid, []))),
            })
        animations.sort(key=lambda x: x["id"])
        return jsonify({"ok": True, "speed_range": [1, 15], "animations": animations})

    @bp.post("/api/gif")
    def api_gif():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "file is required"}), 400
        b = request.files["file"].read()
        if not (len(b) >= 3 and b[0:3] == b"GIF"):
            return jsonify({"ok": False, "error": "Not a GIF (missing GIF header)"}), 400
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                wf = request.form.get("w")
                hf = request.form.get("h")
                pw, ph = _panel_size(settings, ble, ble.address,
                                     w=int(wf) if wf else None, h=int(hf) if hf else None)
                payloads = pkts_program_gif_payloads(
                    settings=settings,
                    gif_bytes=b,
                    id_pro=int(request.form.get("id_pro", 1)),
                    id_rect=int(request.form.get("id_rect", 1)),
                    id_item=int(request.form.get("id_item", 1)),
                    data_save=int(request.form.get("data_save", 0)),
                    target_size=(pw, ph),
                )
                res, _acked = _send_acked_many(ble, settings, payloads)

                play_loop = int(request.form.get("play_loop", 65535))
                dispatch = None
                if request.form.get("play", "1") not in ("0", "false", "no", "False"):
                    dispatch = build_dispatch_play_payload(
                        id_pro=int(request.form.get("id_pro", 1)),
                        play_loop=play_loop,
                        ignore_pgm_cmd=int(request.form.get("ignore_pgm_cmd", 0)),
                    )
                    ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type, payload=dispatch)

                if dispatch is None:
                    return jsonify({"ok": True, "sent_count": len(res), "sent": res,
                                    "note": "uploaded without dispatch (play=0)"})
                return jsonify({"ok": True, "sent_count": len(res) + 1,
                                "sent": res + [{"sno": None, "frame_len": len(dispatch), "kind": "dispatch_play"}]})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.post("/api/image")
    def api_image():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "file is required"}), 400
        b = request.files["file"].read()
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                wf = request.form.get("w")
                hf = request.form.get("h")
                pw, ph = _panel_size(settings, ble, ble.address,
                                     w=int(wf) if wf else None, h=int(hf) if hf else None)
                payloads = pkts_program_image_payloads(
                    settings=settings,
                    image_bytes=b,
                    mode=str(request.form.get("mode", "gif")),
                    target_size=(pw, ph),
                    id_pro=int(request.form.get("id_pro", 1)),
                    id_rect=int(request.form.get("id_rect", 1)),
                    id_item=int(request.form.get("id_item", 1)),
                    data_save=int(request.form.get("data_save", 0)),
                )
                res, _acked = _send_acked_many(ble, settings, payloads)

                play_loop = int(request.form.get("play_loop", 65535))
                dispatch = None
                if request.form.get("play", "1") not in ("0", "false", "no", "False"):
                    dispatch = build_dispatch_play_payload(
                        id_pro=int(request.form.get("id_pro", 1)),
                        play_loop=play_loop,
                        ignore_pgm_cmd=int(request.form.get("ignore_pgm_cmd", 0)),
                    )
                    ble.send_payload(flags=settings.rt_show_flags, msg_type=settings.rt_show_type, payload=dispatch)

                if dispatch is None:
                    return jsonify({"ok": True, "sent_count": len(res), "sent": res,
                                    "note": "uploaded without dispatch (play=0)"})
                return jsonify({"ok": True, "sent_count": len(res) + 1,
                                "sent": res + [{"sno": None, "frame_len": len(dispatch), "kind": "dispatch_play"}]})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.get("/api/image/presets")
    def api_image_presets():
        return jsonify({"ok": True, "images": _list_preset_image_names()})

    @bp.post("/api/image/preset")
    def api_image_preset():
        body = request.get_json(force=True, silent=True) or {}
        name = str(body.get("name", "")).strip()
        if not name:
            return jsonify({"ok": False, "error": "name is required"}), 400
        preset_path = _resolve_preset_image(name)
        if not preset_path:
            return jsonify({"ok": False, "error": f"preset image not found: {name}"}), 404

        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                image_bytes = preset_path.read_bytes()
                payloads = pkts_program_image_payloads(
                    settings=settings,
                    image_bytes=image_bytes,
                    mode=str(body.get("mode", "gif")),
                    target_size=(int(body.get("w", 64)), int(body.get("h", 64))),
                    id_pro=int(body.get("id_pro", 1)),
                    id_rect=int(body.get("id_rect", 1)),
                    id_item=int(body.get("id_item", 1)),
                    data_save=int(body.get("data_save", 0)),
                )
                res, _acked = _send_acked_many(ble, settings, payloads)
                return jsonify({
                    "ok": True,
                    "preset": preset_path.name,
                    "sent_count": len(res),
                    "sent": res,
                })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.get("/api/play-mode")
    def api_play_mode_get():
        """Read the panel's current play state (pgm_play)."""
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                payload = _send_get(ble, settings, 54, bytes([0x36, 0x00]))
                if not payload:
                    return jsonify({"ok": False, "error": "no pgm_play reply from panel"}), 502
                return jsonify({"ok": True, "pgm_play": _decode_pgm_play(_payload_data(payload))})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.post("/api/play-mode")
    def api_play_mode_set():
        """Set play mode. Body: {mode: single|loop|list | model: int,
        index: int, ids: [program ids]} (ids defaults to [1])."""
        body = request.get_json(force=True, silent=True) or {}
        modes = {"single": 1, "loop": 0, "list": 2}
        model = body.get("model")
        if model is None:
            model = modes.get(str(body.get("mode", "")).lower().strip())
        if model is None:
            return jsonify({"ok": False, "error": "mode must be single|loop|list (or raw model)"}), 400
        index = int(body.get("index", 0))
        raw_ids = body.get("ids") or body.get("ids_pro") or [1]
        ids = [int(x) for x in raw_ids] if isinstance(raw_ids, list) else []
        if not ids:
            return jsonify({"ok": False, "error": "ids required"}), 400
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                _send_set(ble, settings, _pgm_play_set_payload(int(model), index, ids), wait_s=0.8)
                payload = _send_get(ble, settings, 54, bytes([0x36, 0x00]))
                state = _decode_pgm_play(_payload_data(payload)) if payload else None
                return jsonify({"ok": True, "requested": {"model": int(model), "index": index, "ids": ids},
                                "pgm_play": state})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.get("/api/device-info")
    def api_device_info():
        """Read device identity + panel size + power/light/rotate/play state."""
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                out: Dict[str, Any] = {"ok": True}
                p = _send_get(ble, settings, 10, bytes([0x0A, 0x00]))
                if p:
                    out["dev_info"] = _decode_dev_info(_payload_data(p))
                p = _send_get(ble, settings, 27, bytes([0x1B, 0x00]))
                if p:
                    out["param_dev"] = _decode_param_dev(_payload_data(p))
                    pd = out["param_dev"]
                    if pd.get("width") and pd.get("height"):
                        out["size"] = {"w": pd["width"], "h": pd["height"]}
                p = _send_get(ble, settings, 4, bytes([0x04, 0x00]))
                if p:
                    d = _payload_data(p)
                    out["power"] = {"type": d[0] if d else None}
                p = _send_get(ble, settings, 6, bytes([0x06, 0x00]))
                if p:
                    d = _payload_data(p)
                    out["light"] = {"type": d[0] if d else None}
                p = _send_get(ble, settings, 47, bytes([0x2F, 0x00]))
                if p:
                    d = _payload_data(p)
                    if d:
                        out["rotate"] = {"degrees": 90 * d[0]}
                p = _send_get(ble, settings, 54, bytes([0x36, 0x00]))
                if p:
                    out["pgm_play"] = _decode_pgm_play(_payload_data(p))
                return jsonify(out)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.get("/api/programs")
    def api_programs():
        """Inventory programs via pgm_key. Query slots with ?ids=1,2,3
        (default: all 60, fetched in chunks of 10)."""
        ids_param = request.args.get("ids")
        if ids_param:
            ids = [int(x) for x in ids_param.split(",") if x.strip()]
        else:
            ids = list(range(1, 61))
        if not ids:
            return jsonify({"ok": False, "error": "ids required"}), 400
        try:
            with _ble_session(g, address=_request_address(g)) as ble:
                if not ble:
                    return jsonify({"ok": False, "error": "no device address (set DEVICE_ADDRESS or pass address)"}), 400
                programs: List[Dict[str, Any]] = []
                for start in range(0, len(ids), 10):
                    chunk = ids[start:start + 10]
                    payload = bytes([0x35, len(chunk)]) + bytes(i - 1 for i in chunk)
                    p = _send_get(ble, settings, 53, payload, wait_s=1.5)
                    if p:
                        programs.extend(_decode_pgm_key(_payload_data(p)))
                return jsonify({"ok": True, "count": len(programs), "programs": programs})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/debug/acks", methods=["GET", "DELETE"])
    def api_debug_acks():
        with _LOCK:
            events = list(_RECENT_ACKS)
        if request.method == "DELETE":
            with _LOCK:
                _RECENT_ACKS.clear()
            return jsonify({"ok": True, "cleared": True})
        limit = int(request.args.get("limit", 50))
        acked_filter = request.args.get("acked_sno")
        include_payload = request.args.get("include_payload", "1") == "1"
        events = events[-limit:]
        if acked_filter is not None:
            try:
                target = int(acked_filter)
                events = [e for e in events if e.acked_sno == target]
            except ValueError:
                return jsonify({"ok": False, "error": "acked_sno must be int"}), 400
        result = []
        for ev in events:
            d = ack_to_dict(ev)
            if not include_payload:
                d.pop("payload_hex", None)
            result.append(d)
        return jsonify({"ok": True, "count": len(result), "acks": result})

    @bp.route("/api/debug/frames", methods=["GET", "DELETE"])
    def api_debug_frames():
        with _LOCK:
            frames = list(_RECENT_FRAMES)
        if request.method == "DELETE":
            with _LOCK:
                _RECENT_FRAMES.clear()
            return jsonify({"ok": True, "cleared": True})
        limit = int(request.args.get("limit", 50))
        type_filter = request.args.get("type")
        sno_filter = request.args.get("sno")
        checksum_ok_filter = request.args.get("checksum_ok")
        include_payload = request.args.get("include_payload", "1") == "1"
        include_raw = request.args.get("include_raw", "0") == "1"
        frames = frames[-limit:] if limit > 0 else frames

        if type_filter is not None:
            try:
                t = int(type_filter)
                frames = [f for f in frames if f.msg_type == t]
            except ValueError:
                return jsonify({"ok": False, "error": "type must be int"}), 400
        if sno_filter is not None:
            try:
                s = int(sno_filter)
                frames = [f for f in frames if f.sno == s]
            except ValueError:
                return jsonify({"ok": False, "error": "sno must be int"}), 400
        if checksum_ok_filter is not None:
            if checksum_ok_filter not in ("0", "1"):
                return jsonify({"ok": False, "error": "checksum_ok must be 0 or 1"}), 400
            want = checksum_ok_filter == "1"
            frames = [f for f in frames if f.checksum_present and f.checksum_ok == want]

        out = []
        for f in frames:
            d: Dict[str, Any] = {
                "recv_ts": f.recv_ts,
                "sno": f.sno,
                "flags": f.flags,
                "msg_type": f.msg_type,
                "checksum_present": f.checksum_present,
                "checksum_ok": f.checksum_ok,
                "payload_len": len(f.payload),
                "raw_len": len(f.raw),
            }
            if include_payload:
                d["payload_hex"] = f.payload.hex()
            if include_raw:
                d["raw_hex"] = f.raw.hex()
            out.append(d)
        return jsonify({"ok": True, "count": len(out), "frames": out})

    @bp.get("/")
    def control_panel():
        return render_template("control_panel.html")

    return bp


# Backwards-compatible alias: make_blueprint(ble=..., settings=...) still works.
def make_blueprint_legacy(*, ble=None, settings=None) -> Blueprint:
    return make_blueprint(settings=settings)