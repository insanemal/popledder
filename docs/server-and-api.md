# Server & API Reference (Flask path)

The HTTP API (`api_tools/` scripts, run against the Flask server on port 5000)
is the **recommended** way to drive the panel. Direct-BLE scripts live in
`cli_tools/` for debugging with the server stopped. This doc covers the API.

## Startup

```bash
./start_matrix_controller.sh   # exports DEVICE_ADDRESS + BLE_WRITE_CHUNK=180
# or
export DEVICE_ADDRESS=FF:25:12:09:30:DC
python3 run.py                 # Flask on 0.0.0.0:5000 (debug on by default)
```

Config via env: `DEVICE_ADDRESS`, `BLE_WRITE_CHUNK` (248 default; 180 proven),
`STREAM_CHUNK` (960), `HOST`, `PORT`, `DEBUG`. See `led_matrix_api/config.py`.

Every JSON response is `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

## Connection model (IMPORTANT)

The server **never holds a BLE connection**. A held link wedges the OS BLE
stack (panel becomes unreachable by the server AND the CLI tools — this broke
us repeatedly). Every BLE-touching endpoint:

1. resolves an address (`?address=` / body `address` / `DEVICE_ADDRESS`),
2. connects,
3. performs the action,
4. **disconnects**, always.

`/api/connect` and `/api/disconnect` are deprecated no-ops (kept only so the
old control panel doesn't 500). `GET /api/status` is server config only (no
BLE). Use `GET /api/discover` to find panels.

## Endpoints

| Method & path | Purpose | Key fields |
|---|---|---|
| `GET /api/discover` | scan for panels (FFF0 UUID or YS/TL name) | `timeout`, `include_all` |
| `GET /api/status` | server config + recent-frame info (no BLE) | — |
| `POST /api/connect` | connect BLE | — |
| `POST /api/disconnect` | disconnect BLE | — |
| `POST /api/power` | `{"on": true/false}` | |
| `POST /api/brightness` | `{"mode":"fixed","value":1..15,"type":0}` or `{"mode":"schedule","entries":[{"value":N,"time":"HH:MM"}]}` | |
| `POST /api/text` | render text | `text` or `blocks`/`list_text`; `size`, `code` (1=GBK, ≥16=UTF-8), `font`, `font_color` (decimal 0xRRGGBB), `bg_color`, `align_horizontal/vertical`, `rotate`, `space_line/font`, `anim` (name/id), `anim_speed` 1–15, `anim_time_stay`, `control_mode`, `control_value`, `play_loop`, `include_anim`, `interval`, `clear_before_send` (ignored) |
| `GET /api/text/animations` | all animations + `speed_range` | — |
| `POST /api/gif` | upload GIF (multipart `file`) | `w`,`h`,`msg_type`,`id_pro`,`data_save` |
| `POST /api/image` | upload image (multipart `file`) | `mode` (`gif\|rgb24\|palette`), `w`,`h`, `clear_before_send` (ignored) |
| `GET /api/image/presets` | list `static/presets/` files | — |
| `POST /api/image/preset` | send bundled preset | `{"name": "...", "mode":"gif", "w":64, "h":64}` |
| `GET/DELETE /api/debug/acks` | ACK events (recent sessions) | `limit`, `acked_sno`, `include_payload` |
| `GET/DELETE /api/debug/frames` | parsed inbound frames (recent sessions) | `limit`, `type`, `sno`, `checksum_ok`, `include_payload`, `include_raw` |
| `GET /api/play-mode` | current pgm_play (`model`, `index`, `ids_pro`) | — |
| `POST /api/play-mode` | set play mode | `mode: single\|loop\|list` (or `model`), `index`, `ids` |
| `GET /api/device-info` | dev_info + size + power/light/rotate + play | — |
| `GET /api/programs` | per-slot keys / occupancy | `ids` (default all 60) |
| `GET /` | web control panel | — |

Example (the original meeting-status call that worked for single-line-ish text):

```bash
curl -s -X POST http://127.0.0.1:5000/api/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"I am in a meeting","size":16,"font_color":16777215,"align_horizontal":"center","anim":"scroll_left","anim_speed":9,"clear_before_send":true}'
```

## Known issues (revisit later)

1. **No held connections** — solved; every request connects/acts/disconnects.
2. **ACK pacing** — now applied to text/image/gif sends.
3. **`/api/clear` still missing** — control panel "Clear Panel" 404s (route +
   `cmd_delete_programs` were removed in a refactor; see git history).
4. Color values must be **decimal** `0xRRGGBB` integers.
5. Debug endpoints show the **recent-session ring buffer** (per-request
   services leave no shared state).
6. The old control panel (`/`) still calls `/api/connect`/`/api/disconnect`
   (deprecated no-ops) and does its own BLE-free "connected" display.

## Control panel

Single page app at `/` (`led_matrix_api/templates/control_panel.html` +
`static/css/control_panel.css`). It's the most complete reference for valid
request shapes (blocks/lists, animations, image form fields). Contains broken
`/api/clear` wiring; also still sends `clear_before_send` (ignored).