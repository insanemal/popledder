# popledder — LED Matrix panel controller

Control a **64×64 RGB LED matrix panel over Bluetooth Low Energy (BLE)** — send
text tickers, GIFs, images, brightness, power, and play-mode control, all from
Python. Ships with two ways to drive the panel, a probing toolkit, and a full
wire-protocol reference reverse-engineered from the vendor's own app.

---

## Quickstart (first display in ~1 minute)

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Start the API server (uses DEVICE_ADDRESS from start_matrix_controller.sh)
./start_matrix_controller.sh

# 3. Find your panel (need the radio on + panel powered)
curl "http://127.0.0.1:5000/api/discover"

# 4. Say hello — scroll a ticker
python3 api_tools/meeting_status.py --text "HELLO"

# 5. Or show a GIF (scaled to 64x64, loops forever)
python3 api_tools/show_gif.py --file your.gif
```

If discovery is empty: make sure the panel is powered and in BLE range, and that
no other app/process is holding a connection to it (see Troubleshooting).

---

## Two ways to drive the panel

| | `api_tools/` (HTTP API) | `cli_tools/` (direct BLE) |
|---|---|---|
| What | talks to the Flask server (`:5000`) | opens BLE itself |
| Used for | normal, scripted, or multi-panel use | raw debugging, protocol experiments |
| Needs | server running | server **stopped** |

> ⚠️ **Never run `cli_tools/` while the server is up.** Two clients can't share
> one panel — and the server is the recommended BLE owner. See Troubleshooting.

### API tools

```bash
python3 api_tools/meeting_status.py [--text "I am in a meeting"] [--color 0xFFFF00]
python3 api_tools/show_gif.py --file dance.gif [--brightness 12]
python3 api_tools/panel_probe.py                  # device info + play state
python3 api_tools/set_play_mode.py --get
python3 api_tools/set_play_mode.py --mode single --index 0 --ids 1
python3 api_tools/set_play_mode.py --mode loop    # cycles the whole 60-slot queue
python3 api_tools/clock_app.py                    # simple clock (API demo)
```

### CLI tools (server stopped)

```bash
python3 cli_tools/meeting_status.py
python3 cli_tools/show_gif.py --file your.gif
python3 cli_tools/panel_probe.py --gets pgm_key=all
python3 cli_tools/set_play_mode.py --get
```

---

## HTTP API

Base `http://127.0.0.1:5000`. Every response is `{"ok": true...}` or
`{"ok": false, "error": ...}`.

### Connection model (read this)

The server **never holds a BLE connection**. A held link wedges the OS BLE
stack and blinds everything — the server *and* the CLI tools. Every BLE-touching
endpoint instead does **connect → act → disconnect**. There is deliberately no
"connect" endpoint; to target a specific panel pass `?address=` (GET) or
`address` in the JSON/form body (POST). Defaults to `DEVICE_ADDRESS`.

### Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/discover` | scan for panels (FFF0 service UUID or `YS`/`TL` name). `?timeout=6` |
| `GET /api/status` | server config + recent-frame info (no BLE) |
| `POST /api/power` | `{"on": true}` (or `false`) |
| `POST /api/brightness` | `{"mode":"fixed","value":1..15}` or `{"mode":"schedule","entries":[{"value":N,"time":"HH:MM"}]}` |
| `POST /api/text` | text (or `blocks`/`list_text`); `size`, `font_color` (decimal 0xRRGGBB), `anim`, `anim_speed`, `play_loop`, … |
| `GET /api/text/animations` | all entrance animations + aliases |
| `POST /api/image` | multipart `file`; `mode`=`gif\|rgb24\|palette`, `w`, `h`. ACK-paced, plays after |
| `POST /api/gif` | multipart `file` (GIF only) |
| `GET /api/image/presets` · `POST /api/image/preset` | bundled weather images |
| `GET /api/play-mode` | current `{model, index, ids_pro}` |
| `POST /api/play-mode` | `{"mode":"single\|loop\|list","index":N,"ids":[1]}` |
| `GET /api/device-info` | dev_info, panel size, power/light/rotate, play state |
| `GET /api/programs` | per-slot keys + `has_content` (occupancy) |
| `GET/DELETE /api/debug/acks` · `GET/DELETE /api/debug/frames` | recent-session diagnostic ring buffer |

Examples:

```bash
curl -X POST http://127.0.0.1:5000/api/power -H 'Content-Type: application/json' -d '{"on": true}'
curl -X POST http://127.0.0.1:5000/api/brightness -H 'Content-Type: application/json' -d '{"mode":"fixed","value":10}'
curl -X POST http://127.0.0.1:5000/api/text -H 'Content-Type: application/json' \
     -d '{"text":"hi","size":16,"font_color":16777215,"anim":"scroll_left"}'
curl -X POST http://127.0.0.1:5000/api/play-mode -H 'Content-Type: application/json' \
     -d '{"mode":"single","index":0,"ids":[1]}'
curl http://127.0.0.1:5000/api/device-info
```

---

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `DEVICE_ADDRESS` | *(empty)* | panel MAC, required for API/CLI when no `--address` |
| `BLE_WRITE_CHUNK` | `180` | bytes per GATT write (180 proven reliable) |
| `STREAM_CHUNK` | `960` | bytes per program TLV stream packet |
| `HOST` / `PORT` | `0.0.0.0` / `5000` | Flask bind |
| `DEBUG` | `1` | Flask debug/reloader |

`start_matrix_controller.sh` sets `DEVICE_ADDRESS=FF:25:12:09:30:DC` and
`BLE_WRITE_CHUNK=180` for the dev panel.

---

## Project layout

```
led_matrix_api/
  ble/service.py        LedBleService (client + discover()), ACK tracking
  protocol/             frame build/parse (AA55 FFFF …), encoding, checksums
  commands/             payload builders (rt_show, program/image/GIF, power…)
  models/animations.py  text entrance animations + aliases
  api/                  Flask app + routes (per-request BLE)
  static/presets/       bundled weather images
cli_tools/              direct-BLE scripts                (server must be stopped)
api_tools/              HTTP-API mirrors + clock_app       (server must be running)
docs/                   research + API + protocol references
```

---

## Troubleshooting

**"Device … was not found"** — the panel isn't advertising to *this* machine:
power it on, bring it in BLE range, and make sure nothing else holds a
connection to it.

**It worked, now nothing is found (the BLE wedge).** If any process was killed
while connected (server or CLI tool), the OS BLE stack can keep thinking the
link is alive and the panel becomes invisible. Recovery: disconnect/forget the
device on this machine (or restart Bluetooth / reboot), then re-run. This is why
the server now uses per-request connect/disconnect — never kill it mid-request
either.

**Content "reverts" / attract-mode loops** — the panel plays its 60-slot default
queue, and empty slots show the built-in demo. Pin one program with
`dispatch` (done automatically after image/text sends) or set
`/api/play-mode` to `single` / `list` with your program id.

**GIF shows nothing** — make sure it's actually animated-GIF (a resized single
image also works), ≤ reasonable size (a few MB), and the upload reports
`acked` ≈ `sent_count`. Un-acked sends mean a flaky link, not a bad file.

---

## Development

- Remotes: `origin` = your fork (`git@github.com:insanemal/popledder.git`),
  `upstream` = original (`https://github.com/eskibars/popledder.git`).
- Work on a branch, push, then open PRs to `upstream/main`:

```bash
git checkout -b my-feature
git push -u origin my-feature          # then PR on GitHub
git fetch upstream && git rebase upstream/main
```

- Never commit the unpacked `APK/` (git-ignored) or binaries you can't license.
- Tests/experiments against a real panel are interactive — see `docs/`.

## Docs

- `docs/protocol-and-research.md` — the protocol + reliability rules, deep dive.
- `docs/official-app-protocol.md` — full wire decode from the vendor's LOY SPACE
  app (get/set commands, ACK tags, play modes, frame types).
- `docs/server-and-api.md` — endpoint reference + known issues.

## Credits

Original project by Shane Connelly (eskibars); this fork extends it with the
protocol decode of the official **LOY SPACE** Android app, the direct-BLE tools,
and the per-request API model.