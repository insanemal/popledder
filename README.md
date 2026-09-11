# LED Matrix (popledder)

Control a 64x64 LED matrix panel over Bluetooth Low Energy (BLE).

**Fork layout:** this repo is a fork. `origin` = your fork
(https://github.com/insanemal/popledder.git), `upstream` = the original
(https://github.com/eskibars/popledder.git). Make branches + PRs back to
upstream. The unpacked companion APK under `APK/` is git-ignored — never
committed.

## Two ways to drive the panel

| Method | Folder | When to use |
|---|---|---|
| **Web API** (`api_tools/`) | Python scripts that talk HTTP to the Flask server | normal use — no BLE contention |
| **Direct BLE** (`cli_tools/`) | Python scripts that open BLE themselves | debugging when the server is stopped |

⚠️ Don't run `cli_tools/` while the server is up — two BLE clients on one panel
collide. The server is the preferred owner.

```bash
# API tools (server must be running: ./start_matrix_controller.sh)
python3 api_tools/meeting_status.py                     # scrolling "I am in a meeting"
python3 api_tools/show_gif.py --file foo.gif            # any GIF -> 64x64, loops forever
python3 api_tools/panel_probe.py                        # device info + play state
python3 api_tools/set_play_mode.py --mode single --index 0 --ids 1

# Direct BLE tools (server stopped!)
python3 cli_tools/meeting_status.py
python3 cli_tools/show_gif.py --file cli_tools/samples/minecraft.gif
python3 cli_tools/panel_probe.py
python3 cli_tools/set_play_mode.py --get
```

## API design (important)

- The server does **NOT hold BLE connections** — a held link wedges the OS BLE
  stack and blinds everything (server + CLI tools). Every endpoint runs
  **connect → act → disconnect**.
- Target a specific panel with `?address=` (GET) or body/form `address`
  (POST); defaults to `DEVICE_ADDRESS`.
- `GET /api/discover` — scan for panels (FFF0 service UUID or `YS`/`TL` name).
- There is deliberately **no connect/disconnect endpoint** (legacy no-ops kept
  for the old control panel).

## Layout

| Path | What |
|---|---|
| `led_matrix_api/ble/service.py` | `LedBleService` (client + `discover()`), ack tracking |
| `led_matrix_api/protocol/` | frame build/parse (`AA55 FFFF …`), encoding, checksums |
| `led_matrix_api/commands/` | payload builders (rt_show, program/image/GIF, power, brightness) |
| `led_matrix_api/models/animations.py` | all text entrance animations + aliases |
| `led_matrix_api/api/` | Flask app + routes (per-request BLE) |
| `led_matrix_api/static/presets/` | bundled weather images |
| `cli_tools/` | direct-BLE scripts (+ `samples/` gifs) |
| `api_tools/` | HTTP-api equivalents of the CLI tools |

## API endpoint cheat-sheet

- `GET /api/discover` · `GET /api/status` (server config, no BLE)
- `POST /api/power` · `POST /api/brightness` (fixed/schedule)
- `POST /api/text` (multi-line blocks, animations) · `GET /api/text/animations`
- `POST /api/image` / `POST /api/gif` (multipart `file`, ACK-paced, plays after)
- `GET /api/image/presets` · `POST /api/image/preset`
- `GET/POST /api/play-mode` — read/set pgm_play (`mode: single|loop|list`,
  `index`, `ids`)
- `GET /api/device-info` — dev_info, param_dev, power/light/rotate, pgm_play
- `GET /api/programs` — per-slot keys (`has_content`)
- `GET/DELETE /api/debug/acks` · `GET/DELETE /api/debug/frames` (recent sessions)

## Requirements

`flask`, `bleak`, `pillow` (see `requirements.txt`). Python 3.10+.

Panel BLE address: `FF:25:12:09:30:DC` (env `DEVICE_ADDRESS`).
Write chunk that works well: **180 bytes** (env `BLE_WRITE_CHUNK`).

## Key facts learned the hard way (READ THESE)

1. **Ack-pacing is mandatory.** Un-paced sends silently drop frames and the
   panel shows nothing. All sends wait for each ACK.
2. **No held BLE connections.** See API design above. This bit us repeatedly.
3. **The panel answers "what's playing":** `get: pgm_play` → `{model, index,
   ids_pro}`. `model` = play mode: 0 loop (full queue), 1 single, 2 custom list.
4. **60-slot play queue by default; occupancy via `pgm_key`** — non-zero key =
   content. Our panel has content only in slot 1.
5. **Long text wraps** at 64px — that's why the ticker renders a GIF instead.
6. **Frame types matter:** gets = type `0x03`, sets = type `0x02`.

See `docs/protocol-and-research.md` (deep dive), `docs/official-app-protocol.md`
(full wire decode from the official LOY SPACE app), `docs/server-and-api.md`
(endpoint details + known issues).