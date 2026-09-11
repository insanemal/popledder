# Protocol & Research Notes (LED Matrix)

Everything learned while getting the 64x64 panel working. Pick up here when
revisting any of the open threads. Companion docs: top-level `README.md`
(quickstart) and `docs/server-and-api.md` (Flask server/API reference).

---

## 1. BLE connection model

- `LedBleService(address, settings)` runs its own asyncio loop in a daemon
  thread; synchronous callers use `run_coro_sync(coro)`.
- UUIDs (from `config.py`): service `0000FFF0-…`, notify `0000FFF1-…`,
  write `0000FFF2-…` (classic TP/fw LED controller profile).
- Frame layout (see `led_matrix_api/protocol/frame.py`):

  ```
  AA55 FFFF LEN(2) SNO(2) FLAGS(1) TYPE(1) PAYLOAD [CHK16 if FLAGS & 0x80]
  total_len = len_field + 6
  ```

- We send with flags `0xC1` (checksum on) and type `0x02` (`rt_show`).
- The panel ACKs with types `0x82 / 0x83 / 0x84` — each just echoes our `sno`.
  **There is no inbound "current program / status" message.**

## 2. Payload command bytes (observed)

| Cmd | Byte | Notes |
|---|---|---|
| Power | `0x04` | `[0x04,len,0x00,state]`, state 1=on |
| Brightness | `0x06` | fixed: `value` 1–15; or schedule entries |
| Delete programs | `0x08` | **REMOVED from current code** — see §8 |
| Dispatch/play | `0x18` | `[0x18,6,2,pro_idx,ignore_pgm_cmd,play_mode,loop u16]` |
| Text header/stream | tags `0x17`, `0x12`, `0x13` | see `protocol_builders.py` |

`build_dispatch_play_payload(id_pro, play_loop=65535)` → play program forever.
Note: `play_loop=0` is clamped to 1 (plays once).

## 3. Program slots

- Hierarchy: **program** (`id_pro`) → **rect** (`id_rect`) → **item**
  (`id_item`). We always use 1/1/1.
- Slots are addressed 1-based; wire bytes are `id-1`.
- **60-slot play queue, not occupancy:** `get: pgm_play` (frame type `0x03`,
  payload `[54,0]`) → `{model, index, ids_pro}` = the panel's full 60-slot
  play queue + active position. Panel also pushes uploads (type `0x06`/tag 54)
  when playback changes.
- **Occupancy is per-slot via `pgm_key`:** non-zero 20-byte key = content,
  all-zero = empty. Our panel: only **slot 1** has content (the GIF); 2..60
  are empty.
- Writing new content into a slot that's playing immediately blanks it
  (incomplete program) until the stream finishes — this is why the old display
  "disappears on connect".
- **Why old content "reverted":** the panel cycles its default 60-slot queue
  and empty slots show attract/demo, so anything in other slots (or the demo)
  comes back unless you `dispatch` to pin one program. Use `delete`
  (del_all `[8,2,0,255]`) before upload, or upload to a fresh slot +
  `dispatch`/`pgm_play` it.

Full wire details: `docs/official-app-protocol.md`.

### Open idea (slot strategy — not yet built)
Now that we know `pgm_play` returns the full queue but `pgm_key` tells real
occupancy, the clean approaches are: (a) `delete del_all` before uploading,
then stream + dispatch — the panel then only has your program; or (b) upload
to a fresh slot and `dispatch`/`pgm_play` it, optionally restoring the previous
program after (we can read its id via `get: pgm_play`). Caveat from old dev
notes: stale programs in other slots cycle back, which the delete command
solves.

## 4. The ticker (meeting_status.py)

- **Problem:** the panel's text renderer wraps at 64px wide. At font size ~16–
  20 that's 4 chars/line ⇒ "I am in a meeting" wraps to 3+(1) lines. The API
  cannot render a single-line marquee.
- **Solution:** render text to an animated GIF strip and let the panel play it:
  - Strip = two copies of the text back-to-back for a seamless loop.
  - `loop_len = textwidth + gap`; strip width = `loop_len + 64`; each frame is a
    64x64 crop shifted left `step` px; `step`/`frame_ms` sets scroll speed.
  - Defaults: `size 28` (≈20px-tall letters in Noto Sans Bold, 4–5 chars
    visible), `step 2`, `frame_ms 50` (~40px/s), color `0xFFFF00`.
  - Font: `/usr/share/fonts/noto/NotoSans-Bold.ttf` (fallback list in script).
  - Colors are decimal `0xRRGGBB` ints (e.g. white = `16777215`).
- **Key lesson:** noto "size 20" ≠ 20px letters (cap height ≈ 0.715 em). At
  size 20 letters were ~10px wide (too small). 28 matches the panel's own
  ~16px-char rendering.
- GIFs are encoded like the panel's own builder: palette 256, `loop=0`,
  duration ms per frame, `disposal=2`, no optimize.

## 5. show_gif.py

Same reliable path, but takes any image: `pkts_program_image_payloads(mode="gif")`
normalizes frames to 64x64 (NEAREST), preserves animation. Verified offline with
200x100 / 640x480 / 64x64 sources.

## 6. Reliability rules (learned empirically)

1. **ACK-pace every payload** (`send_and_wait_ack_sync`, ~2s timeout). Report
   acked/missed. 189/189 acked = panel has the program. Un-paced streams show
   nothing.
2. **Do not send a power-on command by default** — disabled via `--power-on`
   flag; the known-good sends never issued it, and it was suspect when the
   display failed. (Not conclusively proven guilty — just avoided.)
3. Disconnect cleanly at the end (`ble.disconnect()`) — leaving the link open
   hangs the adapter/panel ("things get all hung up").
4. `--settle 1` (default) hold between dispatch and disconnect is fine.

## 7. Flask server — known problems (the "server mystery")

Server path works via `run.py` / `start_matrix_controller.sh` (port 5000), and
`/api/text` worked reliably early on. But:

- `/api/image` showed content while the server held an open BLE connection, then
  **responded HTTP 500 after displaying** — exception raised post-send, body
  never surfaced to the old client (it ignored the error body). Root cause never
  found; abandoned in favor of direct BLE.
- Server **never disconnects** the BLE link when the process exits ⇒ adapter and
  panel get stuck; required service restarts.
- The old meeting-status app (first version, `/api/text`) worked but wrapped.

Endpoints (from `routes.py`): `/api/status`, `/api/connect`, `/api/disconnect`,
`/api/power`, `/api/brightness` (fixed/schedule), `/api/text`,
`/api/text/animations`, `/api/gif`, `/api/image`, `/api/image/presets`,
`/api/image/preset`, `/api/debug/acks`, `/api/debug/frames`, `/` (control panel).

**If we fix the server later:** add ACK pacing to the image/gif paths, send
`dispatch_play` (already added to image/gif), disconnect on shutdown, and
re-read API error bodies client-side.

## 8. Removed features in git history (candidates to resurrect)

Developer: Shane Connelly. Refactors (`6416b3e`, `d66cf6b`) stripped testing
code. Removed:

- `build_delete_programs_payload(del_ids)` / `cmd_delete_programs` + `/api/clear`
  route. Purpose (from commit message): *"Delete programs from the panel. Use
  before sending new content to avoid old pages reverting."* Delete payload:
  `[0x08, len, 0x00 subtype, (id-1) …]`.
- `build_bmp_from_image` + `bmp` mode in image payloads. Comment: *"the original
  app sends BMP for graphics, not YSTP01 or GIF"* — BMP is the panel's native
  graphic format.
- `rt_show_image_payloads` with `gif | palette | bmp | rgb24` modes.

The control panel HTML still calls `/api/clear` (dead — 404s).

## 9. Text animations

~30 entrance animations (see `models/animations.py`): appear, scroll in (4
directions), wipes (with/without lines), rain, elastic, laser, flashing (+invert).
Accept names, aliases, or IDs. `speed_range [1,15]`, `anim_time_stay` ~seconds.
`control_mode "loop"` + scroll anim ≈ repeating marquee (though the API still
wraps long text — use the GIF ticker instead).

## 10. Environment / run

```bash
export DEVICE_ADDRESS=FF:25:12:09:30:DC
export BLE_WRITE_CHUNK=180      # proven reliable
export STREAM_CHUNK=960
python3 cli_tools/meeting_status.py [--text "..."] [--frame-ms 50] [--color 0xRRGGBB]
python3 cli_tools/show_gif.py --file your.gif [--brightness 12] [--settle 1]
```

Server: `./start_matrix_controller.sh` (or `python3 run.py` with env). Panel
pages: `http://127.0.0.1:5000/` (control panel), status at `/api/status`.